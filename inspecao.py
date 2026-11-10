"""Etapas da inspeção: detectar presença, classificar a peça e enviar o comando."""

from pathlib import Path
from time import monotonic, sleep

import cv2
import numpy as np
import paho.mqtt.client as mqtt
from ultralytics import YOLO

# DETECTAR: ajuste a sensibilidade da comparação com o fundo vazio
LIMIAR_DIFERENCA = 25  # Diferença mínima de intensidade de um pixel (0 a 255)
PROPORCAO_MINIMA = 0.05  # Exemplo inicial: mais de 5% dos pixels alterados

# CLASSIFICAR: pesos treinados, tamanho da entrada e dispositivo de execução
ARQUIVO_MODELO = Path(__file__).with_name("best.pt")
TAMANHO_IMAGEM = 224
DISPOSITIVO = "cpu"

# ENVIAR: broker MQTT utilizado pelo Node-RED
IP_NODE_RED = "10.26.92.250"
PORTA_NODE_RED = 1883
USUARIO_MQTT = "senai"
SENHA_MQTT = "senai"
TEMPO_LIMITE = 3  # Tempo máximo de espera pela resposta, em segundos
DURACAO_PULSO = 1.0  # Segundos entre o comando 1 e o retorno a 0 no PLC
# As chaves devem ser iguais aos nomes das classes gravadas no best.pt
COMANDOS = {
    "boa": {"topico": "camera/boa", "valor": 1},
    "ruim": {"topico": "camera/ruim", "valor": 1},
}


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


def aguardar_mqtt(cliente, pronto):
    """Processa a conexão até concluir uma etapa, respeitando o tempo limite."""
    limite = monotonic() + TEMPO_LIMITE
    while not pronto():
        restante = limite - monotonic()
        if restante <= 0:
            raise TimeoutError("Tempo limite ao enviar o comando MQTT.")
        codigo = cliente.loop(timeout=min(0.1, restante))
        if codigo != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(
                "Falha na conexão MQTT. Confira usuário, senha e acesso ao broker. "
                + mqtt.error_string(codigo)
            )


def enviar(classe, destino):
    """Recebe a classe identificada e o destino definido pelas opções de app.py.

    Em "Simulação", apenas retorna a mensagem. Em "Node-RED", publica o
    valor no tópico MQTT configurado, espera DURACAO_PULSO e publica 0.
    """
    comando = COMANDOS.get(classe, {"classe": classe})
    if destino == "Node-RED":
        if classe not in COMANDOS:
            raise ValueError(
                f"Classe '{classe}' sem comando MQTT. Configure COMANDOS em inspecao.py!"
            )
        cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, reconnect_on_failure=False)
        cliente.username_pw_set(USUARIO_MQTT, SENHA_MQTT)
        cliente.connect_timeout = TEMPO_LIMITE
        try:
            cliente.connect(IP_NODE_RED, PORTA_NODE_RED)
            aguardar_mqtt(cliente, cliente.is_connected)
            envio = cliente.publish(comando["topico"], comando["valor"], qos=0, retain=False)
            aguardar_mqtt(cliente, envio.is_published)
            sleep(DURACAO_PULSO)
            envio = cliente.publish(comando["topico"], 0, qos=0, retain=False)
            aguardar_mqtt(cliente, envio.is_published)
        finally:
            cliente.disconnect()
    if classe in COMANDOS:
        return {**comando, "reinicio": 0, "duracao_pulso": DURACAO_PULSO}
    return comando
