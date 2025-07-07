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

# --- Configuración de la Página ---
st.set_page_config(
    page_title="Copiloto de Comunidad de YouTube",
    page_icon="🧉",
    layout="wide"
)

# --- Funciones de Autenticación (Estables) ---
def initialize_flow():
    if 'google_credentials' not in st.secrets or 'APP_URL' not in st.secrets:
        st.error("Error de configuración: Faltan los secretos 'google_credentials' o 'APP_URL'.")
        return None
    client_config = json.loads(st.secrets["google_credentials"])
    redirect_uri = st.secrets["APP_URL"]
    return Flow.from_client_config(
        client_config=client_config,
        scopes=['https://www.googleapis.com/auth/youtube.force-ssl'],
        redirect_uri=redirect_uri
    )

def authenticate():
    if 'credentials' in st.session_state:
        return st.session_state.credentials
    flow = initialize_flow()
    if not flow: return None
    auth_code = st.query_params.get("code")
    if not auth_code:
        auth_url, _ = flow.authorization_url(prompt='consent')
        st.link_button("🚀 Conectar mi Canal de YouTube", auth_url, use_container_width=True, type="primary")
        return None
    try:
        flow.fetch_token(code=auth_code)
        st.session_state.credentials = flow.credentials
        st.query_params.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Error al obtener el token: {e}")
    return None

# --- Funciones de la API de YouTube (Con 'Like') ---
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
    # --- AJUSTE DE PERSONALIDAD ---
    prompt = f"""
    Sos un asistente de comunidad para un creador de contenido de YouTube. Tu personalidad es la de un argentino: directo, ingenioso y con un toque de acidez e ironía. Respondes de forma inteligente y aguda, pero siempre manteniendo el respeto y sin usar insultos ni groserías (como 'boludo', 'pelotudo', 'gil', etc.). Prioriza dar la respuesta más corta y concisa que la elocuencia permita. No usas formalidades y agradeces siempre los mensajes positivos, tambien a los negativos pero con una referencia a que su msj de igual manera ayuda con el algoritmo a darle mas visibilidad al video.

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
        st.text_area("Respuesta recibida de la IA:", response.text, height=150)
        return []

# --- Interfaz Principal de la Aplicación ---
st.title("🧉 Copiloto de Comunidad v5.7 (Estable)")

if 'credentials' not in st.session_state:
    authenticate()
else:
    credentials = st.session_state.credentials
    youtube_service = get_youtube_service(credentials)
    gemini_api_key = st.secrets.get("gemini_api_key")

    st.sidebar.success("Conectado a YouTube")
    if st.sidebar.button("Cerrar Sesión"):
        keys_to_delete = ['credentials', 'videos', 'scripts', 'unanswered_comments']
        for key in keys_to_delete:
            if key in st.session_state:
                del st.session_state[key]
        st.rerun()

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
                    comments_by_video = {}
                    for i, item in enumerate(st.session_state.unanswered_comments):
                        video_id = item['video']['id']['videoId']
                        if video_id not in comments_by_video:
                            comments_by_video[video_id] = []
                        comment_data = {"text": item['comment_thread']['snippet']['topLevelComment']['snippet']['textDisplay'], "original_index": i}
                        comments_by_video[video_id].append(comment_data)

                    with st.spinner("La IA está preparando los borradores con onda..."):
                        for video_id, comments_data in comments_by_video.items():
                            full_script_text = st.session_state.scripts.get(video_id, "")
                            special_instructions, clean_script = process_script(full_script_text)
                            
                            id_to_index_map = {i+1: data['original_index'] for i, data in enumerate(comments_data)}
                            drafts_list = get_ai_bulk_draft_responses(gemini_api_key, clean_script, comments_data, special_instructions)
                            
                            for draft in drafts_list:
                                original_index = id_to_index_map.get(draft['id'])
                                if original_index is not None and original_index < len(st.session_state.unanswered_comments):
                                    st.session_state.unanswered_comments[original_index]['draft'] = draft['respuesta']
    
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
                                st.session_state.scripts[video_id] = full_text
                                st.success(f"Guion .docx para '{title[:30]}...' cargado.")
                            except Exception as e:
                                st.error(f"Error al leer el archivo .docx: {e}")
                        else: 
                            st.session_state.scripts[video_id] = uploaded_file.getvalue().decode("utf-8")
                            st.success(f"Guion de texto para '{title[:30]}...' cargado.")
                        
                    elif video_id in st.session_state.scripts:
                        st.success("🟢 Guion cargado.")
                    else:
                        st.error("🔴 Falta guion.")
