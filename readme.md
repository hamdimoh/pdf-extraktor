# Sirius-Chatbot für HTW-Studierende

Willkommen im Sirius-Chatbot-Repository! Dieses Projekt wurde entwickelt, um einen natürlichen Sprach-SQL-Chatbot zur Informationsverwaltung an der HTW zu erstellen und die Verarbeitung von PDFs und URLs mithilfe von LLMs zu ermöglichen. Es ist Teil meiner Bachelorarbeit und nutzt das GPT-4o-Modell von OpenAI, das in eine Streamlit-GUI integriert wurde, um eine optimierte Benutzerinteraktion zu gewährleisten.

## 1. Homepage
Die Homepage-Seite der Chatbot-Anwendung bietet eine intuitive Benutzeroberfläche für Studierende der HTW Berlin, um administrative Informationen abzurufen und mit einem intelligenten Chatbot zu interagieren. Durch die Integration von LangChain, SQLAlchemy, Dotenv und OpenAI entsteht eine leistungsstarke Plattform, die eine effektive Verwaltungskommunikation unterstützt.

## Funktionen 
- **Natural Language Processing**: Verwendet GPT-4o zur Interpretation und Beantwortung von Benutzeranfragen in natürlicher Sprache.
- **SQL-Abfrage-Erstellung**: Dynamische Erstellung von SQL-Abfragen basierend auf den Eingaben in natürlicher Sprache.
- **Datenbankinteraktion**: Verbindung zu einer SQL-Datenbank zur Abfrage von Ergebnissen und zur Demonstration praktischer Datenbankinteraktionen.
- **Streamlit GUI**: Benutzerfreundliche Oberfläche, entwickelt mit Streamlit, die eine einfache Nutzung für alle Fähigkeitsstufen ermöglicht.
- **Python-based**: Vollständig in Python entwickelt, unter Verwendung moderner Technologien und bewährter Praktiken in der Softwareentwicklung.
- **Chatverlauf Laden:**:Möglichkeit, den Chatverlauf aus einer Datei zu laden und anzuzeigen, um frühere Konversationen nachzuvollziehen.
- **Chatverlauf Löschen:**:Funktion zum Löschen des Chatverlaufs, um die gespeicherten Daten zu entfernen und den Chat zu bereinigen.

## 2. PDFs und URLs
Die Seite für PDFs und URLs demonstriert die Implementierung einer Funktionalität, die es Benutzern ermöglicht, Dokumente hochzuladen oder URLs einzugeben, die dann analysiert werden, um relevante Informationen zu extrahieren. Diese Implementierung stellt sicher, dass der Chatbot auf Basis dieser Informationen intelligente Antworten generieren kann.

## Funktionen für PDFs
- **PDF-Laden**: Die Anwendung kann mehrere PDF-Dokumente einlesen und deren Textinhalte extrahieren.
- **Textaufteilung**: Der extrahierte Text wird in kleinere Abschnitte unterteilt, die effektiv verarbeitet werden können.
- **Sprachmodell**: Die Anwendung verwendet ein Sprachmodell zur Erstellung von Vektor-Repräsentationen (Embeddings) der Textabschnitte.
- **Ähnlichkeitsabgleich**: Bei einer Anfrage vergleicht die App die Frage mit den Textabschnitten und identifiziert die semantisch ähnlichsten.
- **Antwortgenerierung**: Die ausgewählten Abschnitte werden an das Sprachmodell übergeben, das eine Antwort basierend auf dem relevanten Inhalt der PDFs erstellt.
## Funktionen für URLs
- **URL-Verarbeitung**: Die Anwendung kann den Inhalt von angegebenen URLs laden und analysieren.
- **Textextraktion**: Aus den geladenen Webseiten wird der Textinhalt extrahiert, um nützliche Informationen zu gewinnen.
- **Textaufteilung**: Der extrahierte Text wird in kleinere Abschnitte unterteilt, die für die Verarbeitung durch das Sprachmodell vorbereitet werden.
- **Ähnlichkeitsabgleich**: Die Anwendung vergleicht Anfragen mit den extrahierten Textabschnitten von URLs und identifiziert relevante Informationen.
- **Antwortgenerierung**: Basierend auf dem relevanten Inhalt der URLs wird eine passende Antwort durch das Sprachmodell erstellt.

## Installation
Stellen Sie sicher, dass Python 3.9 auf Ihrem System installiert ist, und klonen Sie dieses Repository.



Installieren Sie die benötigten Pakete:

```bash
pip install -r requirements.txt
```

Erstellen Sie eine .env-Datei im Hauptverzeichnis des Projekts mit den erforderlichen Variablen. Fügen Sie insbesondere Ihren OpenAI API-Schlüssel hinzu.

```
OPENAI_API_KEY=[your-openai-api-key]
```

## Nutzung

Um die Streamlit-Anwendung zu starten und mit dem Chatbot zu interagieren, führen Sie den folgenden Befehl aus:

```bash
streamlit run 🏠Homepage.py
```

## Datenbankverbindung

Um die Verbindung zur Datenbank herzustellen, müssen in zwei Dateien Anpassungen vorgenommen werden:

**In der  🏠Homepage.py.py-Datei:**
- Öffnen Sie die Datei und suchen Sie die connect_to_database-Funktion.
- Passen Sie die db_uri-Variable in Zeile 34 an:
```
db_uri = "mysql+mysqlconnector://user:password@host:port/database"
```

**In der 📚PDFS & URL.py-Datei:**
- Öffnen Sie die Datei und suchen Sie die connect_to_database-Funktion.
- Passen Sie die db_uri-Variable in Zeile 134 an:
```
db_uri = "mysql+mysqlconnector://user:password@host:port/database"
```
Ersetzen Sie user, password, host, port und database durch Ihre tatsächlichen Datenbank-Zugangsdaten.

## Lizenz
Dieses Projekt ist unter der MIT-Lizenz lizenziert. Weitere Einzelheiten findest du in der LICENSE-Datei.