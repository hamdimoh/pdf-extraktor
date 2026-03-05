import streamlit as st
import os

st.set_page_config(page_title="Debug Test")
st.title("Test-Modus")

st.write("Wenn du das liest, läuft der Server!")

if "MISTRAL_API_KEY" in st.secrets:
    st.success("API Key in Secrets gefunden!")
else:
    st.error("API Key fehlt in den Secrets!")

st.write("Installierte Pakete werden geprüft...")
try:
    import pytesseract
    st.success("Pytesseract Bibliothek ist da!")
except:
    st.error("Pytesseract fehlt!")