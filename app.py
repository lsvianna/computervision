"""Interface da bancada: captura, recorte, presença, classificação e comando."""

import ctypes
import os
from pathlib import Path

# CÂMERA E ROI: copie as coordenadas produzidas por calibracao/calibrar_roi.py
ROI = (420, 0, 1500, 1080)  # Provisória: recorte quadrado central da câmera 1920 × 1080
URL_CAMERA = "rtsp://admin:senai2026@192.168.3.216:554/cam/realmonitor?channel=1&subtype=0"
TRANSPORTE_RTSP = "tcp"  # Transporte validado com a Intelbras iME 500 Full Color
INTERVALO_CAMERA = 0.03  # Intervalo de atualização da interface, em segundos

# OPERAÇÃO: escolha a origem da imagem e habilite o envio real, se necessário
USAR_VIDEO = False  # True: arquivo de vídeo; False: câmera
ENVIAR_NODE_RED = False  # True: Node-RED; False: simulação. Vídeo sempre simulado

# Configure antes de importar o OpenCV; no Windows, ajuste também o runtime C
opcoes_rtsp = f"rtsp_transport;{TRANSPORTE_RTSP}"
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = opcoes_rtsp
if os.name == "nt":
    ctypes.CDLL("msvcrt")._putenv(f"OPENCV_FFMPEG_CAPTURE_OPTIONS={opcoes_rtsp}".encode())

import cv2
import requests
import streamlit as st

from inspecao import classificar, detectar, enviar
from transformacao import transformar

PASTA = Path(__file__).resolve().parent

st.set_page_config(page_title="Bancada IA", layout="wide")
st.title("Bancada de visão computacional")
if ROI is None:
    st.info(
        "Execute calibracao/calibrar_roi.py e copie as coordenadas para ROI no início de app.py."
    )

# A sessão guarda a captura e os resultados entre atualizações da tela
estado = st.session_state
origem = "Vídeo" if USAR_VIDEO else "Câmera"
destino = "Node-RED" if ENVIAR_NODE_RED and not USAR_VIDEO else "Simulação"
configuracao = (USAR_VIDEO, ENVIAR_NODE_RED, URL_CAMERA, TRANSPORTE_RTSP)
if estado.get("configuracao") != configuracao:
    # Também inicializa a primeira sessão, quando ainda não existe captura
    if estado.get("captura") is not None:
        estado.captura.release()
    estado.update(configuracao=configuracao, captura=None, quadro=None, pausado=False,
                  fim=False, fundo=None, ocupada=False,
                  resultado=None, comando=None, mensagem="")
if estado.get("roi") != ROI:
    estado.update(roi=ROI, fundo=None, ocupada=False)

if USAR_VIDEO:
    endereco = st.text_input("Caminho do arquivo de vídeo", key="arquivo_video",
                            disabled=estado.captura is not None)
    fonte = str(PASTA / endereco)
    st.caption("O vídeo começa pausado. Use uma imagem sem peça para calibrar o fundo.")
else:
    endereco = URL_CAMERA
    fonte = endereco
    if not endereco:
        st.info("Configure URL_CAMERA no início de app.py para abrir a câmera.")
st.caption(f"Origem: {origem} · Comunicação: {destino}")
if destino == "Node-RED":
    st.caption("Use com a bancada preparada e a equipe autorizada a controlá-la.")

st.markdown("""
<style>
.stMainBlockContainer {
    padding-top: 3.75rem;
    padding-bottom: 0.75rem;
}
[data-testid="stVerticalBlock"] {
    gap: 0.5rem;
}
h1 {
    font-size: 1.8rem !important;
    padding-bottom: 0.25rem !important;
}
[data-testid="stAlert"] {
    padding: 0.5rem 0.75rem;
}
.st-key-painel_imagem img {
    max-height: 36vh;
    width: auto;
    max-width: 100%;
    object-fit: contain;
}
.st-key-botoes_inspecao[data-testid="stHorizontalBlock"],
.st-key-botoes_inspecao [data-testid="stHorizontalBlock"] {
    gap: calc(1rem / 3);
}
</style>
""", unsafe_allow_html=True)


@st.fragment(run_every=0.1 if USAR_VIDEO else INTERVALO_CAMERA)
def atualizar():
    """Atualiza o recorte da fonte selecionada e atende aos controles da inspeção."""
    # Todos os controles ficam em uma única linha, acima da imagem
    ativo = estado.captura is not None
    with st.container(key="painel_controles"):
        with st.container(horizontal=True, key="botoes_inspecao"):
            acessar = st.button("Acessar Fonte", disabled=ativo or not endereco or ROI is None)
            calibrar = st.button(
                "Calibrar Fundo", disabled=not ativo, help="Retire o cubo antes de clicar."
            )
            manual = st.button("Classificar Agora", disabled=not ativo)
            parar = st.button("Parar", disabled=not ativo)
            pausar = False
            if USAR_VIDEO:
                pausar = st.button("Continuar vídeo" if estado.pausado else "Pausar vídeo",
                                   disabled=not ativo or estado.fim)
        status_presenca = st.container()

    if acessar:
        if USAR_VIDEO and not Path(fonte).is_file():
            st.error("Arquivo de vídeo não encontrado. Confira o caminho.")
        else:
            captura = cv2.VideoCapture(fonte)
            if captura.isOpened():
                estado.update(captura=captura, quadro=None, pausado=USAR_VIDEO,
                              fim=False, fundo=None, ocupada=False,
                              resultado=None, comando=None, mensagem="")
                st.rerun()
            else:
                captura.release()
                st.error("Não foi possível abrir a fonte. Confira o endereço ou arquivo.")
    if parar:
        estado.captura.release()
        estado.update(captura=None, quadro=None, fundo=None, ocupada=False)
        st.rerun()

    if estado.captura is None or ROI is None:
        st.info("Clique em Acessar Fonte para iniciar!")
        return
    if pausar:
        estado.pausado = not estado.pausado
    if not estado.pausado or estado.quadro is None:
        ok, imagem = estado.captura.read()
        if ok:
            estado.quadro = imagem
        elif USAR_VIDEO and estado.quadro is not None:
            estado.update(pausado=True, fim=True)
        else:
            st.error("Não foi possível ler a fonte!")
            return
    imagem = estado.quadro
    altura, largura = imagem.shape[:2]
    x1, y1, x2, y2 = ROI
    if not (0 <= x1 < x2 <= largura and 0 <= y1 < y2 <= altura):
        st.error(f"ROI inválida para a imagem de {largura} × {altura}! Refaça a seleção.")
        return
    recorte = transformar(imagem, ROI)
    if calibrar:
        estado.update(fundo=recorte.copy(), ocupada=False, mensagem="Fundo vazio calibrado!")

    # Uma chegada é a mudança de região vazia para ocupada
    chegada = False
    if estado.fundo is not None:
        presente = detectar(recorte, estado.fundo)
        chegada = presente and not estado.ocupada
        estado.ocupada = presente
        status_presenca.info("Peça presente" if presente else "Região vazia")

    with st.container(key="painel_imagem"):
        # Reduza apenas a apresentação; a inspeção recebe o recorte original
        st.image(recorte, channels="BGR", width=360, caption="Imagem usada na inspeção")
    if estado.fundo is None:
        st.info("Calibre o fundo vazio para detectar a chegada das peças!")
    if estado.fim:
        st.info("Fim do vídeo! Use Parar e Acessar Fonte para reproduzir novamente.")

    # O botão e a chegada automática utilizam o mesmo caminho de classificação
    if manual or chegada:
        estado.update(resultado=None, comando=None, mensagem="")
        try:
            estado.resultado = classificar(recorte)
            classe, confianca = estado.resultado
            estado.comando = enviar(classe, destino)
            estado.mensagem = (
                "Comando simulado." if destino == "Simulação"
                else "Comando enviado ao Node-RED."
            )
        except (OSError, ValueError, RuntimeError, requests.RequestException) as erro:
            # A chegada já foi consumida: uma falha não provoca reenvios automáticos
            estado.mensagem = f"Falha na inspeção: {erro}"

    if estado.resultado is not None:
        classe, confianca = estado.resultado
        st.write(f"Última classe: **{classe}** · Confiança: **{confianca:.1%}**")
    if estado.comando is not None:
        st.text(f"Último comando / mensagem simulada: {estado.comando}")
    if estado.mensagem:
        st.info(estado.mensagem)


atualizar()
