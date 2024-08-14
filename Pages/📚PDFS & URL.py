
import streamlit as st
from dotenv import load_dotenv
from PyPDF2 import PdfReader
from langchain.text_splitter import CharacterTextSplitter, RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS, Chroma
from langchain_openai import ChatOpenAI
from langchain.memory import ConversationBufferMemory
from langchain.chains import ConversationalRetrievalChain, create_history_aware_retriever, create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_openai import OpenAIEmbeddings
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, HumanMessage
from langchain_community.document_loaders import WebBaseLoader
from htmlTemplates import css, bot_template, user_template, sidebar, page_bg_img
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey
from langchain_community.utilities import SQLDatabase

# Database setup
Base = declarative_base()
load_dotenv()

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

# PDF Handling Functions
def get_pdf_text(pdf_docs):
    text = ""
    for pdf in pdf_docs:
        pdf_reader = PdfReader(pdf)
        for page in pdf_reader.pages:
            text += page.extract_text()
    return text

def get_text_chunks(text):
    text_splitter = CharacterTextSplitter(
        separator="\n",
        chunk_size=1000,
        chunk_overlap=200,
        length_function=len
    )
    chunks = text_splitter.split_text(text)
    return chunks

def get_vectorstore_from_text(text_chunks):
    embeddings = OpenAIEmbeddings()
    vectorstore = FAISS.from_texts(texts=text_chunks, embedding=embeddings)
    return vectorstore

def get_vectorstore_from_url(url):
    loader = WebBaseLoader(url)
    document = loader.load()
    text_splitter = RecursiveCharacterTextSplitter()
    document_chunks = text_splitter.split_documents(document)
    vector_store = Chroma.from_documents(document_chunks, OpenAIEmbeddings())
    return vector_store

# Chat Handling Functions
def get_conversation_chain(vectorstore):
    llm = ChatOpenAI(model="gpt-4o", temperature=0.5, streaming=True)
    memory = ConversationBufferMemory(
        memory_key='chat_history', return_messages=True)
    conversation_chain = ConversationalRetrievalChain.from_llm(
        llm=llm,
        retriever=vectorstore.as_retriever(),
        memory=memory
    )
    return conversation_chain

def get_context_retriever_chain(vector_store):
    llm = ChatOpenAI(model="gpt-4o", temperature=0.5, streaming=True)
    retriever = vector_store.as_retriever()
    prompt = ChatPromptTemplate.from_messages([
        MessagesPlaceholder(variable_name="chat_history"),
        ("user", "{input}"),
        ("user",
         "Angesichts des oben geführten Gesprächs erstellen Sie eine Suchanfrage, um relevante Informationen zu erhalten.")
    ])
    retriever_chain = create_history_aware_retriever(llm, retriever, prompt)
    return retriever_chain

def get_conversational_rag_chain(retriever_chain):
    llm = ChatOpenAI(model="gpt-4o", temperature=0.5, streaming=True)
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Bitte geben Sie die spezifischen Informationen oder den Kontext an, den ich verwenden soll, um die Fragen zu beantworten:\n\n{context}"),
        MessagesPlaceholder(variable_name="chat_history"),
        ("user", "{input}"),
    ])
    stuff_documents_chain = create_stuff_documents_chain(llm, prompt)
    return create_retrieval_chain(retriever_chain, stuff_documents_chain)

def delete_all_messages(session, chat_session_id):
    session.query(ChatMessage).filter_by(session_id=chat_session_id).delete()
    session.commit()

def handle_userinput(user_input, session, chat_session_id):
    if st.session_state.search_mode is None:
        st.warning("Bitte laden Sie PDFs hoch oder geben Sie eine Website-URL ein, bevor Sie mit dem Chat beginnen.")
        return

    response = None
    if st.session_state.search_mode == 'pdf' and st.session_state.conversation:
        response = st.session_state.conversation.invoke({'question': user_input})
        st.session_state.chat_history.extend(response['chat_history'])
    elif st.session_state.search_mode == 'url' and st.session_state.rag_chain:
        retriever_chain = get_context_retriever_chain(st.session_state.vector_store)
        conversation_rag_chain = get_conversational_rag_chain(retriever_chain)
        response = conversation_rag_chain.invoke({
            "chat_history": st.session_state.chat_history,
            "input": user_input
        })

        st.session_state.chat_history.append(HumanMessage(content=user_input))
        st.session_state.chat_history.append(AIMessage(content=response['answer']))

    # Save messages to the database
    save_message(session, "user", user_input, chat_session_id)
    if response:
        save_message(session, "ai", response['answer'], chat_session_id)

def connect_to_database():
    db_uri = "mysql+mysqlconnector://root:Alhamdimo1997@localhost:3306/HTW_chathistory_psds"
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


def main():
    load_dotenv()
    st.set_page_config(page_title="PDFs & Webseiten Chat", page_icon=":books:")
    st.write(css, unsafe_allow_html=True)
    st.write(page_bg_img, unsafe_allow_html=True)
    st.sidebar.title("Einstellungen")

    # Initialize session state
    if "conversation" not in st.session_state:
        st.session_state.conversation = None

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    if "vector_store" not in st.session_state:
        st.session_state.vector_store = None

    if "rag_chain" not in st.session_state:
        st.session_state.rag_chain = None

    if "search_mode" not in st.session_state:
        st.session_state.search_mode = None

    if "show_chat_options" not in st.session_state:
        st.session_state.show_chat_options = False

    if "chat_session_id" not in st.session_state:
        st.session_state.chat_session_id = None

    if "chat_name_displayed" not in st.session_state:
        st.session_state.chat_name_displayed = False

    if "selected_chat" not in st.session_state:
        st.session_state.selected_chat = None

    if "show_history" not in st.session_state:
        st.session_state.show_history = False

    if "active_sidebar_section" not in st.session_state:
        st.session_state.active_sidebar_section = None  # None, 'chat_history', or 'start_chat'

    # Connect to database
    db, db_session = connect_to_database()

    with st.sidebar:
        st.markdown(sidebar, unsafe_allow_html=True)

        # Sidebar buttons
        if st.button("Chat-Verlauf"):
            st.session_state.active_sidebar_section = 'chat_history'
            st.session_state.chat_name_displayed = False  # Clear the chat name when switching to history


        if st.button("Chat mit PDF oder URL starten"):
            st.session_state.active_sidebar_section = 'start_chat'
            st.session_state.chat_name_displayed = False  # Clear the chat name when switching to start chat

        if st.session_state.active_sidebar_section == 'chat_history':
            chat_sessions = db_session.query(ChatSession).all()
            chat_names = [session.subject for session in chat_sessions]

            selected_chat = st.selectbox("Frühere Chats", ["Keine"] + chat_names, key="chat_selector")
            st.session_state.selected_chat = selected_chat

            if selected_chat != "Keine":
                st.session_state.search_mode = 'url'
                selected_session = next(session for session in chat_sessions if session.subject == selected_chat)
                if st.session_state.chat_session_id != selected_session.id:
                    st.session_state.chat_session_id = selected_session.id
                    st.session_state.chat_history = []
                    chat_messages = load_chat_history(db_session, st.session_state.chat_session_id)

                    # Update chat messages in session state
                    for msg in chat_messages:
                        role = AIMessage if msg.role == "ai" else HumanMessage
                        st.session_state.chat_history.append(role(content=msg.content))

                    # Display chat name at the top of the chatbot
                    st.session_state.chat_name_displayed = True

            delete_chat = st.selectbox("Chat löschen", ["Keine"] + chat_names, key="delete_chat_selector")
            if delete_chat != "Keine":
                delete_session = next(session for session in chat_sessions if session.subject == delete_chat)

                if st.session_state.chat_session_id != delete_session.id:
                    st.session_state.chat_session_id = delete_session.id
                    st.session_state.chat_history = []
                    chat_messages = load_chat_history(db_session, st.session_state.chat_session_id)

                    # Update chat messages in session state
                    for msg in chat_messages:
                        role = AIMessage if msg.role == "ai" else HumanMessage
                        st.session_state.chat_history.append(role(content=msg.content))

                if st.button("Ausgewählten Chat löschen"):
                    with st.spinner("Chat wird gelöscht..."):
                        delete_all_messages(db_session, delete_session.id)
                        db_session.query(ChatSession).filter_by(id=delete_session.id).delete()
                        db_session.commit()
                        st.success(f"Chat '{delete_chat}' wurde gelöscht.")

                        # Reset the session state
                        st.session_state.chat_history = []
                        st.session_state.chat_session_id = None
                        st.session_state.chat_name_displayed = False
                        st.rerun()

        elif st.session_state.active_sidebar_section == 'start_chat':
            st.markdown("### PDFs hochladen oder Website-URL angeben")

            pdf_docs = st.file_uploader(
                "Bitte laden Sie Ihre PDFs hier hoch", accept_multiple_files=True, type="pdf")
            process_pdfs_button = st.button(":red[PDFs verarbeiten]")

            website_url = st.text_input("Website-URL")
            process_url_button = st.button(":violet[URL verarbeiten]")

            if process_pdfs_button:
                if not pdf_docs:
                    st.error("Bitte laden Sie mindestens eine PDF-Datei hoch.")
                else:
                    with st.spinner("PDF-Dokumente werden verarbeitet..."):
                        raw_text = get_pdf_text(pdf_docs)
                        text_chunks = get_text_chunks(raw_text)
                        vectorstore_from_pdfs = get_vectorstore_from_text(text_chunks)
                        st.session_state.conversation = get_conversation_chain(vectorstore_from_pdfs)
                        st.session_state.vector_store = None
                        st.session_state.rag_chain = None
                        st.session_state.search_mode = 'pdf'

                        # Clear the existing chat history
                        st.session_state.chat_history = []

                        # Set the chat name to the PDF name
                        subject = pdf_docs[0].name
                        st.session_state.chat_session_id = create_new_chat_session(db_session, subject)
                        st.session_state.chat_name_displayed = True

            if process_url_button:
                if not website_url.strip():
                    st.error("Bitte geben Sie eine Website-URL ein.")
                elif not (website_url.strip().lower().startswith("https://") or website_url.strip().lower().startswith("http://")):
                    st.warning("Bitte geben Sie eine gültige URL ein")
                else:
                    with st.spinner("Website wird verarbeitet..."):
                        vectorstore_from_url = get_vectorstore_from_url(website_url)
                        st.session_state.vector_store = vectorstore_from_url
                        st.session_state.conversation = None
                        st.session_state.rag_chain = get_conversational_rag_chain(
                            get_context_retriever_chain(vectorstore_from_url)
                        )
                        st.session_state.search_mode = 'url'

                        # Clear the existing chat history
                        st.session_state.chat_history = []

                        # Set the chat name to the URL
                        subject = website_url
                        st.session_state.chat_session_id = create_new_chat_session(db_session, subject)
                        st.session_state.chat_name_displayed = True

            if st.session_state.search_mode == 'pdf':
                st.info("Sie durchsuchen aktuell PDFs.")
            elif st.session_state.search_mode == 'url':
                st.info("Sie durchsuchen aktuell eine URL.")
            else:
                st.info("Es wurde kein Suchmodus ausgewählt.")

    # Display chat session title at the top of the chat area
    if st.session_state.chat_name_displayed and st.session_state.chat_session_id:
        session = db_session.query(ChatSession).filter_by(id=st.session_state.chat_session_id).first()
        if session:
            st.info(f"**Chat: {session.subject}**")

    # Chat input and handling
    user_question = st.chat_input("Message Siruis-Chatbot")

    # Ensure queries can only be made if a chat session is selected
    if user_question:
        if st.session_state.chat_session_id:
            handle_userinput(user_question, db_session, st.session_state.chat_session_id)
        else:
            st.warning("Bitte laden Sie PDFs hoch oder geben Sie eine Website-URL ein, bevor Sie mit dem Chat beginnen.")

    # Display chat history in the main chat area only
    if st.session_state.chat_history:
        for message in st.session_state.chat_history:
            if isinstance(message, AIMessage):
                st.write(bot_template.replace("{{MSG}}", message.content), unsafe_allow_html=True)
            elif isinstance(message, HumanMessage):
                st.write(user_template.replace("{{MSG}}", message.content), unsafe_allow_html=True)

if __name__ == '__main__':
    main()
