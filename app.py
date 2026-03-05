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

# Sicherer Check für MISTRAL_API_KEY (verhindert Absturz auf dem Mac)
mistral_key = os.getenv("MISTRAL_API_KEY")

try:
    if "MISTRAL_API_KEY" in st.secrets:
        mistral_key = st.secrets["MISTRAL_API_KEY"]
except Exception:
    pass # Lokal auf dem Mac gibt es keine Secrets, das ist okay

if mistral_key:
    os.environ["MISTRAL_API_KEY"] = mistral_key
else:
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
            wea_flaechen = dict(flaechen_global) 
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
    last_added_idx = -10000
    for idx in area_matches:
        if idx - last_added_idx < 1000: continue
        snippet = text[max(0, idx - 600):min(len(text), idx + 600)]
        if re.search(r'(?i)m²|m2|ha|hektar|quadratmeter|km2|km²', snippet):
            context_window_areas += text[max(0, idx - 1000):min(len(text), idx + 1000)] + "\n...\n"
            last_added_idx = idx

    template_main = """[DEIN KOMPLETTER LANGER PROMPT 1 HIER]""" 
    template_areas = """[DEIN KOMPLETTER LANGER PROMPT 2 HIER]"""

    llm = ChatMistralAI(model="mistral-large-2411", temperature=0.0, timeout=300, max_retries=2)
    chain_main = ChatPromptTemplate.from_template(template_main) | llm | StrOutputParser()
    chain_areas = ChatPromptTemplate.from_template(template_areas) | llm | StrOutputParser()
    
    def parse_llm_json(res_str):
        clean_str = res_str.replace("```json", "").replace("```", "").strip()
        match = re.search(r"\{.*\}", clean_str, re.DOTALL)
        if match: return json.loads(match.group(0))
        return None

    try:
        status_text.info("🧠 Phase 1/2: Metadaten...")
        json_main = parse_llm_json(chain_main.invoke({"context": context_window_main}))
        time.sleep(2)
        status_text.info("🎯 Phase 2/2: Flächen...")
        json_areas = parse_llm_json(chain_areas.invoke({"context": context_window_areas}))
        
        if "3_Flaechen" not in json_main: json_main["3_Flaechen"] = {}
        json_main["3_Flaechen"].update(json_areas)
        
        final_data = post_process_coordinates(json_main)
        final_data = restructure_and_calculate_data(final_data)
        status_text.success("✅ Fertig!")
        return final_data
    except Exception as e:
        st.error(f"Fehler: {e}")
        return {}

# ---------------- MAIN UI ----------------
def main():
    # HIER war der Fehler: Das doppelte set_page_config wurde entfernt!
    st.title("PDF Extraktor")

    if "full_result" not in st.session_state: st.session_state.full_result = {}
    if "extracted_text" not in st.session_state: st.session_state.extracted_text = ""

    with st.sidebar:
        st.header("1. Upload")
        pdfs = st.file_uploader("PDFs hochladen", type="pdf", accept_multiple_files=True)
        st.write("---")
        if st.button(" Start Lokale OCR"):
            if pdfs:
                with st.spinner("Lese Text...."):
                    st.session_state.extracted_text = read_pdfs_tesseract(pdfs)
        st.write("---")
        if st.button(" Start KI-Extraktion"):
            if st.session_state.extracted_text:
                st.session_state.full_result = extract_all_data(st.session_state.extracted_text)

    tab1, tab2 = st.tabs(["📊 Ergebnis & JSON", "📝 Extrahierter Text"])
    with tab1:
        if st.session_state.full_result:
            st.json(st.session_state.full_result)
    with tab2:
        st.markdown(st.session_state.extracted_text)

if __name__ == "__main__":
    main()