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

# --- NEU: IMPORTS FÜR RAM-SCHONENDES LOKALES OCR ---
import gc
from pdf2image import convert_from_bytes, pdfinfo_from_bytes
import pytesseract

# --- IMPORTS FÜR KI-EXTRAKTION ---
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_mistralai import ChatMistralAI
from pyproj import Transformer

# ---------------- CONFIG & API KEY SICHERN ----------------
load_dotenv()

# Sicherer Check für MISTRAL_API_KEY (verhindert Absturz auf dem Mac)
mistral_key = os.getenv("MISTRAL_API_KEY")

try:
    if "MISTRAL_API_KEY" in st.secrets:
        mistral_key = st.secrets["MISTRAL_API_KEY"]
except Exception:
    pass

if mistral_key:
    os.environ["MISTRAL_API_KEY"] = mistral_key
else:
    st.error("⚠️ MISTRAL_API_KEY fehlt in den Secrets oder der .env Datei!")
    st.stop()

# ---------------- 1. BLITZSCHNELLES LOKALES OCR (TESSERACT) - RAM SCHONEND ----------------
def read_pdfs_tesseract(files):
    full_text = ""
    progress_bar = st.progress(0)
    status_text = st.empty()
    total_files = len(files)

    for i, f in enumerate(files):
        start_time = time.time()
        status_text.text(f"Lese Datei {i+1}/{total_files}: {f.name} (Analysiere PDF-Struktur)...")
        
        pdf_bytes = f.getvalue()
        
        try:
            # Nur die Info abrufen, wie viele Seiten das PDF hat (spart extrem viel RAM)
            info = pdfinfo_from_bytes(pdf_bytes)
            total_pages = info["Pages"]
        except Exception as e:
            st.error(f"Fehler beim Lesen der PDF-Info für {f.name}: {e}")
            continue
            
        doc_text = ""
        
        # Schleife: Immer nur EINE Seite in den Speicher laden
        for page_num in range(1, total_pages + 1):
            status_text.text(f"Lese Datei {i+1}/{total_files}: {f.name} (Scanne Seite {page_num} von {total_pages})...")
            
            # dpi 150 ist optimal: Spart 40% RAM gegenüber 200 dpi, aber Tesseract liest es trotzdem fehlerfrei
            images = convert_from_bytes(pdf_bytes, dpi=150, first_page=page_num, last_page=page_num)
            img = images[0]
            
            try:
                text = pytesseract.image_to_string(img, lang='deu')
            except:
                text = pytesseract.image_to_string(img, lang='eng')
                
            doc_text += f"\n\n--- SEITE {page_num} ---\n{text}\n"
            
            # SPEICHER SOFORT LEEREN, BEVOR DIE NÄCHSTE SEITE GELADEN WIRD
            del img
            del images
            gc.collect() 
            
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
def restructure_and_calculate_data(data, stroem_list):
    wea_list = data.get("2_WEA_Details", [])
    num_weas = len(wea_list)
    
    if num_weas > 0:
        meta_global = data.get("1_MetaData_Allgemein", {})
        flaechen_global = data.get("3_Flaechen", {})
        
        # Mathe-Funktion zum Teilen der Flächen
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
                    divided = round(num / divisor, 2)
                    return str(divided).replace(".", ",") 
                except:
                    pass
            return val_str

        new_wea_list = []
        
        for wea_technik in wea_list:
            wea_kennzeichnung = wea_technik.get("Anlagen-Nr. / Kennzeichnung", "")
            wea_flaechen = dict(flaechen_global) 
            
            wea_flaechen["Fläche Mast ($m^2$)"] = divide_val(wea_flaechen.get("Fläche Mast ($m^2$)", ""), num_weas)
            wea_flaechen["Fläche Mast ($ha$)"] = divide_val(wea_flaechen.get("Fläche Mast ($ha$)", ""), num_weas)
            wea_flaechen["Fläche Zuwegung ($m^2$)"] = divide_val(wea_flaechen.get("Fläche Zuwegung ($m^2$)", ""), num_weas)
            wea_flaechen["Fläche Zuwegung ($ha$)"] = divide_val(wea_flaechen.get("Fläche Zuwegung ($ha$)", ""), num_weas)
            wea_flaechen["Fläche Kran ($m^2$)"] = divide_val(wea_flaechen.get("Fläche Kran ($m^2$)", ""), num_weas)
            wea_flaechen["Fläche Kran ($ha$)"] = divide_val(wea_flaechen.get("Fläche Kran ($ha$)", ""), num_weas)
            
            # Ordne die Ström-Daten der richtigen WEA zu
            wea_stroem = {}
            for st_item in stroem_list:
                if st_item.get("Anlagen-Nr. / Kennzeichnung") == wea_kennzeichnung:
                    wea_stroem = st_item
                    break
            if not wea_stroem and len(stroem_list) >= 1:
                wea_stroem = stroem_list[0]
            
            wea_komplett = {
                "1_MetaData_Allgemein": dict(meta_global),
                "2_Technik_Standort": wea_technik,
                "3_Flaechen_und_Abstaende": wea_flaechen,
                "4_Stroem": wea_stroem
            }
            new_wea_list.append(wea_komplett)
            
        data["2_WEA_Details"] = new_wea_list
        data.pop("1_MetaData_Allgemein", None)
        data.pop("3_Flaechen", None)
        data.pop("4_Stroem", None)
        
    return data

# ---------------- 2. EXTRAKTION (3-PHASEN ARCHITEKTUR) ----------------
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

    # --- KONTEXT FÜR CALL 2 (FLÄCHEN) ---
    context_window_areas = ""
    area_matches = [m.start() for m in re.finditer(r'(?i)fundament|aufstandsfläche|zuwegung|zufahrt|wegeausbau|kranstellfläche|montagefläche|waldumwandlung|versiegelung|inanspruchnahme|waldersatz|aufforstung', text)]
    
    filtered_matches = []
    last_added_idx = -10000
    for idx in area_matches:
        if idx - last_added_idx < 1000:
            continue
        snippet = text[max(0, idx - 600):min(len(text), idx + 600)]
        if re.search(r'(?i)m²|m2|ha|hektar|quadratmeter|km2|km²', snippet):
            filtered_matches.append(idx)
            last_added_idx = idx

    if not filtered_matches:
        filtered_matches = area_matches[-15:]

    for idx in filtered_matches[-35:]:
        context_window_areas += text[max(0, idx - 1000):min(len(text), idx + 1000)] + "\n...\n"

    # --- KONTEXT FÜR CALL 3 (STRÖM / ABSCHALTUNGEN) ---
    context_window_stroem = ""
    stroem_matches = [m.start() for m in re.finditer(r'(?i)abschalt|betrieb|schall|fledermaus|vogel|milan|mahd|ernte|pflug|eisansatz|eiserkennung|schatten|radar|antikollision|kamera|identiflight|monitoring|nacht|lärm|rotorblattheizung|flight\s*manager|immission', text)]
    
    last_added_idx = -10000
    filtered_stroem = []
    for idx in stroem_matches:
        if idx - last_added_idx < 1000: continue
        filtered_stroem.append(idx)
        last_added_idx = idx
        
    for idx in filtered_stroem[-45:]:
        context_window_stroem += text[max(0, idx - 1000):min(len(text), idx + 1000)] + "\n...\n"

    # ==========================================
    # PROMPT 1: ALLES AUSSER SPEZIFISCHE FLÄCHEN (VERIFIZIERT & PRÄZISE)
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

    # ==========================================
    # PROMPT 2: FLÄCHEN (MAXIMAL OPTIMIERT - v3)
    # ==========================================
    template_areas = """
    FLÄCHEN-EXTRAKTION FÜR WINDENERGIEANLAGEN

    Extrahiere ausschließlich drei Flächentypen:

    1. Fundamentfläche (Mast / Turm)
    2. Zuwegungsfläche (Wege)
    3. Kranstellfläche

    --------------------------------------------------

    SCHLÜSSELWÖRTER

    Fundament:
    Fundament, Fundamentfläche, Turmfundament, Mastfundament,
    Turmfuß, Aufstandsfläche

    Zuwegung:
    Zuwegung, Zuwegungen, Zuwegungsfläche, Zufahrt,
    Erschließungsweg, Wegeausbau, Feldweg,
    Wirtschaftsweg, Weg

    Kranfläche:
    Kranstellfläche, Kranfläche, Montagefläche,
    Rüstfläche, Betriebsfläche

    --------------------------------------------------

    SUCHREGELN

    1. Extrahiere nur Zahlen mit Einheiten:
    m², qm, ha, Hektar.

    2. Die Zahl darf im selben Satz oder
    bis zu drei Sätze entfernt vom
    Schlüsselwort stehen.

    3. Temporäre Flächen ignorieren:
    temporär, bauzeitlich, Baustelle,
    Lagerfläche.

    4. Wenn im Dokument steht:

    "Kranstellflächen und Zuwegungen 16.645 m²"

    → Wert NUR bei Zuwegung eintragen.

    5. Wenn eine Fläche ausdrücklich
    "je Anlage" oder "je WEA" genannt wird,
    übernehme diesen Wert.

    6. Wenn nur eine Gesamtfläche genannt wird,
    übernehme sie ohne Berechnung.

    8. JE-ANLAGE-REGEL:
    Wenn im Text die Begriffe "je Anlage", "je WEA", "pro Anlage", "pro WEA"
    nicht vorkommen, dann handelt es sich wahrscheinlich um eine Gesamtfläche.
    In diesem Fall:
    → trage die Fläche nur einmal ein
    → kopiere sie NICHT automatisch auf jede WEA.

    9. FUNDAMENT-PLAUSIBILITÄT:
    Fundamentfläche von Windenergieanlagen liegt typischerweise zwischen 300 m² und 1500 m².
    Wenn eine gefundene Fläche kleiner als 200 m² ist, prüfe besonders kritisch, ob sie
    wirklich zum Fundament gehört (oft ist es Kleininfrastruktur wie ein Trafo).
    Wenn eine Fundamentfläche > 5000 m² ist → wahrscheinlich Park-Gesamtfläche → NICHT als
    Einzel-Fundament übernehmen!

    10. KRAN-PLAUSIBILITÄT:
    Kranstellflächen liegen meist zwischen 1000 m² und 4000 m².
    Wenn eine Zahl deutlich kleiner ist (z.B. 200–400 m²), handelt es sich wahrscheinlich
    NICHT um die vollständige Kranstellfläche.

    --------------------------------------------------

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

    # ==========================================
    # PROMPT 3: STRÖM (ABSCHALTUNGEN)
    # ==========================================
    template_stroem = """
    DU BIST EIN DATEN-EXTRAKTOR. EXTRAHIERE ALLE BETRIEBSABSCHALTUNGEN UND REGULATIONEN FÜR JEDE WINDKRAFTANLAGE (WEA).
    GIB GENAU EIN GÜLTIGES JSON-OBJEKT ZURÜCK, DAS EIN ARRAY "4_Stroem" ENTHÄLT. FÜR JEDE WEA GIBT ES EIN EIGENES OBJEKT IM ARRAY.

    WICHTIGE GRUNDREGELN:
    1. Trage bei (Ja/Nein)-Feldern "Ja" oder "Nein" ein. Wenn nichts erwähnt wird, trage "" ein.
    2. Wenn ein Wert (Datum, Temperatur, Art) nicht im Text steht, schreibe "".
    3. Wenn Abschaltungen für "alle Anlagen" gelten, kopiere diese Werte bei jeder einzelnen WEA in das Array.

    -------------------------------------------------------
    EXTRAKTIONSREGELN (SEHR WICHTIG)

    A) FLEDERMAUS-ABSCHALTUNG
    Suche nach:
    - Fledermaus
    - Abschaltalgorithmus
    - Abschaltung Fledermäuse

    Extrahiere:
    - Zeitraum
    - Windgeschwindigkeit
    - Temperatur
    - Tageszeit (Beachte zwingend Regel G!)

    -------------------------------------------------------
    B) VOGEL-ABSCHALTUNG & VOGELARTEN
    Suche nach:
    - Vogelschutz
    - Antikollisionssystem
    - phänologische Abschaltung
    - landwirtschaftliche Abschaltung

    REGEL FÜR VOGELARTEN:
    Wenn eine Vogelart im selben Absatz wie eine Abschaltung, ein Bewirtschaftungsradius oder eine landwirtschaftliche Abschaltung genannt wird, extrahiere die Vogelart.
    
    Typische Arten:
    Weißstorch, Rotmilan, Schwarzmilan, Wiesenweihe, Rohrweihe, Grauammer, Bussardarten, Greifvögel (Rotmilan), Mäusebussard, Kollisionsgefährdete Vogelarten (Wespenbussard), Baumfalke, Kornweihe, Großvögel.

    Beispiel:
    "Zum Schutz des Weißstorchs ist ein Bewirtschaftungsradius von 150 m um die WEA einzuhalten."
    → Ausgabe: "Vogelart (V)": "Weißstorch"

    Extrahiere zusätzlich:
    - Zeitraum
    - Windgeschwindigkeit
    - Niederschlag
    - Tageszeiten (Beachte zwingend Regel G!)

    -------------------------------------------------------
    C) BEWIRTSCHAFTUNGSRAUM UM WEA
    Suche nach Abständen oder Radien um eine Windenergieanlage.

    Keywords:
    - Bewirtschaftungsraum
    - Bewirtschaftungsradius
    - Radius um die WEA
    - Umkreis um die WEA
    - Abstand um die WEA
    - Schutzradius

    Wenn eine Zahl mit Einheit "m" im Zusammenhang mit der WEA steht, extrahiere diese Zahl.

    Beispiele:
    "im Umkreis von 250 m um die WEA"
    "Bewirtschaftungsraum von 250 m"
    "innerhalb eines Radius von 250 m um den Mast"

    → Ausgabe:
    "Abgeschalteter Bewirtschaftsungsraum um WEA ab Mast (m)": "250"

    Regeln:
    - Wenn "um den Mast", "um die WEA", "um den Turm" → Mast-Feld
    - Wenn "um den Rotor" → Rotor-Feld

    -------------------------------------------------------
    D) BAUFELDRÄUMUNG
    Suche nach:
    - Baufeldräumung
    - Rodung
    - außerhalb der Brutzeit

    Extrahiere Zeitraum.

    -------------------------------------------------------
    E) SCHATTENWURF-ABSCHALTUNG
    Suche nach:
    - Schattenwurf
    - Schattenabschaltung
    - Schattenwurfsystem
    - Abschaltautomatik

    Wenn eine automatische Abschaltung erwähnt wird → 
    "Schattenwurf-Abschaltung Summe (Ja/Nein)" = "Ja"

    Wenn Begriffe wie:
    - astronomisch
    - 30 Minuten pro Tag
    - 30 Stunden pro Jahr
    auftreten → 
    "Schattenwurf-Abschaltung astronomisch (Ja/Nein)" = "Ja"

    -------------------------------------------------------
    F) SCHALL-BETRIEBSREGULATION
    Suche nach:
    - Nachtbetrieb
    - Betriebsmodus
    - Schallreduktion
    - schallreduzierter Betrieb
    - Betriebsmodus nachts

    Wenn vorhanden → "Schall-Betriebsregulation (Ja/Nein)" = "Ja"

    -------------------------------------------------------
    G) TAGESZEITEN (SEHR WICHTIG)
    Extrahiere die Tageszeiten exakt wie im Text.
    Kürze oder vereinfache die Zeitspanne NICHT.

    Wenn Formulierungen vorkommen wie:
    - "1 Stunde vor Sonnenuntergang bis Sonnenaufgang"
    - "30 Minuten vor Sonnenuntergang bis Sonnenaufgang"
    - "2 Stunden nach Sonnenuntergang"
    dann muss die Zeitspanne vollständig übernommen werden.

    Beispiele:
    Text: "1 Stunde vor Sonnenuntergang bis Sonnenaufgang"
    → Ausgabe "FM - Tageszeiten": "1 Stunde vor Sonnenuntergang bis Sonnenaufgang"

    -------------------------------------------------------
    ERZWUNGENES OUTPUT-FORMAT (START MIT {{ UND ENDE MIT }}):

    {{
      "4_Stroem": [
        {{
          "Anlagen-Nr. / Kennzeichnung": "WEA 01",
          "Automatisches Antikollisisonssystem (AKS) Vögel (Ja/Nein)": "",
          "Manuelle / Phänologische Abschaltung Vögel (Ja/Nein)": "",
          "Landwirtschaftliche Betriebsabschaltung Vögel (Summe) (Ja/Nein)": "",
          "Abschaltung Mahd (Ja/Nein)": "",
          "Abschaltung Pflug (Ja/Nein)": "",
          "Abschaltung Ernte (Ja/Nein)": "",
          "Abgeschalteter Bewirtschaftsungsraum um WEA ab Mast (m)": "",
          "Abgeschalteter Bewirtschaftsungsraum um WEA ab Rotor (m)": "",
          "Vogelart (V)": "",
          "V - Zeiträume (Datum von-bis)": "",
          "V - Windgeschwindigkeit (< m/s)": "",
          "V - Niederschlag (Nd)": "",
          "V - Tageszeiten": "",
          "Fledermausart (FM)": "",
          "FM - Zeiträume (Datum von-bis)": "",
          "FM - Windgeschwindigkeit (< m/s)": "",
          "FM - Temperatur (> °C)": "",
          "FM - Niederschlag (Nd)": "",
          "FM - Tageszeiten": "",
          "Fledermausmonitoring (Ja/Nein)": "",
          "Vorläufige Abschaltung Fledermausmonitoring (Ja/Nein)": "",
          "Baufeldräumung (Ja/Nein)": "",
          "Baufeldräumung (Zeiten)": "",
          "Schall-Betriebsregulation (Ja/Nein)": "",
          "Schall-Betriebsregulation (Zeiten)": "",
          "Schall-Abschaltung (Ja/Nein)": "",
          "Schall-Abschaltung (Zeiten)": "",
          "Vorläufige Schall-Betriebsregulation bis Monitoring (Ja/Nein)": "",
          "Vorläufige Schall-Betriebsabschaltung bis Monitoring (Ja/Nein)": "",
          "Geräuschpegelgrenzen (dB) tags/gesamt": "",
          "Geräuschpegelgrenzen (dB) nachts/gesamt": "",
          "Betriebsmodus tags (Bezeichnung)": "",
          "Betriebsmodus nachts (Bezeichnung)": "",
          "Blattheizung (Ja/Nein)": "",
          "Eiswurf-Abschaltung (Ja/Nein)": "",
          "Schattenwurf-Abschaltung Summe (Ja/Nein)": "",
          "Schattenwurf-Abschaltung astronomisch (Ja/Nein)": "",
          "Schattenwurf-Abschaltung meteorologisch (Ja/Nein)": "",
          "Abschaltung Turbulenzen (Ja/Nein)": "",
          "Abschaltung durch Flight Manager (Radarabschaltung) (Ja/Nein)": ""
        }}
      ]
    }}

    DOKUMENT (Gefilterte Textauszüge für Abschaltungen):
    {context}
    """

    
    # Modelle Setup
    llm = ChatMistralAI(model="mistral-large-2411", temperature=0.0, timeout=300, max_retries=2)
    
    chain_main = ChatPromptTemplate.from_template(template_main) | llm | StrOutputParser()
    chain_areas = ChatPromptTemplate.from_template(template_areas) | llm | StrOutputParser()
    chain_stroem = ChatPromptTemplate.from_template(template_stroem) | llm | StrOutputParser()
    
    def parse_llm_json(res_str):
        clean_str = res_str.replace("```json", "").replace("```", "").strip()
        match = re.search(r"\{.*\}", clean_str, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return None

    try:
        # Phase 1
        status_text.info("Phase 1/3: Extrahiere Metadaten & Technik...")
        res_main = chain_main.invoke({"context": context_window_main})
        json_main = parse_llm_json(res_main)
        if not json_main: return {}
        time.sleep(2)

        # Phase 2
        status_text.info("Phase 2/3: Scharfschützen-Extraktion der Flächen...")
        res_areas = chain_areas.invoke({"context": context_window_areas})
        json_areas = parse_llm_json(res_areas)
        if not json_areas: json_areas = {}
        time.sleep(2)

        # Phase 3
        status_text.info("Phase 3/3: Extrahiere Ström-Daten (Abschaltungen, Schall, Vögel, Eis)...")
        res_stroem = chain_stroem.invoke({"context": context_window_stroem})
        json_stroem = parse_llm_json(res_stroem)
        if not json_stroem: json_stroem = {"4_Stroem": []}

        # Daten zusammenführen
        status_text.info("Führe Daten zusammen und bereite Tabellen vor...")
        if "3_Flaechen" not in json_main: json_main["3_Flaechen"] = {}
        json_main["3_Flaechen"].update(json_areas)
        
        # Ström als Liste in die Logik übergeben
        stroem_liste = json_stroem.get("4_Stroem", [])
        
        # Post Processing und Zusammenbau pro Anlage
        final_data = post_process_coordinates(json_main)
        final_data = restructure_and_calculate_data(final_data, stroem_liste)
        
        status_text.success("Extraktion erfolgreich in 3 Phasen abgeschlossen!")
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
        if st.button("Start Lokale OCR"):
            if pdfs:
                with st.spinner("Lese Text...."):
                    st.session_state.extracted_text = read_pdfs_tesseract(pdfs)

        st.write("---")
        st.header("3. Daten Extrahieren")
        if st.button("Start KI-Extraktion"):
            if not st.session_state.extracted_text:
                st.error("Bitte zuerst Text einlesen (Schritt 2)!")
                st.stop()
            
            st.session_state.full_result = extract_all_data(st.session_state.extracted_text)

        st.write("---")
        st.header("4. System")
        with st.expander("Admin: App neu starten"):
            admin_password_input = st.text_input("Admin-Passwort", type="password")
            if st.button("App neu starten", type="primary"):
                # Versuch, Passwort aus st.secrets oder .env zu laden
                correct_password = os.getenv("ADMIN_PASSWORD")
                try:
                    if "ADMIN_PASSWORD" in st.secrets:
                        correct_password = st.secrets["ADMIN_PASSWORD"]
                except Exception:
                    pass
                
                if not correct_password:
                    st.error("⚠️ Kein Admin-Passwort im System konfiguriert (ADMIN_PASSWORD).")
                elif admin_password_input == correct_password:
                    st.success("Passwort korrekt. App wird zurückgesetzt...")
                    
                    # App Cache leeren und Session State zurücksetzen
                    st.cache_data.clear()
                    try:
                        st.cache_resource.clear()
                    except AttributeError:
                        pass
                    st.session_state.clear()
                    
                    # Streamlit Rerun ausführen (je nach Streamlit-Version)
                    try:
                        st.rerun()
                    except AttributeError:
                        st.experimental_rerun()
                elif admin_password_input:
                    st.error("❌ Falsches Passwort!")

    # --- ANZEIGE ---
    tab1, tab2 = st.tabs(["Ergebnis Dashboard", "Extrahierter Text (Tesseract)"])
    
    with tab1:
        if st.session_state.full_result:
            res = st.session_state.full_result
            
            # --- 1. METADATEN (ALLGEMEIN) ---
            st.header("Allgemeine Projektdaten")
            
            # Hole Metadaten aus dem Root-Objekt oder der ersten WEA (da es in restructure_and_calculate_data in die Anlagen verschoben wird)
            meta = res.get("1_MetaData_Allgemein", {})
            if not meta and res.get("2_WEA_Details"):
                meta = res["2_WEA_Details"][0].get("1_MetaData_Allgemein", {})
            
            def get_meta_val(key):
                val = meta.get(key, "")
                return val if val and str(val).strip() != "" else "-"
            
            # Die Top-3 Infos als schöne große Kennzahlen
            col1, col2, col3 = st.columns(3)
            col1.metric("Aktenzeichen", get_meta_val("Aktenzeichen (Az)"))
            col2.metric("Genehmigungsdatum", get_meta_val("Genehmigungsdatum"))
            col3.metric("Vorhabenträger", get_meta_val("Vorhabenträger"))
            
            # Den Rest der Metadaten in einen ausklappbaren Bereich
            with st.expander("Weitere allgemeine Daten", expanded=False):
                display_meta = {k: (v if v and str(v).strip() != "" else "-") for k, v in meta.items()}
                meta_df = pd.DataFrame(list(display_meta.items()), columns=["Eigenschaft", "Wert"])
                st.dataframe(meta_df, hide_index=True, use_container_width=True)
            
            st.divider()

            # --- 2. TECHNISCHE ANLAGENÜBERSICHT (WEA) ---
            st.header("Technische Anlagenübersicht")
            weas = res.get("2_WEA_Details", [])
            
            if weas:
                # Wir holen uns die Namen der WEAs für die Reiter-Titel
                wea_names = [wea.get("2_Technik_Standort", {}).get("Anlagen-Nr. / Kennzeichnung", f"WEA {i+1}") for i, wea in enumerate(weas)]
                
                # Wir erstellen für jede WEA einen eigenen Reiter (Tab)
                wea_tabs = st.tabs(wea_names)
                
                for i, wea_tab in enumerate(wea_tabs):
                    with wea_tab:
                        wea_data = weas[i]
                        tech = wea_data.get("2_Technik_Standort", {})
                        flaeche = wea_data.get("3_Flaechen_und_Abstaende", {})
                        stroem = wea_data.get("4_Stroem", {})
                        
                        # Bildschirm in 3 Spalten aufteilen
                        c1, c2, c3 = st.columns(3)
                        
                        # --- Spalte 1: Technik & Standort ---
                        with c1:
                            st.subheader("Meta-Daten")
                            # Leere Einträge rausfiltern
                            tech_clean = {k: v for k, v in tech.items() if v and str(v).strip() != ""}
                            if tech_clean:
                                tech_df = pd.DataFrame(list(tech_clean.items()), columns=["Eigenschaft", "Wert"])
                                st.dataframe(tech_df, hide_index=True, use_container_width=True)
                            else:
                                st.info("Keine technischen Spezifikationen gefunden.")
                                
                        # --- Spalte 2: Flächen & Abstände ---
                        with c2:
                            st.subheader("FlächenDaten")
                            flaeche_clean = {k: v for k, v in flaeche.items() if v and str(v).strip() != ""}
                            if flaeche_clean:
                                flaeche_df = pd.DataFrame(list(flaeche_clean.items()), columns=["Eigenschaft", "Wert"])
                                st.dataframe(flaeche_df, hide_index=True, use_container_width=True)
                            else:
                                st.info("Keine Flächen- oder Abstandsangaben gefunden.")
                                
                        # --- Spalte 3: Ström & Abschaltungen ---
                        with c3:
                            st.subheader("strömDaten")
                            stroem_clean = {k: v for k, v in stroem.items() if v and str(v).strip() != ""}
                            if stroem_clean:
                                stroem_df = pd.DataFrame(list(stroem_clean.items()), columns=["Regel", "Wert"])
                                st.dataframe(stroem_df, hide_index=True, use_container_width=True)
                            else:
                                st.info("Keine spezifischen Betriebs-Regulationen gefunden.")

            st.divider()
            
            # --- 3. DOWNLOAD / JSON FÜR IT ---
            with st.expander("Rohdaten (JSON) für Datenbank / Export anzeigen"):
                st.code(json.dumps(st.session_state.full_result, indent=4, ensure_ascii=False), language="json")

        else:
            st.info("Bitte Dokumente hochladen und die Schritte 1 bis 3 ausführen.")

    with tab2:
        st.markdown(st.session_state.extracted_text)

if __name__ == "__main__":
    main()