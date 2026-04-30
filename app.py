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
import io

# --- IMPORTS FÜR KI-EXTRAKTION ---
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_mistralai import ChatMistralAI
from pyproj import Transformer

# ---------------- CONFIG & API KEY SICHERN ----------------
load_dotenv(override=True)

mistral_key = os.getenv("MISTRAL_API_KEY")

_secrets_paths = [
    os.path.expanduser("~/.streamlit/secrets.toml"),
    os.path.join(os.path.dirname(__file__), ".streamlit", "secrets.toml"),
]
if not mistral_key and any(os.path.exists(p) for p in _secrets_paths):
    try:
        if "MISTRAL_API_KEY" in st.secrets:
            mistral_key = st.secrets["MISTRAL_API_KEY"]
    except Exception:
        pass

if mistral_key:
    os.environ["MISTRAL_API_KEY"] = mistral_key
else:
    st.error("⚠️ MISTRAL_API_KEY fehlt in der .env Datei!")
    st.stop()

# ---------------- HELPER: DATEINAMEN PARSEN ----------------
def parse_filename_metadata(filename):
    base = os.path.splitext(filename)[0]
    
    meta = {
        "Typ": "Genehmigung",
        "Aktenzeichen (Az)": "",
        "Anlagenanzahl": "",
        "Gemarkung": "",
        "Monat": "",
        "Jahr": "",
        "Original_Dateiname": filename
    }
    
    date_match = re.search(r'(\d{2})[-_](\d{4})', base)
    if date_match:
        meta["Monat"] = date_match.group(1)
        meta["Jahr"] = date_match.group(2)
        base = base.replace(date_match.group(0), "")
        
    anzahl_match = re.search(r'_(\d+)\s*(?:WEA)?_', base + "_", re.IGNORECASE)
    if anzahl_match:
        meta["Anlagenanzahl"] = anzahl_match.group(1)
        base = re.sub(r'_' + re.escape(anzahl_match.group(1)) + r'(?:\s*WEA)?_', '_', base + "_", flags=re.IGNORECASE)
        base = base[:-1] 
        
    parts = [p for p in base.split('_') if p.strip()]
    
    gemarkung_parts = []
    for p in parts:
        if "genehmig" in p.lower() or "bescheid" in p.lower():
            meta["Typ"] = p
        elif re.match(r'^[\d\-]+[a-zA-Z]?$', p): 
            meta["Aktenzeichen (Az)"] = p
        elif p.upper() != p and len(p) > 2: 
            gemarkung_parts.append(p)

    if gemarkung_parts:
        meta["Gemarkung"] = "_".join(gemarkung_parts)
    
    if not meta["Aktenzeichen (Az)"] and parts:
         if any(c.isdigit() for c in parts[0]):
             meta["Aktenzeichen (Az)"] = parts[0]
         elif len(parts) > 1 and any(c.isdigit() for c in parts[1]):
             meta["Aktenzeichen (Az)"] = parts[1]

    if not meta["Aktenzeichen (Az)"] and not meta["Gemarkung"]:
        raise ValueError(f"Strikte Validierung fehlgeschlagen: Konnte weder Aktenzeichen noch Gemarkung im Dateinamen identifizieren: {filename}")
        
    return meta


# ---------------- 1. BLITZSCHNELLES LOKALES OCR (TESSERACT) ----------------
def read_pdfs_tesseract(files):
    full_text = ""
    with st.sidebar:
        progress_bar = st.progress(0)
        status_text = st.empty()
        timer_text = st.empty()
    total_files = len(files)
    ocr_total_start = time.time()
    all_durations = []

    for i, f in enumerate(files):
        file_start_time = time.time()
        status_text.text(f"Lese Datei {i+1}/{total_files}: {f.name} (Analysiere PDF-Struktur)...")

        pdf_bytes = f.getvalue()

        try:
            info = pdfinfo_from_bytes(pdf_bytes)
            total_pages = info["Pages"]
        except Exception as e:
            st.error(f"Fehler beim Lesen der PDF-Info für {f.name}: {e}")
            continue

        doc_text = ""
        for page_num in range(1, total_pages + 1):
            elapsed_page = round(time.time() - file_start_time, 0)
            status_text.text(f"Datei {i+1}/{total_files}: {f.name}\nSeite {page_num} / {total_pages}")
            timer_text.caption(f"⏱ {int(elapsed_page)} Sek.")

            images = convert_from_bytes(pdf_bytes, dpi=150, first_page=page_num, last_page=page_num)
            img = images[0]

            try:
                text = pytesseract.image_to_string(img, lang='deu')
            except:
                text = pytesseract.image_to_string(img, lang='eng')

            doc_text += f"\n\n--- SEITE {page_num} ---\n{text}\n"

            del img
            del images
            gc.collect()

        full_text += f"\n\n--- DOKUMENT START: {f.name} ---\n{doc_text}\n--- DOKUMENT ENDE ---\n"

        file_duration = round(time.time() - file_start_time, 1)
        all_durations.append(file_duration)
        status_text.success(f"⚡ {f.name} fertig in {file_duration} Sek.!")
        timer_text.empty()
        progress_bar.progress((i + 1) / total_files)

    ocr_total_sec = round(time.time() - ocr_total_start, 1)
    ocr_total_min = round(ocr_total_sec / 60, 2)
    avg_per_file = round(sum(all_durations) / len(all_durations), 1) if all_durations else 0

    status_text.empty()
    timer_text.empty()
    progress_bar.empty()
    
    st.success(f"✅ OCR abgeschlossen! Gesamtdauer: **{ocr_total_sec} Sek. ({ocr_total_min} Min.)** | Ø pro Datei: **{avg_per_file} Sek.**")
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
def extract_all_data(text, metadata_dict, model_name="mistral-large-2512"):
    status_text = st.empty()

    is_large = (model_name == "mistral-large-2512")

    context_window_main = text[:8000] + "\n\n... [TEXT ÜBERSPRUNGEN] ...\n\n"

    coord_matches = [m.start() for m in re.finditer(r'(?i)rechtswert|hochwert|utm-koordinaten', text)]
    for idx in coord_matches[:6]:
        context_window_main += text[max(0, idx - 800):min(len(text), idx + 800)] + "\n...\n"

    nature_matches = [m.start() for m in re.finditer(r'(?i)heilquelle|hwsg|trinkwasser|twsg|naturschutzgebiet|ffh|biotop|vsg|brutplatz|horst|abstand|entfernung|mindestabstand', text)]
    for idx in nature_matches[:25]:
        context_window_main += text[max(0, idx - 1000):min(len(text), idx + 1000)] + "\n...\n"

    context_window_main += "\n\n... [ENDE DES DOKUMENTS] ...\n\n" + text[-4000:]

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

    context_window_stroem = ""
    stroem_matches = [m.start() for m in re.finditer(r'(?i)abschalt|betrieb|schall|fledermaus|vogel|milan|mahd|ernte|pflug|eisansatz|eiserkennung|schatten|radar|antikollision|kamera|identiflight|monitoring|nacht|lärm|rotorblattheizung|flight\s*manager|immission|ersatzgeld|landschaftsbild|ausgleich|artenschutzzahlung|bürgschaft|rückbau|geräusch|db\(a\)', text)]

    last_added_idx = -10000
    filtered_stroem = []
    for idx in stroem_matches:
        if idx - last_added_idx < 1000: continue
        filtered_stroem.append(idx)
        last_added_idx = idx

    for idx in filtered_stroem[-45:]:
        context_window_stroem += text[max(0, idx - 1000):min(len(text), idx + 1000)] + "\n...\n"

    # ==========================================
    # PROMPT 1: ALLES AUSSER SPEZIFISCHE FLÄCHEN
    # ==========================================
    template_main = """
    DU BIST EIN GNADENLOSER DATEN-EXTRAKTOR FÜR GENEHMIGUNGSBESCHEIDE.
    DEINE EINZIGE AUFGABE IST ES, EIN GÜLTIGES JSON-OBJEKT ZU ERSTELLEN.

    WICHTIGE GRUNDREGELN:
    1. WICHTIG FÜR DIE ZEIT: Wenn ein Wert nicht im Text steht, LASSE DAS GESAMTE FELD WEG (erzeuge keine leeren Schlüssel wie "").
    2. GIB GENAU EIN (1) ZUSAMMENHÄNGENDES JSON-OBJEKT ZURÜCK!
    3. KEINE SCHÄTZUNG.
    4. KEINE INTERPRETATION.

    -------------------------------------------------------
    KONTEXT (Das Dokument):
    {context}

    -------------------------------------------------------
    TEIL 1: METADATA ALLGEMEIN
     - "Titel Genehmigungsbescheid": Suche ganz am Anfang des bereitgestellten Textes nach der Markierung "--- DOKUMENT START: ... ---". Extrahiere exakt den dort genannten Dateinamen des PDFs (z.B. "1048_Genehmigungs_Cheine_03-2024.pdf" oder "124_Genehmigung_Klein_Dammerow_6WEA_05-2024.pdf") und trage ihn hier ein. Erfinde keinen eigenen Titel!
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

    RÜCKBAU VON ALTANLAGEN:
    - "Rückbau von Altanlagen (Ja/Nein)": Trage "Ja" ein, wenn im Text erwähnt wird, dass bestehende Anlagen/Altanlagen abgebaut, zurückgebaut oder demontiert werden (oft im Kontext von Repowering). Andernfalls trage "Nein" oder "" ein.
    - "Bezeichnung abgebauter Altanlagen": Wenn Altanlagen zurückgebaut werden, extrahiere hier EXAKT die Kennzeichnungen oder Namen dieser alten Anlagen (z.B. "Altanlage WEA01, WEA03"). Wenn keine spezifischen Namen genannt werden, schreibe "".

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
          "UTM 33 Koordinaten (Hochwert/N)": "",
          "Rückbau von Altanlagen (Ja/Nein)": "",
        "Bezeichnung abgebauter Altanlagen": ""
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
      }}
    """

    # ==========================================
    # PROMPT 2: FLÄCHEN
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

    0. WICHTIG: Lasse JSON-Schlüssel zwingend komplett weg, wenn du keinen Wert hast (keine leeren ""). Das spart Token!
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
    # PROMPT 3: Strom (ABSCHALTUNGEN)
    # ==========================================
    template_stroem = """
    DU BIST EIN HOCHPRÄZISER DATEN-ANALYST FÜR GENEHMIGUNGSBESCHEIDE.
    DEINE AUFGABE IST ES, ALLE BETRIEBSAUFLAGEN, ABSCHALTUNGEN UND TECHNISCHEN PARAMETER FÜR JEDE WINDKRAFTANLAGE (WEA) IN EIN KLARES FORMAT ZU ÜBERFÜHREN.
    GIB GENAU EIN GÜLTIGES JSON-OBJEKT ZURÜCK, DAS EIN ARRAY "4_Stroem" ENTHÄLT. FÜR JEDE WEA GIBT ES EIN EIGENES OBJEKT IM ARRAY.

    WICHTIGE GRUNDREGELN:
    1. WICHTIG FÜR DIE ZEIT: Wenn ein Wert nicht im Text steht, LASSE DEN SCHLÜSSEL KOMPLETT WEG (erzeuge keine Felder mit "").
    2. Trage bei (Ja/Nein)-Feldern exakt "Ja" oder "Nein" ein, WENN ES IM TEXT STEHT. Sonst lass das Feld weg.
    3. Keine erfundenen "Hilfsspalten" oder interne Notizen. Nur die vorgegebenen JSON-Schlüssel verwenden!
    4. Wenn Abschaltungen für "alle Anlagen" gelten, kopiere diese Werte bei jeder einzelnen WEA in das Array.
    5. TAGESZEITEN: Extrahiere Tageszeiten exakt wie im Text (z.B. "1 Stunde vor Sonnenuntergang bis Sonnenaufgang"). Kürze diese niemals!

    -------------------------------------------------------
    EXTRAKTIONSREGELN (SEHR WICHTIG)

    A) ARTENSCHUTZ: ABSCHALT-TYPEN (VÖGEL)
    Prüfe, welche grundsätzlichen Systeme gefordert werden und trage "Ja" ein:
    - Automatisches Antikollisisonssystem (AKS) Vögel
    - Manuelle / Phänologische Abschaltung Vögel (z.B. feste Kalenderzeiten)
    - Landwirtschaftliche Betriebsabschaltung Vögel (Summe) (Sobald Mahd, Pflug oder Ernte genannt wird = "Ja")
    - Abschaltung Mahd / Abschaltung Pflug / Abschaltung Ernte (jeweils "Ja", wenn explizit gefordert)
    - Abgeschalteter Bewirtschaftsungsraum: Trage hier nur die Meter-Zahl ein (z.B. "300"). Achte darauf, ob ab "Mast" oder ab "Rotor" gemessen wird und trage es in das entsprechende Feld ein.

    B) ARTENSCHUTZ: FLEDERMÄUSE (FM)
    - Fledermausart (FM): Wenn spezifische Arten genannt sind.
    - Zeiten, Wind, Temperatur, Niederschlag, Tageszeiten: Exakt aus dem Abschaltalgorithmus übernehmen.
    - Monitoring: Wird ein Fledermausmonitoring (Gondelmonitoring, Höhenmonitoring) gefordert? -> "Fledermausmonitoring (Ja, Nein)": "Ja".
    - Gibt es eine vorläufige Abschaltung, bis das Monitoring abgeschlossen ist? -> "Vorläufige Abschaltung Fledermausmonitoring (Ja, Nein)": "Ja".

    C) ARTENSCHUTZ: VÖGEL (WICHTIGSTE REGEL ZUR TRENNUNG)
    Wenn für verschiedene Vogelarten UNTERSCHIEDLICHE Auflagen oder Zeiträume gelten, DARFST DU DIESE NIEMALS VERMISCHEN! 
    Nutze für jede Vogelart einen eigenen Block:
    - Vogelart 1 (V1): z.B. Rotmilan -> Trage hierzu exakt die passenden V1-Zeiträume, Windgeschwindigkeiten etc. ein.
    - Vogelart 2 (V2): z.B. Baumfalke -> Trage hierzu exakt die passenden V2-Zeiträume ein.
    - Vogelart 3 (V3): Für weitere Arten.

    D) BAUZEITENREGELUNGEN
    - Baufeldräumung (Ja, Nein)
    - Baufeldräumung (Zeiten): z.B. "außerhalb der Brutzeit", "01.10.-28.02."

    E) SCHALL, SCHATTEN & BETRIEB (TECHNIK)
    - Schall-Betriebsregulation (Ja, Nein): Gibt es einen reduzierten Modus (SO-Modus)? -> "Ja".
    - Betriebsmodus nachts (Bezeichnung): Trage den Modus exakt ein (z.B. "Mode NO 106.0", "SO5").
    - SONDERFALL: Wenn eine Anlage nachts komplett abgestellt werden muss, trage "Außerbetriebsetzung" in den Betriebsmodus nachts ein. Prüfe dies für jede Anlage (insbesondere WEA 01) einzeln!
    - Geräuschpegelgrenzen (dB): Nur die reinen Zahlen tags und nachts.
    - Eiswurf-Abschaltung (Ja/Nein): "Ja", wenn Eiserkennungssystem vorhanden.
    - Schattenwurf-Abschaltung: "Ja" bei Summe, astronomisch oder meteorologisch, je nach Erwähnung (meist 30 Min/Tag, 8 Std/Jahr = astronomisch).
    - Abschaltung Turbulenzen (Ja, Nein): "Ja" bei Sektorabschaltungen wegen Windverwirbelungen.
    - Blattheizung (Ja/Nein)

    F) LUFTVERKEHR
    - Abschaltung durch Flight Manager (Radarabschaltung) (Ja/Nein): Wenn Bedarfsgesteuerte Nachtkennzeichnung (BNK) oder militärisches Radar zur Abschaltung führt.

    -------------------------------------------------------
    ERZWUNGENES OUTPUT-FORMAT (START MIT {{ UND ENDE MIT }}):
    {{
      "4_Stroem": [
        {{
          "Anlagen-Nr. / Kennzeichnung": "WEA 01",
          "Automatisches Antikollisisonssystem (AKS) Vögel": "",
          "Manuelle / Phänologische Abschaltung Vögel": "",
          "Landwirtschaftliche Betriebsabschaltung Vögel (Summe)": "",
          "Abschaltung Mahd": "",
          "Abschaltung Pflug": "",
          "Abschaltung Ernte": "",
          "Abgeschalteter Bewirtschaftsungsraum um WEA ab Mast (m)": "",
          "Abgeschalteter Bewirtschaftsungsraum um WEA ab Rotor (m)": "",
          "Fledermausart (FM)": "",
          "FM - Zeiträume (Datum von-bis)": "",
          "FM - Windgeschwindigkeit (< m/s)": "",
          "FM - Temperatur (> °C)": "",
          "FM - Niederschlag (Nd)": "",
          "FM - Tageszeiten": "",
          "Fledermausmonitoring (Ja, Nein)": "",
          "Vorläufige Abschaltung Fledermausmonitoring (Ja, Nein)": "",
          "Fledermausmonitoring (Zeiten)": "",
          "Vogelart 1 (V1)": "",
          "V1 - Zeiträume (Datum von-bis)": "",
          "V1 - Windgeschwindigkeit (< m/s)": "",
          "V1 - Niederschlag (Nd)": "",
          "V1 - Tageszeiten": "",
          "Vogelart 2 (V2)": "",
          "V2 - Zeiträume (Datum von-bis)": "",
          "V2 - Windgeschwindigkeit (< m/s)": "",
          "V2 - Niederschlag (Nd)": "",
          "V2 - Tageszeiten": "",
          "Vogelart 3 (V3)": "",
          "V3 - Zeiträume (Datum von-bis)": "",
          "V3 - Windgeschwindigkeit (< m/s)": "",
          "V3 - Niederschlag (Nd)": "",
          "V3 - Tageszeiten": "",
          "Baufeldräumung (Ja, Nein)": "",
          "Baufeldräumung (Zeiten)": "",
          "Schall-Betriebsregulation (Ja, Nein)": "",
          "Schall-Betriebsregulation (Zeiten)": "",
          "Schall-Abschaltung (Ja, Nein)": "",
          "Schall-Abschaltung (Zeiten)": "",
          "Vorläufige Schall-Betriebsregulation bis Monitoring (Ja, Nein)": "",
          "Vorläufige Schall-Betriebsabschaltung bis Monitoring (Ja, Nein)": "",
          "Geräuschpegelgrenzen (dB) tags/gesamt": "",
          "Geräuschpegelgrenzen (dB) nachts/gesamt": "",
          "Betriebsmodus tags (Bezeichnung)": "",
          "Betriebsmodus nachts (Bezeichnung)": "",
          "Blattheizung (Ja/Nein)": "",
          "Eiswurf-Abschaltung (Ja/Nein)": "",
          "Schattenwurf-Abschaltung Summe (Ja/Nein)": "",
          "Schattenwurf-Abschaltung astronomisch (Ja/Nein)": "",
          "Schattenwurf-Abschaltung meteorologisch (Ja/Nein)": "",
          "Abschaltung Turbulenzen (Ja, Nein)": "",
          "Abschaltung durch Flight Manager (Radarabschaltung) (Ja/Nein)": ""
        }}
      ]
    }}

    DOKUMENT (Gefilterte Textauszüge für Abschaltungen):
    {context}
    """

    
    # Modelle Setup
    llm = ChatMistralAI(model=model_name, temperature=0.0, timeout=300, max_retries=1, model_kwargs={"response_format": {"type": "json_object"}})
    
    chain_main = ChatPromptTemplate.from_template(template_main) | llm | StrOutputParser()
    chain_areas = ChatPromptTemplate.from_template(template_areas) | llm | StrOutputParser()
    chain_stroem = ChatPromptTemplate.from_template(template_stroem) | llm | StrOutputParser()
    
    def parse_llm_json(res_str):
        clean_str = res_str.replace("```json", "").replace("```", "").strip()
        start = clean_str.find("{")
        if start == -1: return None
        depth = 0
        in_string = False
        escape_next = False
        for i, ch in enumerate(clean_str[start:], start=start):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
            if not in_string:
                if ch == "{": depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(clean_str[start:i+1])
                        except json.JSONDecodeError:
                            return None
        return None

    def invoke_with_retry(chain, inputs, max_attempts=2):
        for attempt in range(max_attempts):
            try:
                return chain.invoke(inputs)
            except Exception as e:
                error_msg = str(e).lower()
                if "429" in error_msg or "rate limit" in error_msg or "capacity exceeded" in error_msg:
                    if attempt < max_attempts - 1:
                        status_text.warning(f"⚠️ Rate Limit (429) erkannt. Warte 10 Sek. (Versuch {attempt+1}/{max_attempts})...")
                        time.sleep(10)
                        status_text.info("Versuche erneut...")
                        continue
                raise e

    ki_timer_text = st.empty()
    ki_total_start = time.time()

    def update_ki_timer(label):
        elapsed = round(time.time() - ki_total_start, 0)
        ki_timer_text.caption(f"⏱ {label} — Verstrichene Zeit: {int(elapsed)} Sek.")

    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        status_text.info("⚡ Alle 3 Phasen werden parallel gestartet (mistral-large)...")
        update_ki_timer("Parallel Phase 1+2+3")

        def call_api(name, chain, ctx, max_retries=4, base_delay=5):
            # Versetzter Start (Jitter) verhindert, dass mistral-large alle 3 Limits in derselben Sekunde bricht
            if name == "areas": time.sleep(2.5)
            elif name == "stroem": time.sleep(5)

            for attempt in range(max_retries):
                try:
                    result = chain.invoke({"context": ctx})
                    return name, result
                except Exception as e:
                    err = str(e).lower()
                    if ("429" in err or "rate limit" in err or "capacity exceeded" in err) and attempt < max_retries - 1:
                        wait = base_delay * (2 ** attempt)
                        print(f"[{name}] Rate-Limit – warte {wait}s (Versuch {attempt+1}/{max_retries})")
                        time.sleep(wait)
                    else:
                        raise e

        results = {}
        errors  = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(call_api, "main",   chain_main,   context_window_main):   "main",
                executor.submit(call_api, "areas",  chain_areas,  context_window_areas):  "areas",
                executor.submit(call_api, "stroem", chain_stroem, context_window_stroem): "stroem",
            }
            done_count = 0
            for future in as_completed(futures):
                try:
                    name, result = future.result()
                    results[name] = result
                    done_count += 1
                    status_text.info(f"✅ {done_count}/3 Phasen fertig...")
                except Exception as exc:
                    errors[futures[future]] = exc

        if errors:
            raise list(errors.values())[0]

        res_main   = results.get("main", "")
        res_areas  = results.get("areas", "")
        res_stroem = results.get("stroem", "")

        json_main = parse_llm_json(res_main)
        if not json_main: return {}
        json_areas  = parse_llm_json(res_areas)  or {}
        json_stroem = parse_llm_json(res_stroem) or {"4_Stroem": []}

        status_text.info("Führe Daten zusammen und bereite Tabellen vor...")
        if "3_Flaechen" not in json_main: json_main["3_Flaechen"] = {}
        json_main["3_Flaechen"].update(json_areas)
        
        # NEU: Injected Metadata aus Dateinamen-Parsing UND gerettetem Text-AZ!
        if "1_MetaData_Allgemein" not in json_main: json_main["1_MetaData_Allgemein"] = {}
        for fname, meta in metadata_dict.items():
            
            # Wähle das beste Aktenzeichen aus (Text vor Dateiname)
            best_az = meta.get("Gerettetes_AZ_aus_Text", "")
            if not best_az:
                best_az = meta.get("Aktenzeichen (Az)", "")
                
            json_main["1_MetaData_Allgemein"].update({
                "Titel Genehmigungsbescheid": meta.get("Original_Dateiname", ""),
                "Typ": meta.get("Typ", ""),
                "Aktenzeichen (Az)": best_az, # Hier wird das gerettete eingesetzt!
                "Anlagenanzahl": meta.get("Anlagenanzahl", ""),
                "Gemarkung": meta.get("Gemarkung", ""),
                "Monat": meta.get("Monat", ""),
                "Jahr": meta.get("Jahr", "")
            })
            break 
        
        stroem_liste = json_stroem.get("4_Stroem", [])
        
        final_data = post_process_coordinates(json_main)
        final_data = restructure_and_calculate_data(final_data, stroem_liste)
        
        ki_total_sec = round(time.time() - ki_total_start, 1)
        ki_total_min = round(ki_total_sec / 60, 2)
        ki_avg_phase = round(ki_total_sec / 3, 1)
        
        ki_timer_text.empty()
        status_text.empty()
        st.success(f"✅ KI-Extraktion abgeschlossen! Gesamtdauer: **{ki_total_sec} Sek. ({ki_total_min} Min.)** | Ø pro Phase: **{ki_avg_phase} Sek.**")
        return final_data
        
    except Exception as e:
        ki_timer_text.empty()
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
    if "parsed_metadata" not in st.session_state: st.session_state.parsed_metadata = {}

    with st.sidebar:
        st.header("1. Upload")
        pdfs = st.file_uploader("PDFs hochladen", type="pdf", accept_multiple_files=True)

        if pdfs:
            for pdf_file in pdfs:
                if pdf_file.name not in st.session_state.parsed_metadata:
                    try:
                        meta = parse_filename_metadata(pdf_file.name)
                        st.session_state.parsed_metadata[pdf_file.name] = meta
                        st.info(f"Metadaten aus Dateiname: Az. {meta['Aktenzeichen (Az)']}, Gemarkung {meta['Gemarkung']}, {meta['Monat']}-{meta['Jahr']}")
                    except ValueError as e:
                        st.error(str(e))
                        st.stop()
                else:
                    st.success(f"✅ {pdf_file.name}")

        st.write("---")
        st.header("2. Text lokal auslesen")
        if st.button("Start Lokale OCR"):
            if not pdfs:
                st.error("Bitte Dokumente hochladen!")
                st.stop()

            with st.spinner("Lese Text aus PDFs...."):
                st.session_state.extracted_text = read_pdfs_tesseract(pdfs)

        st.write("---")
        st.header("3. Daten Extrahieren")

        model_choice = st.radio(
            "Modell auswählen:",
            options=["mistral-large-2512", "mistral-small-2503"],
            captions=["Höchste Genauigkeit", "Schneller & günstiger"],
            index=0,
        )

        if st.button("Start KI-Extraktion"):
            if not st.session_state.extracted_text:
                st.error("Bitte zuerst Text einlesen (Schritt 2)!")
                st.stop()

            st.session_state.full_result = extract_all_data(
                st.session_state.extracted_text,
                st.session_state.parsed_metadata,
                model_name=model_choice,
            )

    # --- ANZEIGE ---
    tab1, tab2 = st.tabs(["Ergebnis Dashboard", "Extrahierter Text (Tesseract)"])
    
    with tab1:
        if st.session_state.full_result:
            res = st.session_state.full_result
            
            st.header("Allgemeine Projektdaten")
            
            meta = res.get("1_MetaData_Allgemein", {})
            if not meta and res.get("2_WEA_Details"):
                meta = res["2_WEA_Details"][0].get("1_MetaData_Allgemein", {})
            
            def get_meta_val(key):
                val = meta.get(key, "")
                return val if val and str(val).strip() != "" else "-"
            
            col1, col2, col3 = st.columns(3)
            col1.metric("Aktenzeichen", get_meta_val("Aktenzeichen (Az)"))
            col2.metric("Genehmigungsdatum", get_meta_val("Genehmigungsdatum"))
            col3.metric("Vorhabenträger", get_meta_val("Vorhabenträger"))
            
            with st.expander("Weitere allgemeine Daten", expanded=False):
                display_meta = {k: (v if v and str(v).strip() != "" else "-") for k, v in meta.items()}
                meta_df = pd.DataFrame(list(display_meta.items()), columns=["Eigenschaft", "Wert"])
                st.dataframe(meta_df, hide_index=True, use_container_width=True)
            
            st.divider()

            st.header("Technische Anlagenübersicht")
            weas = res.get("2_WEA_Details", [])
            
            if weas:
                wea_names = [wea.get("2_Technik_Standort", {}).get("Anlagen-Nr. / Kennzeichnung", f"WEA {i+1}") for i, wea in enumerate(weas)]
                wea_tabs = st.tabs(wea_names)
                
                for i, wea_tab in enumerate(wea_tabs):
                    with wea_tab:
                        wea_data = weas[i]
                        tech = wea_data.get("2_Technik_Standort", {})
                        flaeche = wea_data.get("3_Flaechen_und_Abstaende", {})
                        stroem = wea_data.get("4_Stroem", {})
                        
                        c1, c2, c3 = st.columns(3)
                        
                        with c1:
                            st.subheader("Meta-Daten")
                            tech_clean = {k: v for k, v in tech.items() if v and str(v).strip() != ""}
                            if tech_clean:
                                tech_df = pd.DataFrame(list(tech_clean.items()), columns=["Eigenschaft", "Wert"])
                                st.dataframe(tech_df, hide_index=True, use_container_width=True)
                            else:
                                st.info("Keine technischen Spezifikationen gefunden.")
                                
                        with c2:
                            st.subheader("Flächen-Daten")
                            flaeche_clean = {k: v for k, v in flaeche.items() if v and str(v).strip() != ""}
                            if flaeche_clean:
                                flaeche_df = pd.DataFrame(list(flaeche_clean.items()), columns=["Eigenschaft", "Wert"])
                                st.dataframe(flaeche_df, hide_index=True, use_container_width=True)
                            else:
                                st.info("Keine Flächen- oder Abstandsangaben gefunden.")
                                
                        with c3:
                            st.subheader("Strom-Daten")
                            stroem_clean = {k: v for k, v in stroem.items() if v and str(v).strip() != ""}
                            if stroem_clean:
                                stroem_df = pd.DataFrame(list(stroem_clean.items()), columns=["Regel", "Wert"])
                                st.dataframe(stroem_df, hide_index=True, use_container_width=True)
                            else:
                                st.info("Keine spezifischen Betriebs-Regulationen gefunden.")

            st.divider()
            
            with st.expander("Rohdaten (JSON) für Datenbank / Export anzeigen"):
                st.code(json.dumps(st.session_state.full_result, indent=4, ensure_ascii=False), language="json")

        else:
            st.info("Bitte Dokumente hochladen und die Schritte 1 bis 3 ausführen.")

    with tab2:
        st.markdown(st.session_state.extracted_text)

if __name__ == "__main__":
    main()