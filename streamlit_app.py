import streamlit as st
import google.oauth2.credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import json
import pandas as pd
import google.generativeai as genai
import re
import ast
import io
import docx
from google.cloud import firestore
import base64
import time

# --- Configuración de la Página ---
st.set_page_config(
    page_title="Copiloto de Comunidad de YouTube",
    page_icon="🧉",
    layout="wide"
)

# --- Funciones de Conexión a la Base de Datos ---
@st.cache_resource
def get_db_connection():
    try:
        creds_b64 = st.secrets["firebase_credentials_b64"]
        creds_json_str = base64.b64decode(creds_b64).decode("utf-8")
        creds_info = json.loads(creds_json_str)
        db = firestore.Client.from_service_account_info(creds_info)
        return db
    except Exception as e:
        st.error(f"Error Crítico al conectar a la Base de Datos: {e}")
        return None

def save_script_to_db(db, user_id, video_id, script_content):
    try:
        doc_ref = db.collection('users').document(user_id).collection('scripts').document(video_id)
        doc_ref.set({'script': script_content})
        st.toast("✅ Guion guardado en la base de datos.")
        return True
    except Exception as e:
        st.error(f"Error al guardar el guion: {e}")
        return False

def load_scripts_from_db(db, user_id):
    scripts = {}
    try:
        docs = db.collection('users').document(user_id).collection('scripts').stream()
        for doc in docs:
            scripts[doc.id] = doc.to_dict().get('script', '')
        if scripts:
            st.toast(f"✅ {len(scripts)} guion(es) cargado(s) desde la base de datos.")
    except Exception as e:
        st.warning(f"No se pudieron cargar los guiones guardados: {e}")
    return scripts

# --- Funciones de Autenticación y API ---
def initialize_flow():
    if 'google_credentials' not in st.secrets or 'APP_URL' not in st.secrets:
        st.error("Error de configuración: Faltan los secretos 'google_credentials' o 'APP_URL'.")
        return None
    client_config = json.loads(st.secrets["google_credentials"])
    redirect_uri = st.secrets["APP_URL"]
    scopes = [
        'https://www.googleapis.com/auth/youtube.force-ssl',
        'openid',
        'https://www.googleapis.com/auth/userinfo.email',
        'https://www.googleapis.com/auth/userinfo.profile'
    ]
    return Flow.from_client_config(client_config=client_config, scopes=scopes, redirect_uri=redirect_uri)

def get_user_info(credentials):
    try:
        user_info_service = build('oauth2', 'v2', credentials=credentials)
        user_info = user_info_service.userinfo().get().execute()
        st.session_state.user_info = user_info
        return user_info
    except Exception as e:
        st.error(f"Error al obtener la información del usuario: {e}")
        return None

def authenticate():
    if 'credentials' in st.session_state:
        return
    flow = initialize_flow()
    if not flow: return
    auth_code = st.query_params.get("code")
    if not auth_code:
        auth_url, _ = flow.authorization_url(prompt='select_account')
        st.link_button("🚀 Conectar mi Canal de YouTube", auth_url, use_container_width=True, type="primary")
        st.stop()
    try:
        flow.fetch_token(code=auth_code)
        st.session_state.credentials = flow.credentials
        st.query_params.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Error al obtener el token: {e}")

def get_youtube_service(credentials):
    return build('youtube', 'v3', credentials=credentials)

@st.cache_data(ttl=600)
def get_channel_videos(_youtube_service):
    try:
        request = _youtube_service.search().list(part="snippet", forMine=True, maxResults=50, type="video")
        return request.execute().get("items", [])
    except Exception as e:
        st.error(f"No se pudieron obtener los videos del canal: {e}")
        return []

@st.cache_data(ttl=300)
def get_unanswered_comments(_youtube_service, video_id):
    unanswered = []
    try:
        request = _youtube_service.commentThreads().list(part="snippet,replies", videoId=video_id, maxResults=100)
        while request:
            response = request.execute()
            for item in response.get('items', []):
                if item.get('replies') is None:
                    unanswered.append(item)
            request = _youtube_service.commentThreads().list_next(request, response)
    except Exception as e:
        st.warning(f"No se pudieron obtener comentarios para el video {video_id}: {e}")
    return unanswered

def post_youtube_reply(youtube_service, parent_id, text):
    try:
        request = youtube_service.comments().insert(part="snippet", body={"snippet": {"parentId": parent_id, "textOriginal": text}})
        request.execute()
        st.toast(f"✅ ¡Respuesta mandada!")
        return True
    except Exception as e:
        st.error(f"Error al publicar la respuesta: {e}")
        return False

def like_youtube_comment(youtube_service, comment_id):
    try:
        youtube_service.comments().rate(id=comment_id, rating="like").execute()
        st.toast(f"👍 ¡Like enviado!")
    except Exception as e:
        st.error(f"Error al dar like: {e}")

# --- Funciones de Lógica y IA ---
def process_script(script_text):
    special_instructions = re.findall(r'\*\*(.*?)\*\*', script_text, re.DOTALL)
    clean_script = re.sub(r'\*\*(.*?)\*\*', '', script_text)
    return "\n".join(special_instructions), clean_script

def get_ai_bulk_draft_responses(gemini_api_key, script, comments_data, special_instructions=""):
    genai.configure(api_key=gemini_api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    formatted_comments = "\n".join([f"{i+1}. \"{data['text']}\"" for i, data in enumerate(comments_data)])

    instructions_prompt_part = ""
    if special_instructions:
        instructions_prompt_part = f"""
        INSTRUCCIONES ESPECIALES PARA ESTE VIDEO (¡MUY IMPORTANTE!):
        ---
        {special_instructions}
        ---
        """
    prompt = f"""
    Sos un asistente de comunidad para un creador de contenido de YouTube. Tu personalidad es la de un argentino: directo, breve, con un toque de acidez e irreverencia, pero siempre ingenioso. No usas formalidades.
    {instructions_prompt_part}
    CONTEXTO DEL VIDEO (GUION):
    ---
    {script}
    ---
    LISTA DE COMENTARIOS A RESPONDER:
    ---
    {formatted_comments}
    ---
    Tu tarea es generar un borrador de respuesta para CADA uno de los comentarios de la lista, siguiendo todas las instrucciones.
    Devuelve tus respuestas en una lista de Python con formato JSON, donde cada objeto tiene un "id" (el número del comentario) y una "respuesta" (el borrador).
    Ejemplo de formato de salida:
    ```json
    [
      {{"id": 1, "respuesta": "Gracias por la buena onda, ¡un abrazo!"}},
      {{"id": 2, "respuesta": "Buena pregunta. En el video explico que..."}}
    ]
    ```
    """
    try:
        response = model.generate_content(prompt)
        match = re.search(r'\[.*\]', response.text, re.DOTALL)
        if match:
            list_str = match.group(0)
            return ast.literal_eval(list_str)
        else:
            st.error("La IA no devolvió una lista con formato válido.")
            st.text_area("Respuesta recibida de la IA:", response.text, height=150)
            return []
    except Exception as e:
        st.error(f"La IA se trabó generando respuestas. Error: {e}")
        return []

# --- Interfaz Principal de la Aplicación ---
st.title("🧉 Copiloto de Comunidad v6.0 (Estable)")

if 'credentials' not in st.session_state:
    authenticate()
else:
    credentials = st.session_state.credentials
    
    if 'user_info' not in st.session_state:
        with st.spinner("Verificando identidad..."):
            user_info = get_user_info(credentials)
            if not user_info:
                st.error("No se pudo verificar la información del usuario. Intenta cerrar sesión y volver a conectar.")
                st.stop()
    else:
        user_info = st.session_state.user_info

    user_id = user_info.get('id')
    user_email = user_info.get('email')

    if not user_id:
        st.error("No se pudo obtener un ID de usuario único. La aplicación no puede continuar.")
        st.stop()

    youtube_service = get_youtube_service(credentials)
    gemini_api_key = st.secrets.get("gemini_api_key")
    db = get_db_connection()

    if db:
        st.sidebar.success(f"Conectado como: {user_email}")
        if st.sidebar.button("Cerrar Sesión"):
            keys_to_delete = ['credentials', 'videos', 'scripts', 'unanswered_comments', 'user_info']
            for key in keys_to_delete:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

        if 'scripts' not in st.session_state:
            with st.spinner("Cargando guiones desde la base de datos..."):
                st.session_state.scripts = load_scripts_from_db(db, user_id)
        
        if 'videos' not in st.session_state:
            with st.spinner("Cargando videos de tu canal..."):
                st.session_state.videos = get_channel_videos(youtube_service)

        if st.button("🔄 Buscar Comentarios Sin Respuesta", use_container_width=True, type="primary"):
            if not gemini_api_key:
                st.error("Che, poné la 'gemini_api_key' en los Secrets para que esto funcione.")
            else:
                videos_with_context = [v for v in st.session_state.get('videos', []) if v["id"]["videoId"] in st.session_state.get('scripts', {})]
                if not videos_with_context:
                    st.warning("No hay videos con guion cargado. Subí al menos uno para empezar.")
                else:
                    st.session_state.unanswered_comments = []
                    with st.spinner("Buscando comentarios nuevos en el canal..."):
                        for video in videos_with_context:
                            comments = get_unanswered_comments(youtube_service, video["id"]["videoId"])
                            for comment in comments:
                                st.session_state.unanswered_comments.append({"video": video, "comment_thread": comment})
                    
                    if not st.session_state.unanswered_comments:
                        st.success("¡Capo! No tenés comentarios sin responder. Andá a tomar unos mates.")
                    else:
                        # --- LÓGICA DE PROCESAMIENTO POR LOTES MEJORADA ---
                        all_comments_to_process = []
                        for i, item in enumerate(st.session_state.unanswered_comments):
                            video_id = item['video']['id']['videoId']
                            comment_text = item['comment_thread']['snippet']['topLevelComment']['snippet']['textDisplay']
                            all_comments_to_process.append({
                                "video_id": video_id,
                                "text": comment_text,
                                "original_index": i
                            })
                        
                        chunk_size = 15
                        comment_chunks = [all_comments_to_process[i:i + chunk_size] for i in range(0, len(all_comments_to_process), chunk_size)]
                        
                        progress_bar = st.progress(0, text="Procesando lotes de comentarios con la IA...")
                        
                        for i, chunk in enumerate(comment_chunks):
                            video_id_for_chunk = chunk[0]['video_id']
                            script = st.session_state.scripts.get(video_id_for_chunk, "")
                            special_instructions, clean_script = process_script(script)
                            
                            drafts_list = get_ai_bulk_draft_responses(gemini_api_key, clean_script, chunk, special_instructions)
                            
                            for draft in drafts_list:
                                chunk_index = draft['id'] - 1
                                if chunk_index < len(chunk):
                                    original_index = chunk[chunk_index]['original_index']
                                    st.session_state.unanswered_comments[original_index]['draft'] = draft['respuesta']
                            
                            progress_bar.progress((i + 1) / len(comment_chunks), text=f"Lote {i+1}/{len(comment_chunks)} procesado...")
                            time.sleep(3)
                        
                        progress_bar.empty()
        
        if "unanswered_comments" in st.session_state and st.session_state.unanswered_comments:
            st.header("📬 Bandeja de Entrada Inteligente")
            for item in list(st.session_state.unanswered_comments):
                comment_thread = item['comment_thread']
                comment = comment_thread['snippet']['topLevelComment']['snippet']
                comment_id = comment_thread['snippet']['topLevelComment']['id']
                
                with st.container(border=True):
                    col1, col2 = st.columns([1, 10])
                    with col1: st.image(comment['authorProfileImageUrl'])
                    with col2:
                        st.write(f"**{comment['authorDisplayName']}** en *{item['video']['snippet']['title']}*:")
                        st.info(f"_{comment['textDisplay']}_")

                    draft = item.get('draft', 'La IA no generó un borrador para este comentario.')
                    edited_draft = st.text_area("Borrador de Respuesta:", value=draft, key=f"text_{comment_id}")

                    b_col1, b_col2, b_col3, b_col4 = st.columns([2, 1, 1, 5])
                    if b_col1.button("✅ Publicar Respuesta", key=f"pub_{comment_id}", type="primary"):
                        success = post_youtube_reply(youtube_service, comment_id, edited_draft)
                        if success:
                            st.session_state.unanswered_comments.remove(item)
                            st.rerun()
                    
                    if b_col2.button("👍 Like", key=f"like_{comment_id}"):
                        like_youtube_comment(youtube_service, comment_id)

                    if b_col3.button("🗑️ Descartar", key=f"del_{comment_id}"):
                        st.session_state.unanswered_comments.remove(item)
                        st.rerun()

        st.divider()
        
        with st.expander("🎬 Ver y Gestionar Tus Videos y Contextos"):
            if not st.session_state.get('videos'):
                st.warning("No se encontraron videos en tu canal.")
            else:
                if 'scripts' not in st.session_state: st.session_state.scripts = {}
                for video in st.session_state.videos:
                    video_id = video["id"]["videoId"]
                    title = video["snippet"]["title"]
                    col1, col2 = st.columns([1, 4])
                    with col1: st.image(video["snippet"]["thumbnails"]["medium"]["url"])
                    with col2:
                        st.subheader(title)
                        uploaded_file = st.file_uploader(f"Subir/Actualizar guion", type=['txt', 'md', 'docx'], key=video_id)
                        if uploaded_file:
                            if uploaded_file.name.endswith('.docx'):
                                try:
                                    doc = docx.Document(io.BytesIO(uploaded_file.getvalue()))
                                    full_text = "\n".join([para.text for para in doc.paragraphs])
                                except Exception as e:
                                    st.error(f"Error al leer el archivo .docx: {e}")
                                    full_text = ""
                            else: 
                                full_text = uploaded_file.getvalue().decode("utf-8")
                            
                            if full_text:
                                if save_script_to_db(db, user_id, video_id, full_text):
                                    st.session_state.scripts[video_id] = full_text
                                    st.rerun()

                        elif video_id in st.session_state.scripts:
                            st.success("🟢 Guion cargado desde la base de datos.")
                        else:
                            st.error("🔴 Falta guion.")
