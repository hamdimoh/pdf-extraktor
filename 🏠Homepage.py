import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from langchain_community.utilities import SQLDatabase
from htmlTemplates import css, bot_template, user_template, page_bg_img
import time

Base = declarative_base()


class ChatSession(Base):
    __tablename__ = 'chat_sessions'
    id = Column(Integer, primary_key=True)
    subject = Column(String(255), nullable=False)
    messages = relationship("ChatMessage", back_populates="session")


class ChatMessage(Base):
    __tablename__ = 'chat_messages'
    id = Column(Integer, primary_key=True)
    role = Column(String(10))  # "user" or "ai"
    content = Column(Text)
    session_id = Column(Integer, ForeignKey('chat_sessions.id'))
    session = relationship("ChatSession", back_populates="messages")


def connect_to_database():
    db_uri = "mysql+mysqlconnector://root:Alhamdimo1997@localhost:3306/HTW"
    engine = create_engine(db_uri)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return SQLDatabase.from_uri(db_uri), Session()


def create_new_chat_session(session, subject):
    new_session = ChatSession(subject=subject)
    session.add(new_session)
    session.commit()
    return new_session.id


def save_message(session, role, content, chat_session_id):
    message = ChatMessage(role=role, content=content, session_id=chat_session_id)
    session.add(message)
    session.commit()


def load_chat_history(session, chat_session_id):
    return session.query(ChatMessage).filter_by(session_id=chat_session_id).all()


def delete_all_messages(session, chat_session_id):
    session.query(ChatMessage).filter_by(session_id=chat_session_id).delete()
    session.commit()


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


def get_response(user_query, db, chat_history):
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


def generate_chat_subject(first_query, first_response):
    # Limit subject to max 6 words
    template = (
        "Erstelle ein kurzes und prägnantes Chat-Thema (max. 6 Wörter) basierend auf dem folgenden Text:\n"
        "User Input: {first_query}\n"
        "Response: {first_response}"
    )

    # Create a prompt using the template
    prompt = ChatPromptTemplate.from_template(template)

    # Initialize the model with desired parameters
    llm = ChatOpenAI(model="gpt-4o", temperature=0.5)

    # Create the chain to process the input and generate the chat subject
    chain = (
            prompt
            | llm
            | StrOutputParser()
    )

    # Invoke the chain with the correct variable names
    result = chain.invoke({
        "first_query": first_query,
        "first_response": first_response
    })

    return result


def main():
    load_dotenv()
    st.set_page_config(
        page_title="SIRIUS-HTW",
        page_icon=":rotating_light:",
    )

    # Initialize session state if not already set
    if 'selected_action' not in st.session_state:
        st.session_state.selected_action = None
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "db" not in st.session_state:
        st.session_state.db = None
        st.session_state.db_session = None
    if "current_chat_session" not in st.session_state:
        st.session_state.current_chat_session = None
    if "selected_chat" not in st.session_state:
        st.session_state.selected_chat = "Keine"

    st.write(css, unsafe_allow_html=True)
    st.write(page_bg_img, unsafe_allow_html=True)
    st.sidebar.title("Einstellungen")

    # Connect to the database if not already connected
    if 'db_connected' not in st.session_state:
        st.session_state.db, st.session_state.db_session = connect_to_database()
        st.session_state.db_connected = True

        success_placeholder = st.empty()
        success_placeholder.success("Datenbankverbindung hergestellt")
        time.sleep(1)
        success_placeholder.empty()
        st.write(bot_template.replace("{{MSG}}",
                                      "Hallo, ich bin Sirius, der Chatbot-Assistent der HTW Berlin. Ich kann Ihnen bei Fragen zur Hochschulverwaltung helfen. Wie kann ich Ihnen heute helfen?"),
                 unsafe_allow_html=True)

    # Handle the "New Chat" button click
    if st.sidebar.button("Neuer Chat"):
        st.session_state.selected_action = "New-Chat"
        st.session_state.current_chat_session = None
        st.session_state.chat_history = []  # Clear chat history
        st.session_state.selected_chat = None  # Reset selected chat to "None"
        st.rerun()  # Refresh the page to reflect the changes


    # List of previous chat sessions
    chat_sessions = st.session_state.db_session.query(ChatSession).all()
    chat_names = [session.subject for session in chat_sessions]
    selected_chat = st.sidebar.selectbox(
        "Frühere Chats", ["Keine"] + chat_names, key="chat_selector"
    )

    # Update session state with the selected chat
    st.session_state.selected_chat = selected_chat

    if st.session_state.selected_chat != "Keine":
        selected_session = next(
            session for session in chat_sessions if session.subject == st.session_state.selected_chat)
        if st.session_state.current_chat_session != selected_session.id:
            st.session_state.current_chat_session = selected_session.id
            st.session_state.chat_history = []
            chat_messages = load_chat_history(st.session_state.db_session, st.session_state.current_chat_session)

            for msg in chat_messages:
                role = AIMessage if msg.role == "ai" else HumanMessage
                st.session_state.chat_history.append(role(content=msg.content))

            st.success(f"Chat history for '{st.session_state.selected_chat}' has been loaded!")

    delete_chat = st.sidebar.selectbox("Chat löschen", ["Keine"] + [session.subject for session in chat_sessions],
                                       key="delete_chat_selector")

    if delete_chat != "Keine":
        delete_session = next(session for session in chat_sessions if session.subject == delete_chat)

        st.session_state.chat_history = []
        chat_messages = load_chat_history(st.session_state.db_session, delete_session.id)
        for msg in chat_messages:
            role = AIMessage if msg.role == "ai" else HumanMessage
            st.session_state.chat_history.append(role(content=msg.content))

        st.warning(f"Chat '{delete_chat}' befindet sich im Löschmodus. Weitere Interaktionen sind nicht erlaubt.")

        for message in st.session_state.chat_history:
            if isinstance(message, HumanMessage):
                st.write(user_template.replace("{{MSG}}", message.content), unsafe_allow_html=True)
            elif isinstance(message, AIMessage):
                st.write(bot_template.replace("{{MSG}}", message.content), unsafe_allow_html=True)

        user_query = None

        if st.sidebar.button("Ausgewählten Chat löschen"):
            with st.spinner("Chat wird gelöscht ..."):
                delete_all_messages(st.session_state.db_session, delete_session.id)
                st.session_state.db_session.query(ChatSession).filter_by(id=delete_session.id).delete()
                st.session_state.db_session.commit()

                st.session_state.chat_history = []
                st.rerun()

    else:
        user_query = st.chat_input("Message Siruis-Chatbot")

        if user_query and st.session_state.db_session is None:
            st.warning("Bitte klicken Sie auf „Verbinden“, bevor Sie den Chat starten.")
        elif user_query and user_query.strip() != "":
            if st.session_state.current_chat_session is None:
                st.session_state.chat_history.append(HumanMessage(content=user_query))

                with st.spinner("Antwort wird generiert..."):
                    first_response = get_response(user_query, st.session_state.db, "")
                    st.session_state.chat_history.append(AIMessage(content=first_response))

                    chat_subject = generate_chat_subject(user_query, first_response)
                    st.success(f"Neue Chat '{chat_subject}' gestartet.")
                    st.session_state.current_chat_session = create_new_chat_session(st.session_state.db_session,
                                                                                    chat_subject)
                    save_message(st.session_state.db_session, "user", user_query, st.session_state.current_chat_session)
                    save_message(st.session_state.db_session, "ai", first_response,
                                 st.session_state.current_chat_session)

            else:
                st.session_state.chat_history.append(HumanMessage(content=user_query))
                save_message(st.session_state.db_session, "user", user_query, st.session_state.current_chat_session)

                with st.spinner("Antwort wird generiert..."):
                    chat_history_text = "\n".join([
                        f"User: {msg.content}" if isinstance(msg, HumanMessage) else f"AI: {msg.content}"
                        for msg in st.session_state.chat_history
                    ])

                    response = get_response(user_query, st.session_state.db, chat_history_text)
                    st.session_state.chat_history.append(AIMessage(content=response))
                    save_message(st.session_state.db_session, "ai", response, st.session_state.current_chat_session)

    # Display the chat history
    for message in st.session_state.chat_history:
        if isinstance(message, HumanMessage):
            st.write(user_template.replace("{{MSG}}", message.content), unsafe_allow_html=True)
        elif isinstance(message, AIMessage):
            st.write(bot_template.replace("{{MSG}}", message.content), unsafe_allow_html=True)


if __name__ == "__main__":
    main()
