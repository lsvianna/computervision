"""Etapas da inspeção: detectar presença, classificar a peça e enviar o comando."""

from pathlib import Path

import cv2
import numpy as np
import requests
from ultralytics import YOLO

# DETECTAR: ajuste a sensibilidade da comparação com o fundo vazio
LIMIAR_DIFERENCA = 25  # Diferença mínima de intensidade de um pixel (0 a 255)
PROPORCAO_MINIMA = 0.05  # Exemplo inicial: mais de 5% dos pixels alterados

# CLASSIFICAR: pesos treinados, tamanho da entrada e dispositivo de execução
ARQUIVO_MODELO = Path(__file__).with_name("best.pt")
TAMANHO_IMAGEM = 224
DISPOSITIVO = "cpu"

# ENVIAR: conexão HTTP com o Node-RED; preencher com os dados reais da bancada
PROTOCOLO = "http"
IP_NODE_RED = ""  # IP ou nome do computador central
PORTA_NODE_RED = None  # Porta informada pela equipe da bancada
ROTA_NODE_RED = ""  # Caminho do endpoint HTTP que recebe o comando
TEMPO_LIMITE = 3  # Tempo máximo de espera pela resposta, em segundos
COMANDOS = {}  # "nome_da_classe": mensagem JSON aceita pela automação


def detectar(imagem, fundo):
    """Compara o recorte atual da câmera ou vídeo com a referência sem peça.

    imagem é o recorte atual; fundo é o recorte vazio salvo pelo botão
    "Calibrar Fundo". Retorna True quando a proporção de pixels com
    diferença significativa ultrapassa o limite configurado.
    """
    diferenca = cv2.absdiff(imagem, fundo)
    cinza = cv2.cvtColor(diferenca, cv2.COLOR_BGR2GRAY)
    return bool(np.mean(cinza > LIMIAR_DIFERENCA) > PROPORCAO_MINIMA)


# Estado: guarda o modelo após o primeiro carregamento
modelo = None


def classificar(imagem):
    """Classifica o recorte recebido da câmera ou vídeo.

    Executa o YOLO sobre a imagem já recortada, carregando o modelo na primeira
    chamada. Retorna o nome da classe e a confiança, entre 0 e 1.
    """
    global modelo
    if modelo is None:
        if not ARQUIVO_MODELO.is_file():
            raise FileNotFoundError("Copie o best.pt do treinamento para a pasta do aplicativo!")
        modelo = YOLO(str(ARQUIVO_MODELO))

    resultado = modelo.predict(
        imagem,
        imgsz=TAMANHO_IMAGEM,
        device=DISPOSITIVO,
        verbose=False,
    )[0]
    if resultado.probs is None:
        raise ValueError("Use um modelo YOLO de classificação treinado com pesos -cls!")
    indice = resultado.probs.top1
    return resultado.names[indice], float(resultado.probs.top1conf)


def enviar(classe, destino):
    """Recebe a classe identificada e o destino definido pelas opções de app.py.

    Em "Simulação", apenas retorna a mensagem. Em "Node-RED", envia o
    comando por HTTP usando as configurações acima e retorna o comando.
    """
    # Esta mensagem é apenas demonstrativa enquanto o contrato real é desconhecido
    comando = COMANDOS.get(classe, {"classe": classe})
    if destino == "Node-RED":
        if (
            not IP_NODE_RED
            or PORTA_NODE_RED is None
            or not ROTA_NODE_RED
            or classe not in COMANDOS
        ):
            raise ValueError(
                "Configure IP_NODE_RED, PORTA_NODE_RED, ROTA_NODE_RED e COMANDOS em inspecao.py!"
            )
        endpoint = f"{PROTOCOLO}://{IP_NODE_RED}:{PORTA_NODE_RED}/{ROTA_NODE_RED.lstrip('/')}"
        resposta = requests.post(endpoint, json=comando, timeout=TEMPO_LIMITE)
        resposta.raise_for_status()
    return comando
