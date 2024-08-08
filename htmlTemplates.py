css = '''
<style>

.chat-message {
    display: flex;
    align-items: flex-start;
    margin-bottom: 20px;
}
.chat-message img {
    border-radius: 50%;
    margin-right: 10px;
}
.chat-message .message {
    max-width: 80%;
    background-color: #f1f0f0;
    padding: 10px;
    border-radius: 10px;
}
.chat-message.ai .message {
    background-color: #d5e8d4; /* Light green background for bot messages */
}
.chat-message.user .message {
    background-color: #ebf0f6; /* Light blue background for user messages */
}
.sidebar .stButton {
    font-family: 'Arial', sans-serif; /* Hier kannst du die gewünschte Schriftart angeben */
    font-size: 2px; /* Optional: Schriftgröße anpassen */
    font-weight: bold; /* Optional: Schriftgewicht anpassen */
}
'''

page_bg_img = '''
<style>
[data-testid="stAppViewContainer"] {
background-image: url("https://i.ibb.co/6J8vT5R/Screenshot-2024-07-08-at-20-03-02.png");
background-size: 45%;
background-repeat: no-repeat;
background-position: center;
background-attachment: fixed;
}


</style>
'''

bot_template = '''
<div class="chat-message ai">
    <img src="https://i.ibb.co/Tgn5RgZ/Chatbot-Logo.jpg" style="max-height: 45px; max-width: 45px; border-radius: 50%; object-fit: cover;">
     <div class="message">{{MSG}}</div>
           
</div>
'''
bot_Search_template = '''
<div class="chat-message ai">
    <img src="https://i.ibb.co/Wc5LZp1/icons8-search-64.png" style="max-height: 45px; max-width: 45px; border-radius: 50%; object-fit: cover;">
     <div class="message">{{MSG}}</div>

</div>
'''
user_template = '''
<div class="chat-message user">
    <img src="https://i.ibb.co/mR681JZ/male-student.png" style="max-height: 45px; max-width: 45px; border-radius: 50%; object-fit: cover;">
    <div class="message">{{MSG}}</div>
</div>
'''
sidebar='''
<style>
[data-testid="stFileUploadDropzone"] div div::before {color:Black; content:"Ziehen Sie die Datei hierher, um sie hochzuladen"}
[data-testid="stFileUploadDropzone"] div div span{display:none;}
[data-testid="stFileUploadDropzone"] div div::after {color:Black; font-size: .8em; content:"Limit: 200 MB pro Datei"}
[data-testid="stFileUploadDropzone"] div div small{display:none;}
</style>
'''


