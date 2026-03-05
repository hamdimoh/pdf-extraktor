import os
import streamlit as st

# 1. WICHTIG: Das MUSS der allererste Streamlit-Befehl sein!
st.set_page_config(page_title="PDF Extraktor", page_icon="⚡", layout="wide")

os.environ["TOKENIZERS_PARALLELISM"] = "false"
import tempfile
import json
import pandas as pd
import time
import re
from dotenv import load_dotenv

# --- IMPORTS FÜR BLITZSCHNELLES LOKALES OCR ---
from pdf2image import convert_from_bytes
import pytesseract

# --- IMPORTS FÜR KI-EXTRAKTION ---
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_mistralai import ChatMistralAI
from pyproj import Transformer

# ---------------- CONFIG & API KEY SICHERN ----------------
load_dotenv()

# Streamlit Cloud sucht den Key in den Secrets
try:
    if "MISTRAL_API_KEY" in st.secrets:
        os.environ["MISTRAL_API_KEY"] = st.secrets["MISTRAL_API_KEY"]
except:
    pass

if not os.getenv("MISTRAL_API_KEY"):
    st.error("⚠️ MISTRAL_API_KEY fehlt in den Secrets oder der .env Datei!")
    st.stop()

# ---------------- 1. BLITZSCHNELLES LOKALES OCR (TESSERACT) ----------------
def read_pdfs_tesseract(files):
    full_text = ""
    progress_bar = st.progress(0)
    status_text = st.empty()
    total_files = len(files)

    for i, f in enumerate(files):
        start_time = time.time()
        status_text.text(f"Lese Datei {i+1}/{total_files}: {f.name} (Tesseract OCR)...")
        
        pdf_bytes = f.getvalue()
        images = convert_from_bytes(pdf_bytes, dpi=200)
        
        doc_text = ""
        for page_num, img in enumerate(images):
            try:
                text = pytesseract.image_to_string(img, lang='deu')
            except:
                text = pytesseract.image_to_string(img, lang='eng')
                
            doc_text += f"\n\n--- SEITE {page_num + 1} ---\n{text}\n"
            
        full_text += f"\n\n--- DOKUMENT START: {f.name} ---\n{doc_text}\n--- DOKUMENT ENDE ---\n"
        
        duration = round(time.time() - start_time, 1)
        status_text.success(f"⚡ {f.name} fertig in {duration} Sekunden!")
        progress_bar.progress((i + 1) / total_files)

    status_text.empty()
    progress_bar.empty()
    return full_text

# ---------------- HELPER: KOORDINATEN UMRECHNEN (DMS -> UTM) ----------------
def dms_string_to_decimal(dms_str):
    try:
        dms_str = dms_str.replace(",", ".")
        numbers = re.findall(r"(\d+)[°\s]+(\d+)['\s]+([\d\.]+)", dms_str)
        if numbers:
            d, m, s = map(float, numbers[0])
            return d + m/60 + s/3600
        return None
    except:
        return None

def post_process_coordinates(data):
    meta = data.get("1_MetaData_Allgemein", {})
    raw_coords = meta.get("_Geografische_Koordinaten_Text", "")
    
    if raw_coords and (not meta.get("UTM 32 Koordinaten (Rechtswert/E)") or meta.get("UTM 32 Koordinaten (Rechtswert/E)") == ""):
        lat_match = re.search(r"(N|Nord).*?(\d+°.*?[\"'])", raw_coords, re.IGNORECASE)
        lon_match = re.search(r"(E|O|Ost).*?(\d+°.*?[\"'])", raw_coords, re.IGNORECASE)
        
        if not lat_match:
             matches = re.findall(r"(\d+°\d+'[\d\.]+\")", raw_coords)
             if len(matches) >= 2:
                 lat_str = matches[0]
                 lon_str = matches[1]
             else:
                 return data
        else:
            lat_str = lat_match.group(2)
            lon_str = lon_match.group(2)

        lat_dd = dms_string_to_decimal(lat_str)
        lon_dd = dms_string_to_decimal(lon_str)

        if lat_dd and lon_dd:
            transformer32 = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
            e32, n32 = transformer32.transform(lon_dd, lat_dd) 
            
            transformer33 = Transformer.from_crs("EPSG:4326", "EPSG:25833", always_xy=True)
            e33, n33 = transformer33.transform(lon_dd, lat_dd)
            
            meta["UTM 32 Koordinaten (Rechtswert/E)"] = str(round(e32))
            meta["UTM 32 Koordinaten (Hochwert/N)"] = str(round(n32))
            meta["UTM 33 Koordinaten (Rechtswert/E)"] = str(round(e33))
            meta["UTM 33 Koordinaten (Hochwert/N)"] = str(round(n33))
            
            del meta["_Geografische_Koordinaten_Text"]
            data["1_MetaData_Allgemein"] = meta
            
    return data

# ---------------- HELPER: ALLE DATEN IN JEDE WEA KOPIEREN & FLÄCHEN TEILEN ----------------
def restructure_and_calculate_data(data):
    wea_list = data.get("2_WEA_Details", [])
    num_weas = len(wea_list)
    
    if num_weas > 0:
        meta_global = data.get("1_MetaData_Allgemein", {})
        flaechen_global = data.get("3_Flaechen", {})
        
        # Mathe-Funktion zum Teilen der Flächen (rechnet z.B. 2436 / 5)
        def divide_val(val_str, divisor):
            if not val_str or str(val_str).strip() == "": return ""
            v = str(val_str).strip()
            if "." in v and "," in v:
                v = v.replace(".", "").replace(",", ".")
            elif "," in v:
                v = v.replace(",", ".")
            elif "." in v:
                parts = v.split(".")
                if len(parts[-1]) == 3: 
                    v = v.replace(".", "")
            match = re.search(r"[-+]?\d*\.\d+|\d+", v)
            if match:
                try:
                    num = float(match.group())
                    divided = round(num / divisor, 2) # Auf 2 Kommastellen runden
                    return str(divided).replace(".", ",") 
                except:
                    pass
            return val_str

        new_wea_list = []
        
        for wea_technik in wea_list:
            wea_flaechen = dict(flaechen_global) 
            
            # Hier teilt Python die gefundenen Summen automatisch durch die Anlagenzahl!
            wea_flaechen["Fläche Mast ($m^2$)"] = divide_val(wea_flaechen.get("Fläche Mast ($m^2$)", ""), num_weas)
            wea_flaechen["Fläche Mast ($ha$)"] = divide_val(wea_flaechen.get("Fläche Mast ($ha$)", ""), num_weas)
            
            wea_flaechen["Fläche Zuwegung ($m^2$)"] = divide_val(wea_flaechen.get("Fläche Zuwegung ($m^2$)", ""), num_weas)
            wea_flaechen["Fläche Zuwegung ($ha$)"] = divide_val(wea_flaechen.get("Fläche Zuwegung ($ha$)", ""), num_weas)
            
            wea_flaechen["Fläche Kran ($m^2$)"] = divide_val(wea_flaechen.get("Fläche Kran ($m^2$)", ""), num_weas)
            wea_flaechen["Fläche Kran ($ha$)"] = divide_val(wea_flaechen.get("Fläche Kran ($ha$)", ""), num_weas)
            
            wea_komplett = {
                "1_MetaData_Allgemein": dict(meta_global),
                "2_Technik_Standort": wea_technik,
                "3_Flaechen_und_Abstaende": wea_flaechen
            }
            new_wea_list.append(wea_komplett)
            
        data["2_WEA_Details"] = new_wea_list
        data.pop("1_MetaData_Allgemein", None)
        data.pop("3_Flaechen", None)
        
    return data

# ---------------- 2. EXTRAKTION (2-PHASEN ARCHITEKTUR) ----------------
def extract_all_data(text):
    status_text = st.empty()
    
    # --- KONTEXT FÜR CALL 1 (ALLGEMEIN) ---
    context_window_main = text[:8000] + "\n\n... [TEXT ÜBERSPRUNGEN] ...\n\n"

    coord_matches = [m.start() for m in re.finditer(r'(?i)rechtswert|hochwert|utm-koordinaten', text)]
    for idx in coord_matches[:6]: 
        context_window_main += text[max(0, idx - 800):min(len(text), idx + 800)] + "\n...\n"

    nature_matches = [m.start() for m in re.finditer(r'(?i)heilquelle|hwsg|trinkwasser|twsg|naturschutzgebiet|ffh|biotop|vsg|brutplatz|horst|abstand|entfernung|mindestabstand', text)]
    for idx in nature_matches[:25]:
        context_window_main += text[max(0, idx - 1000):min(len(text), idx + 1000)] + "\n...\n"

    context_window_main += "\n\n... [ENDE DES DOKUMENTS] ...\n\n" + text[-4000:]


    # --- KONTEXT FÜR CALL 2 (NUR FLÄCHEN - INTELLIGENTER FILTER) ---
    context_window_areas = ""
    
    # 1. Alle Vorkommen der Flächen-Schlagwörter finden (inkl. waldersatz und aufforstung)
    area_matches = [m.start() for m in re.finditer(r'(?i)fundament|aufstandsfläche|zuwegung|zufahrt|wegeausbau|kranstellfläche|montagefläche|waldumwandlung|versiegelung|inanspruchnahme|waldersatz|aufforstung', text)]
    
    filtered_matches = []
    last_added_idx = -10000
    
    for idx in area_matches:
        # 2. Um Überschneidungen zu vermeiden, nehmen wir nur alle ~1000 Zeichen einen Ausschnitt
        if idx - last_added_idx < 1000:
            continue
            
        # 3. Wir prüfen das Umfeld (± 600 Zeichen) um das gefundene Wort
        snippet = text[max(0, idx - 600):min(len(text), idx + 600)]
        
        # 4. DER TRICK: Nur wenn in der Nähe auch eine Flächen-Einheit steht, ist die Textstelle für die KI relevant!
        if re.search(r'(?i)m²|m2|ha|hektar|quadratmeter|km2|km²', snippet):
            filtered_matches.append(idx)
            last_added_idx = idx

    # Fallback, falls OCR die Einheiten komplett zerschossen hat
    if not filtered_matches:
        filtered_matches = area_matches[-15:]

    # 5. Wir fügen die besten Treffer (bis zu 35 Abschnitte) für die KI zusammen
    for idx in filtered_matches[-35:]:
        context_window_areas += text[max(0, idx - 1000):min(len(text), idx + 1000)] + "\n...\n"


    # ==========================================
    # PROMPT 1: ALLES AUSSER SPEZIFISCHE FLÄCHEN
    # ==========================================
    template_main = """
    DU BIST EIN GNADENLOSER DATEN-EXTRAKTOR FÜR GENEHMIGUNGSBESCHEIDE.
    DEINE EINZIGE AUFGABE IST ES, EIN GÜLTIGES JSON-OBJEKT ZU ERSTELLEN.

    WICHTIGE GRUNDREGELN:
    1. Wenn ein Wert nicht eindeutig gefunden wird, schreibe "".
    2. GIB GENAU EIN (1) ZUSAMMENHÄNGENDES JSON-OBJEKT ZURÜCK!
    3. KEINE SCHÄTZUNG.
    4. KEINE INTERPRETATION.

    -------------------------------------------------------
    KONTEXT (Das Dokument):
    {context}

    -------------------------------------------------------
    TEIL 1: METADATA ALLGEMEIN

    - "Aktenzeichen (Az)": NUR die reine Kennzeichnung (Ignoriere "Az.").
    - "Genehmigungsdatum": Datum der Entscheidung.
    - "Antragsdatum": Nur Datum nach „Antrag vom“ oder „eingegangen am“.
    - "Vorhabenträger": Firma.
    - "Genehmigungsbehörde": z.B. Landratsamt, Bezirksregierung.

    ART DES VERFAHRENS & PRÜFUNGEN (Ja/Nein):
    - Neuerrichtung
    - Änderungsgenehmigung
    - Repowering nach § 16b BImSchG
    - Repowering als Neuerrichtung
    - "UVP durchgeführt? (Ja/Nein)": Markiere Wörter wie "UVP-Bericht" oder "Umweltverträglichkeitsprüfung", wenn sie durchgeführt wurde. Trage "Ja" ein, wenn sie durchgeführt wurde (z.B. "Eine UVP wurde durchgeführt."). Trage "Nein" ein, wenn z.B. "Eine UVP-Vorprüfung ergab keine UVP-Pflicht" dort steht. Signalwörter: Umweltverträglichkeitsprüfung, UVP, UVP-Vorprüfung, Umweltbericht.

    FINANZEN:
    - Rückbaukosten / Bürgschaft (€): Nur wenn ausdrücklich als Sicherheitsleistung / Bürgschaft genannt.

    -------------------------------------------------------
    TEIL 2: WEA-STECKBRIEFE

    Für JEDE WEA ein eigenes Objekt erzeugen.

    TECHNIK:
    - Hersteller
    - Anlagentyp (strikt getrennt vom Hersteller)
    - Nennleistung (MW)
    - Nabenhöhe (m)
    - Gesamthöhe (m)
    - Rotordurchmesser (m)
    - Turmtyp
    - Netzanschlusspunkt

    STANDORT (der WEA, nicht der Behörde):
    - Bundesland
    - Landkreis
    - Gemeinde/Stadt
    - PLZ
    - Gemarkung
    - Flurnummer
    - Flurstück
    - UTM Koordinaten:
      Rechtswert (E) = 6-stellig
      Hochwert (N) = 7-stellig
      NIEMALS Höhenangaben übernehmen.

    -------------------------------------------------------
    TEIL 3: SCHUTZGEBIETE, ABSTÄNDE & EINSCHRÄNKUNGEN

    NATURSCHUTZ JA/NEIN REGEL:
    - JA bei: "liegt im", "befindet sich im", "innerhalb von"
    - NEIN bei: "liegt außerhalb", "nicht innerhalb", "in ... Entfernung"
    - Wenn nichts erwähnt → NEIN
    
    ABSTÄNDE:
    - Wenn "(Genannt?)" = "Nein" → Mast = "0" und Rotor = "0".
    - Nur konkrete Meterangaben übernehmen. Keine Interpretation.

    ERZWUNGENES OUTPUT-FORMAT (START MIT {{ UND ENDE MIT }}):
    {{
      "1_MetaData_Allgemein": {{
        "Titel Genehmigungsbescheid": "",
        "Aktenzeichen (Az)": "",
        "Genehmigungsdatum": "",
        "Antragsdatum": "",
        "Vorhabenträger": "",
        "Genehmigungsbehörde": "",
        "Neuerrichtung": "",
        "Änderungsgenehmigung": "",
        "Repowering nach § 16b BImSchG": "",
        "Repowering als Neuerrichtung": "",
        "UVP durchgeführt? (Ja/Nein)": "",
        "Rückbaukosten / Bürgschaft (€)": ""
      }},
      "2_WEA_Details": [
        {{
          "Anlagen-Nr. / Kennzeichnung": "WEA 01",
          "Hersteller": "",
          "Anlagentyp": "",
          "Nennleistung (MW)": "",
          "Nabenhöhe (m)": "",
          "Gesamthöhe (m)": "",
          "Rotordurchmesser (m)": "",
          "Turmtyp": "",
          "Netzanschlusspunkt": "",
          "Bundesland": "",
          "Landkreis": "",
          "Gemeinde/Stadt": "",
          "PLZ": "",
          "Gemarkung": "",
          "Flurnummer": "",
          "Flurstück": "",
          "UTM 32 Koordinaten (Rechtswert/E)": "",
          "UTM 32 Koordinaten (Hochwert/N)": "",
          "UTM 33 Koordinaten (Rechtswert/E)": "",
          "UTM 33 Koordinaten (Hochwert/N)": ""
        }}
      ],
      "3_Flaechen": {{
        "Kennziffer (ID)": "",
        "Derzeitige Flächennutzung": "",
        "Lage im Naturschutzgebiet (NSG)": "",
        "Lage im FFH-Gebiet": "",
        "Lage im Vogelschutzgebiet (VSG)": "",
        "Lage im gesetzlich geschützten Biotop (GGB)": "",
        "Lage im Naturpark": "",
        "Lage im Biosphärenreservat (BR)": "",
        "Lage im Landschaftsschutzgebiet (LSG)": "",
        "Lage im Trinkwasserschutzgebiet (TWSG)": "",
        "Lage im Heilquellenschutzgebiet (HWSG)": "",
        
        "Abstand Wald (Genannt?)": "",
        "Abstand Wald (Mast) [m]": "",
        "Abstand Wald (Rotor) [m]": "",
        "Abstand FFH-Gebiet (Genannt?)": "",
        "Abstand FFH-Gebiet (Mast) [m]": "",
        "Abstand FFH-Gebiet (Rotor) [m]": "",
        "Abstand VSG (Genannt?)": "",
        "Abstand VSG (Mast) [m]": "",
        "Abstand VSG (Rotor) [m]": "",
        "Abstand Naturpark (Genannt?)": "",
        "Abstand Naturpark (Mast) [m]": "",
        "Abstand Naturpark (Rotor) [m]": "",
        "Abstand BR (Genannt?)": "",
        "Abstand BR (Mast) [m]": "",
        "Abstand BR (Rotor) [m]": "",
        "Abstand GGB (Genannt?)": "",
        "Abstand GGB (Mast) [m]": "",
        "Abstand GGB (Rotor) [m]": "",
        "Abstand LSG (Genannt?)": "",
        "Abstand LSG (Mast) [m]": "",
        "Abstand LSG (Rotor) [m]": "",
        "Abstand NSG (Genannt?)": "",
        "Abstand NSG (Mast) [m]": "",
        "Abstand NSG (Rotor) [m]": "",
        "Abstand Geschützte Landschaftsbestandteile (GLB) (Genannt?)": "",
        "Abstand Geschützte Landschaftsbestandteile (GLB) (Mast) [m]": "",
        "Abstand Geschützte Landschaftsbestandteile (GLB) (Rotor) [m]": "",
        "Abstand Feuchtgebiete (RAMSAR) (Genannt?)": "",
        "Abstand Feuchtgebiete (RAMSAR) (Mast) [m]": "",
        "Abstand Feuchtgebiete (RAMSAR) (Rotor) [m]": "",
        "Abstand Moore (Genannt?)": "",
        "Abstand Moore (Mast) [m]": "",
        "Abstand Moore (Rotor) [m]": "",
        "Abstand Brutplätze Vögel (Genannt?)": "",
        "Abstand Brutplätze Vögel (Mast) [m]": "",
        "Abstand Brutplätze Vögel (Rotor) [m]": "",
        "Abstand Nahrungshabitate, Horstschutzzonen (Genannt?)": "",
        "Abstand Nahrungshabitate, Horstschutzzonen (Mast) [m]": "",
        "Abstand Nahrungshabitate, Horstschutzzonen (Rotor) [m]": "",
        "Abstand Flugkorridore (Genannt?)": "",
        "Abstand Flugkorridore (Mast) [m]": "",
        "Abstand Flugkorridore (Rotor) [m]": "",
        
        "Abstand Seismologie (Genannt?)": "",
        "Abstand Seismologie (Mast) [m]": "",
        "Abstand Seismologie (Rotor) [m]": "",
        "Abstand D/VOR (Genannt?)": "",
        "Abstand D/VOR (Mast) [m]": "",
        "Abstand D/VOR (Rotor) [m]": "",
        "Abstand Luftverteidigungsradar (Genannt?)": "",
        "Abstand Luftverteidigungsradar (Mast) [m]": "",
        "Abstand Luftverteidigungsradar (Rotor) [m]": "",
        "Abstand Wetterradar (Genannt?)": "",
        "Abstand Wetterradar (Mast) [m]": "",
        "Abstand Wetterradar (Rotor) [m]": "",
        
        "Abstand Bundesstraße (Genannt?)": "",
        "Abstand Bundesstraße (Mast) [m]": "",
        "Abstand Bundesstraße (Rotor) [m]": "",
        "Abstand Landstraße (Genannt?)": "",
        "Abstand Landstraße (Mast) [m]": "",
        "Abstand Landstraße (Rotor) [m]": "",
        "Abstand Kreisstraße (Genannt?)": "",
        "Abstand Kreisstraße (Mast) [m]": "",
        "Abstand Kreisstraße (Rotor) [m]": "",
        "Abstand Autobahn (Genannt?)": "",
        "Abstand Autobahn (Mast) [m]": "",
        "Abstand Autobahn (Rotor) [m]": "",
        "Abstand Bahn (Genannt?)": "",
        "Abstand Bahn (Mast) [m]": "",
        "Abstand Bahn (Rotor) [m]": "",
        "Abstand Bundeswasserstraße (Genannt?)": "",
        "Abstand Bundeswasserstraße (Mast) [m]": "",
        "Abstand Bundeswasserstraße (Rotor) [m]": "",
        "Abstand unterirdische Leitungen (Genannt?)": "",
        "Abstand unterirdische Leitungen (Mast) [m]": "",
        "Abstand unterirdische Leitungen (Rotor) [m]": "",
        
        "Abstand Trinkwasserschutzgebiet (TWSG) (Genannt?)": "",
        "Abstand Trinkwasserschutzgebiet (TWSG) (Mast) [m]": "",
        "Abstand Trinkwasserschutzgebiet (TWSG) (Rotor) [m]": "",
        "Abstand Hochwasserschutzgebiet (HWSG) (Genannt?)": "",
        "Abstand Hochwasserschutzgebiet (HWSG) (Mast) [m]": "",
        "Abstand Hochwasserschutzgebiet (HWSG) (Rotor) [m]": "",
        "Abstand Heilquellenschutzgebiet (HQSG) (Genannt?)": "",
        "Abstand Heilquellenschutzgebiet (HQSG) (Mast) [m]": "",
        "Abstand Heilquellenschutzgebiet (HQSG) (Rotor) [m]": "",
        "Abstand Optisch bedrängende Wirkung (Genannt?)": "",
        "Abstand Optisch bedrängende Wirkung (Mast) [m]": "",
        "Abstand Optisch bedrängende Wirkung (Rotor) [m]": "",
        "Abstand Gewässer und Seen (Genannt?)": "",
        "Abstand Gewässer und Seen (Mast) [m]": "",
        "Abstand Gewässer und Seen (Rotor) [m]": "",

        "Verbot Flächeninanspruchnahme außerhalb definierter Bereiche": "",
        "Verbot neuer Wege in bestimmten Bereichen": "",
        "Begrenzung der Wegebreite": "",
        "Nutzung bestehender Wege zwingend vorgeschrieben": ""
      }},
      "4_Stroem": {{
        "Abstände Infrastruktur & Radar": ""
      }}
    }}
    """

    template_areas = """
    DU DARFST AUSSCHLIESSLICH FOLGENDE DREI FLÄCHEN EXTRAHIEREN:
    1) Fundamentfläche (Mast)
    2) Zuwegungsfläche (nur Wege)
    3) Kranstellfläche (nur Betriebsfläche)

    WICHTIGE GRUNDREGELN:
    1. Wenn ein Wert nicht eindeutig gefunden wird, schreibe "".
    2. GIB GENAU EIN (1) ZUSAMMENHÄNGENDES JSON-OBJEKT ZURÜCK!
    3. WENN DU BEI "Genannt?" EIN "Ja" EINTRÄGST, BIST DU GEZWUNGEN, DIE GEFUNDENE ZAHL BEI m² ODER ha EINZUTRAGEN! LASS ES NIEMALS LEER!
    4. Das Dokument kann sehr lang sein. Es muss der gesamte Textauszug durchsucht werden, auch wenn relevante Begriffe weit auseinander stehen.
    5. VERBOT: Es dürfen KEINE Flächen durch Division, Mittelwertbildung oder rechnerische Aufteilung erzeugt werden! Nimm exakt die Zahl, die im Text steht.

    -------------------------------------------------------
    ALLGEMEINE FLÄCHEN-REGELN (SEHR WICHTIG):
    1. EINHEITEN-PFLICHT: Extrahiere NUR Zahlen, bei denen direkt die Einheit m², qm, ha oder Hektar steht. Ignoriere alle Zahlen ohne Flächeneinheit oder unklare Dezimalzahlen!
    2. SATZ-REGEL: Die Zahl muss im SELBEN SATZ (oder max. 1 Satz davor/danach wegen OCR-Fehlern) wie das jeweilige Schlüsselwort stehen.
    3. DAUERHAFTIGKEIT: Ignoriere temporäre Flächen (z.B. "temporär", "bauzeitlich", "Lagerflächen", "nach Bauende rückgebaut"). Wir suchen nur die dauerhafte Inanspruchnahme/Versiegelung. Wenn mehrere Zahlen im gleichen Kontext stehen, nimm die Zahl mit dauerhaftem Flächenbezug ("dauerhaft versiegelt", "anlagenbedingt").
    4. UMRECHNUNG: Wenn nur eine Einheit vorhanden ist, rechne automatisch um und fülle BEIDE Felder: 1 ha = 10.000 m² / 10.000 m² = 1 ha.
    5. ANTI-FEHLER-REGEL: Die KI darf KEINE Zahl unter 100 m² extrahieren, es sei denn, sie wird ausdrücklich "Fundamentfläche" oder "Mast" genannt! Das verhindert falsche Extraktionen von Rotorblattüberstrichen oder Kabellängen.

    -------------------------------------------------------
    WICHTIGE NEGATIV-REGEL FÜR FLÄCHEN
    Folgende Flächen dürfen NIEMALS für Mast, Zuwegung oder Kran verwendet werden: Waldumwandlung, Ausgleichsmaßnahmen, Kompensationsflächen, Ersatzmaßnahmen, Bauphasenflächen. Diese sind IMMER zu ignorieren.

    -------------------------------------------------------
    A) FLÄCHE MAST
    - "Fläche Mast (Genannt?)": "Ja" oder "Nein".
    - "Fläche Mast ($m^2$)" / "($ha$)": TRAGE HIER DIE ZAHL EIN!
    REGEL: Zahl übernehmen, wenn Begriffe wie Fundament, Fundamentfläche, Turmfuß, Mast, Aufstandsfläche genannt werden.

    -------------------------------------------------------
    B) FLÄCHE ZUWEGUNG
    - "Fläche Zuwegung (Genannt?)": "Ja" oder "Nein".
    - "Fläche Zuwegung ($m^2$)" / "($ha$)": TRAGE HIER DIE ZAHL EIN!
    
    1. STUFE: Wenn im Text explizit eine Fläche mit Begriffen wie Zuwegung, Zuwegungen, Zuwegungsfläche, Zufahrt, Wegeausbau, Weg genannt wird -> Zahl extrahieren.
    2. STUFE: Wenn die Fläche gemeinsam mit Kranstellflächen genannt wird (z.B. "Kranstellflächen und Zuwegungen"):
       - Wenn KEINE separate Fläche für Kranstellflächen existiert -> Gesamtzahl übernehmen.
       - Wenn separate Kranfläche existiert -> NUR die explizite Zuwegungsfläche übernehmen. NICHT rechnen oder schätzen!
    3. STUFE: Wenn keine eindeutige Zuordnung möglich ist oder nur eine undefinierte Gesamtversiegelung genannt wird -> Feld leer lassen "".

    -------------------------------------------------------
    C) KRANSTELLFLÄCHE
    - "Fläche Kran (Genannt?)": "Ja" oder "Nein".
    - "Fläche Kran ($m^2$)" / "($ha$)": TRAGE HIER DIE ZAHL EIN!
    REGEL: Zahl übernehmen, wenn Begriffe wie Kranstellfläche, Kranfläche, Montagefläche genannt werden. (Achtung: Wenn bei Zuwegung eine kombinierte Zahl eingetragen wurde, diese hier nicht doppelt eintragen).

    -------------------------------------------------------
    SONDERFLÄCHEN:
    - "Waldumwandlung notwendig?": (Ja/Nein)
    - "Flächen für Waldersatz (in km2)": Nur die Zahl.

    GIB NUR DIESES JSON ZURÜCK:
    {{
      "Waldumwandlung notwendig?": "",
      "Flächen für Waldersatz (in km2)": "",
      "Fläche Mast (Genannt?)": "",
      "Fläche Mast ($m^2$)": "",
      "Fläche Mast ($ha$)": "",
      "Fläche Zuwegung (Genannt?)": "",
      "Fläche Zuwegung ($m^2$)": "",
      "Fläche Zuwegung ($ha$)": "",
      "Fläche Kran (Genannt?)": "",
      "Fläche Kran ($m^2$)": "",
      "Fläche Kran ($ha$)": ""
    }}

    DOKUMENT (Gefilterte Textauszüge):
    {context}
    """

    # Modelle mit Temperatur 0 für maximale deterministische Präzision
    llm = ChatMistralAI(model="mistral-large-2411", temperature=0.0, timeout=300, max_retries=2)
    
    chain_main = ChatPromptTemplate.from_template(template_main) | llm | StrOutputParser()
    chain_areas = ChatPromptTemplate.from_template(template_areas) | llm | StrOutputParser()
    
    def parse_llm_json(res_str):
        clean_str = res_str.replace("```json", "").replace("```", "").strip()
        match = re.search(r"\{.*\}", clean_str, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return None

    try:
       # --- CALL 1: HAUPTDATEN ---
        status_text.info("🧠 Phase 1/2: Extrahiere Metadaten, Technik & Abstände (Dauert ca. 1-2 Min)...")
        res_main = chain_main.invoke({"context": context_window_main})
        json_main = parse_llm_json(res_main)
        
        if not json_main:
            st.error("Fehler in Phase 1: Ungültiges JSON.")
            return {}

        # ---> NEU: HIER IST DIE PAUSE FÜR DAS API-LIMIT <---
        time.sleep(2)

        # --- CALL 2: SCHARFSCHÜTZE FLÄCHEN ---
        status_text.info("🎯 Phase 2/2: Scharfschützen-Extraktion der Flächen (Jetzt mit Einheiten-Filter!)...")
        res_areas = chain_areas.invoke({"context": context_window_areas})
        json_areas = parse_llm_json(res_areas)
        
        if not json_areas:
            st.warning("Phase 2 (Flächen) fehlgeschlagen. Werte bleiben leer.")
            json_areas = {
                "Fläche Mast (Genannt?)": "", "Fläche Mast ($m^2$)": "", "Fläche Mast ($ha$)": "",
                "Fläche Zuwegung (Genannt?)": "", "Fläche Zuwegung ($m^2$)": "", "Fläche Zuwegung ($ha$)": "",
                "Fläche Kran (Genannt?)": "", "Fläche Kran ($m^2$)": "", "Fläche Kran ($ha$)": ""
            }

        # --- MERGE: ZUSAMMENFÜHREN ---
        status_text.info("⚙️ Führe Daten zusammen und bereite Tabellen vor...")
        
        if "3_Flaechen" not in json_main:
            json_main["3_Flaechen"] = {}
            
        json_main["3_Flaechen"].update(json_areas)
        
        # --- POST-PROCESSING ---
        final_data = post_process_coordinates(json_main)
        final_data = restructure_and_calculate_data(final_data)
        
        status_text.success("✅ Extraktion erfolgreich in 2 Phasen abgeschlossen!")
        return final_data
        
    except Exception as e:
        error_msg = str(e).lower()
        if "429" in error_msg or "rate limit" in error_msg or "timeout" in error_msg:
            st.error(f"⏳ API ist ausgelastet oder Limit erreicht. Detail-Fehler: {e}")
        else:
            st.error(f"Kritischer Fehler bei der API-Abfrage: {e}")
        return {}
    
# ---------------- MAIN UI ----------------
def main():
    st.title("PDF Extraktor")

    if "full_result" not in st.session_state: st.session_state.full_result = {}
    if "extracted_text" not in st.session_state: st.session_state.extracted_text = ""

    with st.sidebar:
        st.header("1. Upload")
        pdfs = st.file_uploader("PDFs hochladen", type="pdf", accept_multiple_files=True)
        
        st.write("---")
        st.header("2. Text lokal auslesen")
        if st.button(" Start Lokale OCR"):
            if pdfs:
                with st.spinner("Lese Text...."):
                    st.session_state.extracted_text = read_pdfs_tesseract(pdfs)

        st.write("---")
        st.header("3. Daten Extrahieren")
        if st.button(" Start KI-Extraktion"):
            if not st.session_state.extracted_text:
                st.error("Bitte zuerst Text einlesen (Schritt 2)!")
                st.stop()
            
            st.session_state.full_result = extract_all_data(st.session_state.extracted_text)

    # --- ANZEIGE ---
    tab1, tab2 = st.tabs(["📊 Ergebnis & JSON", "📝 Extrahierter Text (Tesseract)"])
    
    with tab1:
        if st.session_state.full_result:
            st.subheader("Deine Anlagen-Steckbriefe (Fertig für MongoDB / Excel!)")
            st.json(st.session_state.full_result)
            
            st.divider()
            st.subheader("Zum Kopieren (JSON)")
            st.code(json.dumps(st.session_state.full_result, indent=4, ensure_ascii=False), language="json")
        else:
            st.info("Bitte Dokumente hochladen und die Schritte 1 bis 3 ausführen.")

    with tab2:
        st.markdown(st.session_state.extracted_text)

if __name__ == "__main__":
    main()