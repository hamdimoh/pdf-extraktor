import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from langchain_community.utilities import SQLDatabase
from htmlTemplates import css, bot_template, user_template, page_bg_img



Base = declarative_base()

# Chat message model
class ChatMessage(Base):
    __tablename__ = 'chat_messages'
    id = Column(Integer, primary_key=True)
    role = Column(String(10))  # "user" or "ai"
    content = Column(Text)

# Function to initialize database connection using SQLAlchemy
def connect_to_database():
    db_uri = "mysql+mysqlconnector://root:Alhamdimo1997@localhost:3306/HTW"
    engine = create_engine(db_uri)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return SQLDatabase.from_uri(db_uri), Session()

# Function to save chat message to the database
def save_message(session, role, content):
    message = ChatMessage(role=role, content=content)
    session.add(message)
    session.commit()

# Function to load chat history from the database
def load_chat_history(session):
    return session.query(ChatMessage).all()

# Function to delete all chat messages from the database
def delete_all_messages(session):
    session.query(ChatMessage).delete()
    session.commit()
    return "Chat history deleted."

# Function to get SQL chain for querying
def get_sql_chain(db):
    template = """
    Du bist Sirius-HTW, ein Botassistent für die Studierenden an der HTW Berlin. Du interagierst mit einem Studenten der HTW, der Fragen zur HTW-Verwaltung hat, beispielsweise zu Dozenten, Veranstaltungen, Terminen und Studiengangsinformationen an der HTW.
    Basierend auf dem unten stehenden Tabellenschema, schreibe eine SQL-Abfrage, die die Frage des Benutzers beantwortet. Berücksichtige den Gesprächsverlauf.

    <SCHEMA>{schema}</SCHEMA>

    Gesprächsverlauf: {chat_history}

    Schreibe nur die SQL-Abfrage und nichts anderes. Umrahme die SQL-Abfrage nicht mit zusätzlichem Text, auch nicht mit Backticks.

    Zum Beispiel:
    Frage: Wie lautet die E-Mail-Adresse von Prof. Cristab Fritsch?
    SQL-Abfrage: SELECT Email FROM Professoren WHERE Name = 'Cristab Fritsch';

    Frage: Wann ist der Bewerbungszeitraum für das Sommersemester 2024?
    SQL-Abfrage: SELECT Bewerbungszeitraum FROM Semester WHERE Jahr = 2024 AND Typ = 'Sommer';

    Dein Zug:

    Frage: {question}
    SQL-Abfrage:
    """

    prompt = ChatPromptTemplate.from_template(template)

    llm = ChatOpenAI(model="gpt-4o", temperature=0.5)

    def get_schema(_):
        return db.get_table_info()

    return (
            RunnablePassthrough.assign(schema=get_schema)
            | prompt
            | llm
            | StrOutputParser()
    )

# Function to generate response based on user query
def get_response(user_query: str, db: SQLDatabase, chat_history: list):
    sql_chain = get_sql_chain(db)

    template = """
    Du bist Sirius-HTW, ein Botassistent für die Studierenden an der HTW Berlin. Du interagierst mit einem Studenten der HTW, der Fragen zur HTW-Verwaltung hat, beispielsweise zu Dozenten, Veranstaltungen, Terminen und Studiengangsinformationen an der HTW.
    Basierend auf dem unten stehenden Tabellenschema, der Frage, der SQL-Abfrage und der SQL-Antwort, schreibe eine Antwort in natürlicher Sprache.
    <SCHEMA>{schema}</SCHEMA>

    Gesprächsverlauf: {chat_history}
    SQL-Abfrage: <SQL>{query}</SQL>
    Benutzerfrage: {question}
    SQL-Antwort: {response}
    """

    prompt = ChatPromptTemplate.from_template(template)

    llm = ChatOpenAI(model="gpt-4o", temperature=0.5)

    chain = (
            RunnablePassthrough.assign(query=sql_chain).assign(
                schema=lambda _: db.get_table_info(),
                response=lambda vars: db.run(vars["query"]),
            )
            | prompt
            | llm
            | StrOutputParser()
    )

    return chain.invoke({
        "question": user_query,
        "chat_history": chat_history,
    })

# Main Streamlit app
def main():
    load_dotenv()
    st.set_page_config(
        page_title="SIRIUS-HTW",
        page_icon=":rotating_light:",  # Icon für die Seite (Beispiel)
    )

    st.write(css, unsafe_allow_html=True)
    st.write(page_bg_img, unsafe_allow_html=True)
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    if "db" not in st.session_state:
        st.session_state.db = None
        st.session_state.db_session = None
        st.info("Bitte klicken Sie auf 'Verbinden', um den Chat zu starten")

    st.sidebar.title("Einstellungen")

    # Connect to database button
    if st.sidebar.button(":blue[Verbinden]", key="connect_button"):
        with st.spinner("Die Verbindung wird hergestellt..."):
            st.session_state.db, st.session_state.db_session = connect_to_database()
            st.success("Die Verbindung wurde hergestellt!")
            st.write(bot_template.replace("{{MSG}}", "Hallo, ich bin Sirius, der Chatbot-Assistent der HTW Berlin. Ich kann Ihnen bei Fragen zur Hochschulverwaltung helfen. Wie kann ich Ihnen heute helfen?"), unsafe_allow_html=True)

    # Dropdown-Menü für Chat-Historie Aktionen
    selected_action = st.sidebar.selectbox(
        " ",
        ["Chat-Historie", "Chat-Laden", "Chat-Löschen"],
        key="dropdown_actions", label_visibility="collapsed"
    )

    # Aktionen basierend auf Dropdown-Auswahl ausführen
    if selected_action == "Chat-Laden":
        if st.session_state.db:
            with st.spinner("Lade Chat-Historie..."):
                st.session_state.chat_history = []
                chat_messages = load_chat_history(st.session_state.db_session)
                if not chat_messages:
                    st.warning("Die Chat-Historie ist leer.")
                else:
                    for msg in chat_messages:
                        role = AIMessage if msg.role == "ai" else HumanMessage
                        st.session_state.chat_history.append(role(content=msg.content))
                    st.success("Chat-Historie wurde geladen!")
        else:
            st.warning("Bitte klicken Sie zuerst auf den Verbinden-Button.")

    elif selected_action == "Chat-Löschen":
        if st.session_state.db:
            with st.spinner("Lösche Chat-Historie..."):
                delete_all_messages(st.session_state.db_session)
                st.session_state.chat_history = []
                st.success("Chat-Historie erfolgreich gelöscht!")
        else:
            st.warning("Bitte klicken Sie zuerst auf den Verbinden-Button.")

    # Display chat messages
    for message in st.session_state.chat_history:
        if isinstance(message, AIMessage):
            st.write(bot_template.replace("{{MSG}}", message.content), unsafe_allow_html=True)
        elif isinstance(message, HumanMessage):
            st.write(user_template.replace("{{MSG}}", message.content), unsafe_allow_html=True)

    # User input
    user_query = st.chat_input("Message Siruis-Chatbot")
    if user_query and st.session_state.db_session is None:
        st.warning("Bitte klicken Sie auf 'Verbinden', bevor Sie mit dem Chat beginnen.")
    elif user_query and user_query.strip() != "":
        st.session_state.chat_history.append(HumanMessage(content=user_query))
        save_message(st.session_state.db_session, "user", user_query)
        st.write(user_template.replace("{{MSG}}", user_query), unsafe_allow_html=True)

        # Process user query and generate response
        with st.spinner("Verarbeitung..."):
            response = get_response(user_query, st.session_state.db, st.session_state.chat_history)
            st.session_state.chat_history.append(AIMessage(content=response))
            save_message(st.session_state.db_session, "ai", response)
            st.write(bot_template.replace("{{MSG}}", response), unsafe_allow_html=True)



if __name__ == "__main__":
    main()
