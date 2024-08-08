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

load_dotenv()


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


def get_conversation_chain(vectorstore):
    llm = ChatOpenAI(model="gpt-4o", temperature=0.6, streaming=True)
    memory = ConversationBufferMemory(
        memory_key='chat_history', return_messages=True)
    conversation_chain = ConversationalRetrievalChain.from_llm(
        llm=llm,
        retriever=vectorstore.as_retriever(),
        memory=memory
    )
    return conversation_chain


def get_context_retriever_chain(vector_store):
    llm = ChatOpenAI(model="gpt-4o", temperature=0, streaming=True)
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
    llm = ChatOpenAI(model="gpt-4o", temperature=0, streaming=True)
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Beantworte die Fragen des Nutzers basierend auf den folgenden Informationen context:\n\n{context}"),
        MessagesPlaceholder(variable_name="chat_history"),
        ("user", "{input}"),
    ])
    stuff_documents_chain = create_stuff_documents_chain(llm, prompt)
    return create_retrieval_chain(retriever_chain, stuff_documents_chain)


def handle_userinput(user_input):
    if st.session_state.search_mode is None:
        st.warning("Bitte laden Sie PDFs hoch oder geben Sie eine Website-URL ein, bevor Sie mit dem Chat beginnen.")
        return

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

def main():
    load_dotenv()

    st.set_page_config(page_title=" PDFs & Webseiten",
                       page_icon=":books:")
    st.write(css, unsafe_allow_html=True)
    st.write(page_bg_img, unsafe_allow_html=True)
    if "conversation" not in st.session_state:
        st.info("Bitte laden Sie PDFs hoch oder geben Sie eine Website-URL ein, bevor Sie mit dem Chat beginnen.")

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    if "vector_store" not in st.session_state:
        st.session_state.vector_store = None

    if "rag_chain" not in st.session_state:
        st.session_state.rag_chain = None

    if "search_mode" not in st.session_state:
        st.session_state.search_mode = None

    user_question = st.chat_input("Message Siruis-Chatbot")
    if user_question and st.session_state.search_mode is None:
        st.warning("Bitte laden Sie PDFs hoch oder geben Sie eine Website-URL ein, bevor Sie mit dem Chat beginnen.")
    elif user_question:
        handle_userinput(user_question)

    with st.sidebar:
        st.markdown(sidebar, unsafe_allow_html=True)

        pdf_docs = st.file_uploader(
            "Bitte laden Sie Ihre PDFs hier hoch", accept_multiple_files=True, type="pdf")
        process_pdfs_button = st.button(":red[Verarbeite PDFs]")

        website_url = st.text_input("Website URL")
        process_url_button = st.button(":violet[Verarbeite URL]")

        if process_pdfs_button:
            if pdf_docs is None or len(pdf_docs) == 0:
                st.error("Bitte laden Sie mindestens eine PDF-Datei hoch")
            else:
                with st.spinner("Verarbeite PDF-Dokumente..."):
                    raw_text = get_pdf_text(pdf_docs)
                    text_chunks = get_text_chunks(raw_text)
                    vectorstore_from_pdfs = get_vectorstore_from_text(text_chunks)
                    st.session_state.conversation = get_conversation_chain(vectorstore_from_pdfs)
                    st.session_state.vector_store = None
                    st.session_state.rag_chain = None
                    st.session_state.search_mode = 'pdf'
                    st.success("Die PDF-Daten wurden erfolgreich verarbeitet.")

        if process_url_button:
            if website_url is None or website_url.strip() == "":
                st.error("Bitte geben Sie eine Website-URL ein.")
            else:
                with st.spinner("Verarbeite Webseite..."):
                    vectorstore_from_url = get_vectorstore_from_url(website_url)
                    st.session_state.vector_store = vectorstore_from_url
                    st.session_state.conversation = None
                    st.session_state.rag_chain = get_conversational_rag_chain(
                        get_context_retriever_chain(vectorstore_from_url)
                    )
                    st.session_state.search_mode = 'url'
                    st.success("Die Daten der Website wurden erfolgreich verarbeitet.")

        if st.session_state.search_mode == 'pdf':
            st.info("Sie durchsuchen aktuell PDFs.")
        elif st.session_state.search_mode == 'url':
            st.info("Sie durchsuchen aktuell eine URL.")
        else:
            st.info("Es wurde kein Suchmodus ausgewählt.")

    if st.session_state.chat_history:
        for message in st.session_state.chat_history:
            if isinstance(message, AIMessage):

                st.write(bot_template.replace("{{MSG}}", message.content), unsafe_allow_html=True)
            elif isinstance(message, HumanMessage):
                st.write(user_template.replace("{{MSG}}", message.content), unsafe_allow_html=True)


if __name__ == '__main__':
    main()