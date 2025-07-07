import streamlit as st
import google.oauth2.credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import json
import pandas as pd
import google.generativeai as genai

# --- Configuración de la Página ---
st.set_page_config(
    page_title="Asistente de Comunidad de YouTube",
    page_icon="🚀",
    layout="wide"
)

# --- Funciones de Autenticación (Ya funcionan) ---

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
        st.info("Deberás autorizar a esta aplicación para que pueda leer tus videos y publicar respuestas en tu nombre.")
        return None
    try:
        flow.fetch_token(code=auth_code)
        st.session_state.credentials = flow.credentials
        st.query_params.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Error al obtener el token: {e}")
    return None

# --- Funciones de la API de YouTube (Nuevas y Mejoradas) ---

def get_youtube_service(credentials):
    return build('youtube', 'v3', credentials=credentials)

@st.cache_data(ttl=600) # Cache por 10 minutos
def get_channel_videos(_youtube_service):
    try:
        request = _youtube_service.search().list(part="snippet", forMine=True, maxResults=50, type="video")
        response = request.execute()
        return response.get("items", [])
    except Exception as e:
        st.error(f"No se pudieron obtener los videos del canal: {e}")
        return []

@st.cache_data(ttl=300) # Cache por 5 minutos
def get_unanswered_comments(_youtube_service, video_id):
    unanswered = []
    try:
        request = _youtube_service.commentThreads().list(
            part="snippet,replies",
            videoId=video_id,
            maxResults=100
        )
        while request:
            response = request.execute()
            for item in response['items']:
                # Si no hay respuestas, o si el autor del canal no está entre los que respondieron
                if item.get('replies') is None:
                    unanswered.append(item)
            request = _youtube_service.commentThreads().list_next(request, response)
    except Exception as e:
        st.warning(f"No se pudieron obtener comentarios para el video {video_id}: {e}")
    return unanswered

def post_youtube_reply(youtube_service, parent_id, text):
    try:
        request = youtube_service.comments().insert(
            part="snippet",
            body={
              "snippet": {
                "parentId": parent_id,
                "textOriginal": text
              }
            }
        )
        response = request.execute()
        st.toast(f"✅ Respuesta publicada con éxito!")
        return response
    except Exception as e:
        st.error(f"No se pudo publicar la respuesta: {e}")
        return None

# --- Función de IA ---
@st.cache_data
def get_ai_draft_response(_gemini_api_key, script, comment_text):
    genai.configure(api_key=_gemini_api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    Eres un asistente de comunidad para un creador de contenido de YouTube. Tu tono es amigable, agradecido y servicial.
    
    CONTEXTO DEL VIDEO (GUION):
    ---
    {script}
    ---
    
    COMENTARIO DEL USUARIO AL QUE DEBES RESPONDER:
    ---
    "{comment_text}"
    ---
    
    Basado en el contexto del video y el comentario del usuario, redacta un borrador de respuesta conciso y positivo. Agradece siempre el comentario. Si es una pregunta, intenta responderla usando el guion. Si es una opinión, agradécela.
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Error de la IA: {e}"

# --- Interfaz Principal de la Aplicación ---

st.title("🚀 Asistente de Comunidad de YouTube v3.0")

credentials = authenticate()

if credentials:
    st.success("¡Conexión con YouTube exitosa!")
    youtube_service = get_youtube_service(credentials)
    gemini_api_key = st.secrets.get("gemini_api_key") # Asumimos que guardaste una clave de Gemini en los Secrets

    # --- Dashboard de Videos y Contexto ---
    st.header("🎬 Dashboard de Videos del Canal")
    if 'scripts' not in st.session_state:
        st.session_state.scripts = {}

    videos = get_channel_videos(youtube_service)
    if not videos:
        st.warning("No se encontraron videos en tu canal.")
    else:
        for video in videos:
            video_id = video["id"]["videoId"]
            title = video["snippet"]["title"]
            thumbnail_url = video["snippet"]["thumbnails"]["medium"]["url"]
            col1, col2, col3 = st.columns([1, 3, 2])
            with col1: st.image(thumbnail_url)
            with col2: st.subheader(title)
            with col3:
                uploaded_file = st.file_uploader(f"Subir guion para '{title[:30]}...'", type=["txt", "md"], key=video_id)
                if uploaded_file:
                    st.session_state.scripts[video_id] = uploaded_file.getvalue().decode("utf-8")
                    st.success(f"Guion para '{title[:30]}...' cargado.")

    st.divider()

    # --- Bandeja de Entrada Inteligente ---
    if st.button("🔄 Buscar Comentarios Sin Respuesta", use_container_width=True, type="primary"):
        if not gemini_api_key:
            st.error("Por favor, añade tu 'gemini_api_key' a los Secrets de la aplicación para generar borradores.")
        else:
            videos_with_context = [v for v in videos if v["id"]["videoId"] in st.session_state.scripts]
            if not videos_with_context:
                st.warning("No hay videos con contexto cargado. Sube al menos un guion para empezar.")
            else:
                st.session_state.unanswered_comments = []
                with st.spinner("Buscando comentarios en todo el canal..."):
                    for video in videos_with_context:
                        video_id = video["id"]["videoId"]
                        comments = get_unanswered_comments(youtube_service, video_id)
                        for comment in comments:
                            st.session_state.unanswered_comments.append((video, comment))
                if not st.session_state.unanswered_comments:
                    st.success("¡Felicidades! No tienes comentarios sin responder en los videos con contexto.")

    if "unanswered_comments" in st.session_state and st.session_state.unanswered_comments:
        st.header("📬 Bandeja de Entrada Inteligente")
        
        for i, (video, comment_thread) in enumerate(st.session_state.unanswered_comments):
            comment = comment_thread['snippet']['topLevelComment']['snippet']
            video_title = video['snippet']['title']
            video_id = video['id']['videoId']
            author_name = comment['authorDisplayName']
            author_image = comment['authorProfileImageUrl']
            comment_text = comment['textDisplay']
            parent_id = comment_thread['id']

            with st.expander(f"**{author_name}** comentó en **'{video_title[:40]}...'**"):
                col1, col2 = st.columns([1, 6])
                with col1:
                    st.image(author_image)
                with col2:
                    st.write(f"**Comentario:**")
                    st.info(comment_text)

                # Generar borrador con la IA
                script_context = st.session_state.scripts.get(video_id, "No hay guion disponible.")
                draft_key = f"draft_{i}"
                
                if draft_key not in st.session_state:
                    st.session_state[draft_key] = get_ai_draft_response(gemini_api_key, script_context, comment_text)
                
                st.write("**Borrador Sugerido por la IA:**")
                edited_draft = st.text_area("Puedes editar la respuesta aquí:", value=st.session_state[draft_key], key=f"text_{i}")

                # Botones de acción
                b_col1, b_col2, b_col3 = st.columns([1,1,4])
                if b_col1.button("✅ Publicar Respuesta", key=f"pub_{i}", type="primary"):
                    post_youtube_reply(youtube_service, parent_id, edited_draft)
                    # Eliminar de la lista para que no vuelva a aparecer
                    st.session_state.unanswered_comments.pop(i)
                    st.rerun()
                
                if b_col2.button("🗑️ Descartar", key=f"del_{i}"):
                    st.session_state.unanswered_comments.pop(i)
                    st.rerun()
