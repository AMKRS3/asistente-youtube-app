import streamlit as st
import google.oauth2.credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import json
import pandas as pd
import os

# --- Configuración de la Página ---
st.set_page_config(
    page_title="Asistente de Comunidad de YouTube",
    page_icon="🚀",
    layout="wide"
)

# --- Funciones de Autenticación (El "Motor" de la App) ---

def initialize_flow():
    """
    Prepara el flujo de autenticación OAuth 2.0 cargando las credenciales secretas.
    """
    # Esta es la parte clave. El Dockerfile crea un "atajo" para que la app,
    # desde la carpeta 'src', pueda encontrar los secretos.
    if 'google_credentials' not in st.secrets or 'APP_URL' not in st.secrets:
        st.error("Error de configuración: Faltan los secretos 'google_credentials' o 'APP_URL'. Por favor, configúralos en los 'Settings' de tu Space y reinicia.")
        return None

    client_config = json.loads(st.secrets["google_credentials"])
    redirect_uri = st.secrets["APP_URL"]

    flow = Flow.from_client_config(
        client_config=client_config,
        scopes=['https://www.googleapis.com/auth/youtube.force-ssl'],
        redirect_uri=redirect_uri
    )
    return flow

# --- Funciones de la API de YouTube ---

def get_youtube_service(credentials):
    """Crea un objeto de servicio para interactuar con la API de YouTube."""
    return build('youtube', 'v3', credentials=credentials)

def get_channel_videos(youtube):
    """Obtiene una lista de todos los videos del canal del usuario autenticado."""
    try:
        request = youtube.search().list(
            part="snippet",
            forMine=True,
            maxResults=50,
            type="video"
        )
        response = request.execute()
        return response.get("items", [])
    except Exception as e:
        st.error(f"No se pudieron obtener los videos del canal: {e}")
        return []

# --- Interfaz Principal de la Aplicación (Lógica Reestructurada) ---

st.title("🚀 Asistente de Comunidad de YouTube v3.0")

# Primero, verificamos si el usuario ya está autenticado en esta sesión.
if 'credentials' in st.session_state:
    # Si ya lo está, mostramos la aplicación principal.
    credentials = st.session_state.credentials
    st.success("¡Conexión con YouTube exitosa!")
    youtube_service = get_youtube_service(credentials)

    st.header("🎬 Dashboard de Videos del Canal")
    videos = get_channel_videos(youtube_service)

    if not videos:
        st.warning("No se encontraron videos en tu canal o no se pudieron cargar.")
    else:
        if 'scripts' not in st.session_state:
            st.session_state.scripts = {}

        for video in videos:
            video_id = video["id"]["videoId"]
            title = video["snippet"]["title"]
            thumbnail_url = video["snippet"]["thumbnails"]["medium"]["url"]

            col1, col2, col3 = st.columns([1, 3, 2])

            with col1:
                st.image(thumbnail_url)
            with col2:
                st.subheader(title)
                if video_id in st.session_state.scripts:
                    st.success("🟢 Contexto Cargado")
                else:
                    st.error("🔴 Contexto Faltante")
            with col3:
                uploaded_file = st.file_uploader(f"Subir guion para '{title[:30]}...'", type=["txt", "md"], key=video_id)
                if uploaded_file is not None:
                    st.session_state.scripts[video_id] = uploaded_file.getvalue().decode("utf-8")
                    st.rerun()

    st.divider()
    if st.button("🔄 Buscar Comentarios Sin Respuesta en Todo el Canal", use_container_width=True):
        st.info("Esta funcionalidad (la bandeja de entrada inteligente) se implementará en el siguiente paso.")

else:
    # Si el usuario NO está autenticado, manejamos el flujo de login.
    flow = initialize_flow()
    if flow:
        # Verificamos si Google nos ha redirigido con un código.
        auth_code = st.query_params.get("code")
        if not auth_code:
            # Si no hay código, mostramos el botón de login.
            auth_url, _ = flow.authorization_url(prompt='consent')
            st.link_button("🚀 Conectar mi Canal de YouTube", auth_url, use_container_width=True, type="primary")
            st.info("Deberás autorizar a esta aplicación para que pueda leer tus videos y publicar respuestas en tu nombre.")
        else:
            # Si hay código, lo intercambiamos por las credenciales.
            try:
                flow.fetch_token(code=auth_code)
                st.session_state.credentials = flow.credentials
                st.query_params.clear()
                st.rerun() # Volvemos a ejecutar para mostrar la app ya logueado.
            except Exception as e:
                st.error(f"Error al obtener el token: {e}")
