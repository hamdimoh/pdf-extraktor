import streamlit as st

st.set_page_config("Text Inspektor", "🔍", layout="wide")

st.title("🔍 Text-Inspektor")
st.markdown("Hier siehst du, was **pdfplumber** erkannt hat.")

# Prüfen, ob Daten da sind
if "extracted_text" not in st.session_state or not st.session_state.extracted_text:
    st.warning("⚠️ Es wurde noch kein Text geladen.")
    st.info("Bitte gehe zurück zur Hauptseite ('app') und lade ein PDF hoch.")
else:
    # Statistik
    text_len = len(st.session_state.extracted_text)
    st.metric("Zeichen gesamt", f"{text_len:,}")

    # Text Anzeige
    st.text_area(
        label="Extrahierter Inhalt:",
        value=st.session_state.extracted_text,
        height=800, # Extra groß
        disabled=True
    )