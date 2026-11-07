"""Teste independente da imagem completa da câmera no navegador."""

import ctypes
import os
from threading import Event, Thread

URL_CAMERA = "rtsp://admin:senai2026@192.168.3.13:554/onvif1"
TRANSPORTE_RTSP = "udp"
INTERVALO = 0.1  # A tela atualiza a 10 quadros/s; a captura é contínua

# No Windows, o FFmpeg também precisa da opção no ambiente do runtime C
opcoes_rtsp = f"rtsp_transport;{TRANSPORTE_RTSP}"
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = opcoes_rtsp
if os.name == "nt":
    ctypes.CDLL("msvcrt")._putenv(f"OPENCV_FFMPEG_CAPTURE_OPTIONS={opcoes_rtsp}".encode())

import cv2
import streamlit as st


def capturar(captura, dados, encerrar):
    """Lê continuamente e guarda apenas o quadro mais recente, sem acessar a interface."""
    try:
        while not encerrar.is_set():
            ok, imagem = captura.read()
            if not ok:
                dados["erro"] = "A câmera parou de enviar quadros. Pare e inicie novamente."
                break
            dados["imagem"] = imagem
    except cv2.error:
        dados["erro"] = "Falha na leitura da câmera. Pare e inicie novamente."
    finally:
        captura.release()


st.set_page_config(page_title="Teste da câmera", layout="wide")
st.title("Teste da câmera")

estado = st.session_state
if "leitura" not in estado:
    estado.leitura = None

iniciar, parar = st.columns(2)
if iniciar.button("Iniciar", disabled=estado.leitura is not None):
    captura = cv2.VideoCapture(URL_CAMERA, cv2.CAP_FFMPEG, [
        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
        cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000,
    ])
    if captura.isOpened():
        dados = {"imagem": None, "erro": ""}
        encerrar = Event()
        tarefa = Thread(target=capturar, args=(captura, dados, encerrar), daemon=True)
        estado.leitura = {"dados": dados, "encerrar": encerrar, "tarefa": tarefa}
        tarefa.start()
        st.rerun()
    else:
        captura.release()
        st.error("Não foi possível abrir a câmera. Confira URL_CAMERA.")
if parar.button("Parar") and estado.leitura is not None:
    estado.leitura["encerrar"].set()
    estado.leitura["tarefa"].join(timeout=6)
    if estado.leitura["tarefa"].is_alive():
        st.warning("Aguardando a câmera encerrar. Clique em Parar novamente em alguns segundos.")
        st.stop()
    estado.leitura = None
    st.rerun()


@st.fragment(run_every=INTERVALO)
def mostrar_camera():
    """Mostra o último quadro recebido; a captura continua enquanto a tela é atualizada."""
    if estado.leitura is None:
        st.info("Clique em Iniciar para visualizar a câmera.")
        return
    dados = estado.leitura["dados"]
    if dados["erro"]:
        st.warning(dados["erro"])
    imagem = dados["imagem"]
    if imagem is None:
        if not dados["erro"]:
            st.info("Aguardando o primeiro quadro da câmera.")
        return
    altura, largura = imagem.shape[:2]
    # Reduza a prévia antes de enviá-la ao Streamlit, preservando a imagem capturada
    largura_tela = min(960, largura)
    previa = cv2.resize(imagem, (largura_tela, int(altura * largura_tela / largura)))
    st.image(previa, channels="BGR", width=largura_tela, output_format="JPEG",
             caption=f"Imagem completa · {largura} × {altura}")


mostrar_camera()
