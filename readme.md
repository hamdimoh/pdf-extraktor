# ⚡ Mistral Local OCR – Windows Terminal Setup (README)

Diese Anleitung richtet das Projekt vollständig über das Windows Terminal (PowerShell) ein.  
Keine manuellen Downloads. Keine Installationsfenster. Alles reproduzierbar per Kommandozeile.

Wir verwenden **winget** – vergleichbar mit Homebrew auf macOS.

---

## ⚠️ WICHTIG: PowerShell als Administrator starten

Für die Installation der Systemprogramme sind Administratorrechte erforderlich.

1. Windows-Taste drücken  
2. `PowerShell` eingeben  
3. **Als Administrator ausführen** auswählen  

---

# SCHRITT 1 – Skriptausführung erlauben & C++ Runtime installieren

Windows blockiert standardmäßig Skripte.  
Zusätzlich benötigt Poppler die Visual C++ Runtime.

Kopiere diesen Block vollständig ins Terminal:

```powershell
# 1. Skriptausführung erlauben
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force

# 2. Visual C++ Redistributable installieren (WICHTIG für Poppler)
winget install Microsoft.VCRedist.2015+.x64 --accept-package-agreements --accept-source-agreements
```

---

# SCHRITT 2 – Tesseract OCR + Deutsches Sprachpaket

Installation von Tesseract sowie Download des deutschen Sprachmodells (`deu.traineddata`).

```powershell
# 1. Tesseract installieren
winget install -e --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements

# 2. Deutsches Sprachpaket herunterladen
$tessdata = "C:\Program Files\Tesseract-OCR\tessdata"
Invoke-WebRequest -Uri "https://github.com/tesseract-ocr/tessdata/raw/main/deu.traineddata" -OutFile "$tessdata\deu.traineddata"

Write-Host "Tesseract + Deutsch installiert." -ForegroundColor Green
```

---

# SCHRITT 3 – Poppler installieren

Poppler wird als ZIP geladen und in  
`C:\Users\<USERNAME>\poppler` installiert.

```powershell
# Zielordner definieren
$dest = "$env:USERPROFILE\poppler"
$url = "https://github.com/oschwartz10612/poppler-windows/releases/download/v24.02.0-0/Release-24.02.0-0.zip"
$zip = "$env:TEMP\pop.zip"

# Download
Write-Host "Lade Poppler herunter..." -ForegroundColor Cyan
Invoke-WebRequest $url -OutFile $zip

# Vorherige Installation entfernen (falls vorhanden)
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }

# Entpacken
Write-Host "Entpacke Poppler..." -ForegroundColor Cyan
Expand-Archive $zip -DestinationPath $dest -Force

# Cleanup
Remove-Item $zip

Write-Host "Poppler erfolgreich installiert." -ForegroundColor Green
```

---

# SCHRITT 4 – Python-Umgebung einrichten

Ab hier sind **keine Administratorrechte mehr nötig**.  
Terminal im Projektordner öffnen (z. B. VS Code Terminal).

⚠️ Verwende **Python 3.11**  
Python 3.14 (Beta) verursacht Fehler mit LangChain und Pydantic.

```powershell
# 1. Virtuelle Umgebung erstellen
python -m venv venv

# 2. Umgebung aktivieren
.\venv\Scripts\activate

# 3. Pip aktualisieren
python -m pip install --upgrade pip

# 4. Projekt-Abhängigkeiten installieren
pip install -r requirements.txt

# 5. Fehlende Koordinaten-Bibliothek nachinstallieren
pip install pyproj
```

Wenn alles korrekt ist, erscheint `(venv)` am Anfang der Terminalzeile.

---

# SCHRITT 5 – API-Key setzen & Anwendung starten

1. Datei `.env` im Projektordner erstellen  
2. Inhalt einfügen:

```
MISTRAL_API_KEY=dein_schluessel_hier
```

3. Anwendung starten:

```powershell
streamlit run app.py
```

---

## Ergebnis

✔ Vollständig terminalbasiertes Setup  
✔ Reproduzierbare Installation  
✔ Keine manuellen Downloads erforderlich  

---

Falls gewünscht, kann eine optimierte `requirements.txt` erstellt werden, die alle Abhängigkeiten versionsstabil definiert.