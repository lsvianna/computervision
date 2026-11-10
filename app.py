"""Interface Flask com captura contínua, inspeção e vídeo na mesma origem.

Execute com: python app.py
"""

import ctypes
import os
from pathlib import Path
from threading import Condition, Event, Lock, Thread
from time import monotonic

# CÂMERA E ROI: copie as coordenadas produzidas por calibracao/calibrar_roi.py
ROI = (420, 0, 1500, 1080)  # Recorte quadrado central da câmera 1920 × 1080
URL_CAMERA = "rtsp://admin:senai2026@10.26.92.144:554/cam/realmonitor?channel=1&subtype=0"
TRANSPORTE_RTSP = "tcp"  # Transporte validado com a Intelbras iME 500 Full Color
INTERVALO_CAMERA = 0.1  # Intervalo da detecção de presença, em segundos

# OPERAÇÃO: estas opções não aparecem na interface
USAR_VIDEO = False  # True: arquivo de vídeo; False: câmera
ENVIAR_NODE_RED = True  # Vídeo sempre utiliza simulação
PORTA_APP = 5000  # Abra http://localhost:5000 no navegador

# Configure antes de importar o OpenCV; no Windows, ajuste também o runtime C
opcoes_rtsp = f"rtsp_transport;{TRANSPORTE_RTSP}"
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = opcoes_rtsp
if os.name == "nt":
    ctypes.CDLL("msvcrt")._putenv(f"OPENCV_FFMPEG_CAPTURE_OPTIONS={opcoes_rtsp}".encode())

import cv2
from flask import Flask, Response, jsonify, render_template_string, request

from inspecao import classificar, detectar, enviar
from transformacao import transformar

PASTA = Path(__file__).resolve().parent
DESTINO = "Node-RED" if ENVIAR_NODE_RED and not USAR_VIDEO else "Simulação"
app = Flask(__name__)


class Fonte:
    """Lê a fonte uma única vez e compartilha o recorte atual e a prévia JPEG."""

    def __init__(self, endereco):
        self.encerrar = Event()
        self.pausa = Event()
        self.condicao = Condition()
        self.recorte = None
        self.jpeg = None
        self.numero = 0
        self.fim = False
        self.erro = ""
        if USAR_VIDEO:
            self.pausa.set()
            self.captura = cv2.VideoCapture(endereco)
        else:
            self.captura = cv2.VideoCapture(endereco, cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000,
            ])
        try:
            if not self.captura.isOpened():
                raise ValueError("Não foi possível abrir a fonte! Confira a configuração.")
            ok, imagem = self.captura.read()
            if not ok:
                raise ValueError("Não foi possível ler o primeiro quadro da fonte!")
            self.publicar(imagem)
            fps = self.captura.get(cv2.CAP_PROP_FPS)
            self.intervalo = 1 / fps if 0 < fps <= 240 else 1 / 25
        except Exception:
            self.captura.release()
            raise
        self.tarefa = Thread(target=self.capturar, daemon=True)
        self.tarefa.start()

    def publicar(self, imagem):
        """Aplica a transformação e reduz somente a imagem exibida no navegador."""
        altura, largura = imagem.shape[:2]
        if ROI is None:
            raise ValueError("Configure ROI no início de app.py.")
        x1, y1, x2, y2 = ROI
        if not (0 <= x1 < x2 <= largura and 0 <= y1 < y2 <= altura):
            raise ValueError(f"ROI inválida para a imagem de {largura} × {altura}.")
        recorte = transformar(imagem, ROI)
        altura, largura = recorte.shape[:2]
        escala = min(480 / largura, 480 / altura, 1)
        previa = cv2.resize(recorte, (max(1, int(largura * escala)),
                                     max(1, int(altura * escala))))
        ok, arquivo = cv2.imencode(".jpg", previa, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            raise ValueError("Não foi possível preparar a prévia da imagem!")
        with self.condicao:
            self.recorte = recorte
            self.jpeg = arquivo.tobytes()
            self.numero += 1
            self.condicao.notify_all()

    def capturar(self):
        """Mantém apenas o quadro recente, mesmo enquanto o YOLO está ocupado."""
        proximo = monotonic()
        try:
            while not self.encerrar.is_set():
                if self.pausa.is_set():
                    self.encerrar.wait(0.03)
                    proximo = monotonic()
                    continue
                ok, imagem = self.captura.read()
                if not ok:
                    if USAR_VIDEO:
                        self.fim = True
                        self.pausa.set()
                        continue
                    self.erro = "A câmera parou de enviar imagens. Use Parar e Acessar Fonte."
                    break
                self.publicar(imagem)
                if USAR_VIDEO:
                    proximo += self.intervalo
                    self.encerrar.wait(max(0, proximo - monotonic()))
        except (cv2.error, ValueError):
            self.erro = "Falha na leitura da imagem. Use Parar e Acessar Fonte."
        finally:
            self.encerrar.set()
            with self.condicao:
                self.condicao.notify_all()
            self.captura.release()

    def imagem(self):
        """Copia o recorte na resolução original para calibrar ou inspecionar."""
        with self.condicao:
            if self.encerrar.is_set():
                raise ValueError("A fonte não está enviando imagens.")
            return self.recorte.copy()

    def video(self):
        """Envia JPEGs pela mesma conexão HTTP, sem recarregar a página."""
        numero = 0
        while not self.encerrar.is_set():
            with self.condicao:
                self.condicao.wait_for(
                    lambda: self.numero != numero or self.encerrar.is_set(), timeout=3
                )
                if self.encerrar.is_set():
                    break
                if self.numero == numero:
                    continue
                numero = self.numero
                jpeg = self.jpeg
            yield (b"--quadro\r\nContent-Type: image/jpeg\r\nContent-Length: "
                   + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")

    def parar(self):
        """Encerra a leitura, libera a câmera e avisa as conexões de vídeo."""
        self.encerrar.set()
        with self.condicao:
            self.condicao.notify_all()
        self.tarefa.join(timeout=4)


# Uma única inspeção atende à bancada, independentemente do número de abas
estado = dict(fonte=None, fundo=None, ocupada=False, resultado=None,
              comando=None, mensagem="", conexao=0, ocupado=False, pendente=None)
controle = Lock()
encerrar = Event()


def classificar_quadro(recorte, fonte, fundo):
    """Aplica as funções didáticas ao recorte recebido e guarda o resultado."""
    try:
        resultado = classificar(recorte)
        with controle:
            # Parar ou recalibrar descarta uma classificação que ainda estava em execução
            if (encerrar.is_set() or fonte.encerrar.is_set()
                    or estado["fonte"] is not fonte or estado["fundo"] is not fundo):
                return
            estado["resultado"] = resultado
            classe, confianca = resultado
            estado["comando"] = enviar(classe, DESTINO)
            estado["mensagem"] = (
                "Comando simulado." if DESTINO == "Simulação"
                else "Comando enviado ao Node-RED."
            )
    except (OSError, ValueError, RuntimeError) as erro:
        with controle:
            if estado["fonte"] is fonte and estado["fundo"] is fundo:
                estado["mensagem"] = f"Falha na inspeção: {erro}"
    finally:
        with controle:
            if estado["fonte"] is fonte and estado["fundo"] is fundo:
                estado["ocupado"] = False


def inspecionar():
    """Detecta chegadas no servidor, sem depender das consultas do navegador."""
    while not encerrar.wait(INTERVALO_CAMERA):
        # Só esta tarefa chama o modelo; os botões apenas solicitam uma classificação
        with controle:
            fonte = estado["fonte"]
            tarefa = estado["pendente"]
            estado["pendente"] = None
            if tarefa is None:
                if fonte is None or fonte.encerrar.is_set() or estado["fundo"] is None:
                    continue
                try:
                    recorte = fonte.imagem()
                    presente = detectar(recorte, estado["fundo"])
                    chegada = presente and not estado["ocupada"]
                    estado["ocupada"] = presente  # Consome a chegada antes de classificar
                    if not chegada:
                        continue
                    tarefa = (recorte, fonte, estado["fundo"])
                except (cv2.error, ValueError) as erro:
                    estado["mensagem"] = f"Falha na detecção: {erro}"
                    continue
            estado.update(ocupado=True, resultado=None, comando=None, mensagem="Classificando...")
        # O vídeo, os botões e as consultas de estado continuam durante a inferência
        classificar_quadro(*tarefa)


HTML = """<!doctype html>
<html lang="pt-BR">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bancada IA</title>
<style>
body {font:16px system-ui,sans-serif;color:#20242b;margin:0;background:white}
main {max-width:1400px;margin:0 auto;padding:24px}
h1 {font-size:30px;margin:0 0 4px}
small,figcaption {color:#68717d}
.botoes {display:flex;gap:6px;flex-wrap:wrap;margin:18px 0 12px}
button,input {font:inherit;padding:9px 14px;border:1px solid #cbd0d6;border-radius:7px}
button {background:white;cursor:pointer} button:disabled {opacity:.45;cursor:default}
input {width:min(600px,90%);margin-top:12px}
.info {background:#eaf3ff;color:#1458a2;padding:12px 16px;border-radius:7px;margin:10px 0}
figure {margin:12px 0} img {display:block;max-width:100%;max-height:42vh;width:480px;
height:auto;object-fit:contain;object-position:left} figcaption {font-size:13px;margin-top:5px}
p {margin:10px 0} [hidden] {display:none!important}
</style>
<main>
<h1>Bancada de visão computacional</h1>
<small>Origem: {{ origem }} · Comunicação: {{ destino }}</small>
{% if destino == 'Node-RED' %}<p><small>Use com a bancada preparada para atuação.</small></p>{% endif %}
{% if usar_video %}
<div><input id="arquivo" placeholder="Caminho do arquivo de vídeo" aria-label="Arquivo de vídeo"></div>
<small>O vídeo começa pausado. Use uma imagem sem peça para calibrar o fundo.</small>
{% endif %}
<div class="botoes">
<button id="acessar" onclick="acao('acessar')">Acessar Fonte</button>
<button id="calibrar" onclick="acao('calibrar')" disabled>Calibrar Fundo</button>
<button id="classificar" onclick="acao('classificar')" disabled>Classificar Agora</button>
<button id="parar" onclick="acao('parar')" disabled>Parar</button>
{% if usar_video %}<button id="pausar" onclick="acao('pausar')" disabled>Continuar vídeo</button>{% endif %}
</div>
<div id="presenca" class="info" hidden></div>
<figure id="figura" hidden>
<img id="imagem" alt="Região de inspeção">
<figcaption>Vídeo contínuo da região de inspeção</figcaption>
</figure>
<div id="orientacao" class="info">Clique em Acessar Fonte para iniciar!</div>
<div id="fim" class="info" hidden>Fim do vídeo! Use Parar e Acessar Fonte para reproduzir novamente.</div>
<p id="resultado" hidden></p><p id="comando" hidden></p>
<div id="mensagem" class="info" hidden></div>
</main>
<script>
const elemento = id => document.getElementById(id);
let ocupado = false;
function texto(id, valor) {
    elemento(id).textContent = valor;
    elemento(id).hidden = !valor;
}
function mostrar(dados) {
    elemento('acessar').disabled = dados.conectada || ocupado;
    for (const id of ['calibrar', 'classificar']) {
        elemento(id).disabled = !dados.ativo || dados.ocupado || ocupado;
    }
    elemento('parar').disabled = !dados.conectada || ocupado;
    if (elemento('arquivo')) elemento('arquivo').disabled = dados.conectada || ocupado;
    if (elemento('pausar')) {
        elemento('pausar').disabled = !dados.ativo || dados.fim || ocupado;
        elemento('pausar').textContent = dados.pausado ? 'Continuar vídeo' : 'Pausar vídeo';
    }
    const imagem = elemento('imagem');
    if (dados.conectada && imagem.dataset.conexao !== String(dados.conexao)) {
        imagem.src = '/video?conexao=' + dados.conexao;
        imagem.dataset.conexao = dados.conexao;
    } else if (!dados.conectada) {
        imagem.removeAttribute('src');
        imagem.dataset.conexao = '';
    }
    elemento('figura').hidden = !dados.conectada;
    texto('presenca', dados.presenca);
    texto('orientacao', dados.orientacao);
    texto('mensagem', dados.mensagem);
    elemento('fim').hidden = !dados.fim;
    texto('resultado', dados.resultado ? 'Última classe: ' + dados.resultado[0] +
          ' · Confiança: ' + (100 * dados.resultado[1]).toFixed(1) + '%' : '');
    texto('comando', dados.comando === null ? '' :
          'Último comando / mensagem simulada: ' + JSON.stringify(dados.comando));
}
async function atualizar() {
    try {
        const resposta = await fetch('/estado', {cache:'no-store'});
        if (!ocupado) mostrar(await resposta.json());
    } catch (erro) {
        texto('mensagem', 'Não foi possível consultar o aplicativo! Confira se o servidor está aberto.');
    } finally {
        setTimeout(atualizar, 300);
    }
}
async function acao(nome) {
    if (ocupado) return;
    ocupado = true;
    document.querySelectorAll('button').forEach(botao => botao.disabled = true);
    texto('mensagem', nome === 'classificar' ? 'Classificando...' : 'Aguarde...');
    try {
        const resposta = await fetch('/acao/' + nome, {
            method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({arquivo:elemento('arquivo')?.value || ''})
        });
        const dados = await resposta.json();
        if (!resposta.ok) throw new Error(dados.mensagem);
    } catch (erro) {
        texto('mensagem', erro.message);
    } finally {
        ocupado = false;
        try {
            const resposta = await fetch('/estado', {cache:'no-store'});
            mostrar(await resposta.json());
        } catch (erro) {
            texto('mensagem', 'Não foi possível consultar o aplicativo! Confira o servidor.');
        }
    }
}
elemento('imagem').onerror = () => setTimeout(() => {
    const imagem = elemento('imagem');
    if (imagem.dataset.conexao) {
        imagem.src = '/video?conexao=' + imagem.dataset.conexao + '&t=' + Date.now();
    }
}, 1000);
atualizar();
</script>
</html>"""


@app.get("/")
def pagina():
    """Entrega a página simples, sem atualizar o vídeo pelo Python."""
    return render_template_string(HTML, usar_video=USAR_VIDEO, destino=DESTINO,
                                  origem="Vídeo" if USAR_VIDEO else "Câmera")


@app.get("/video")
def video():
    """Abre o fluxo MJPEG na mesma origem da interface."""
    with controle:
        fonte = estado["fonte"]
        conexao = request.args.get("conexao", type=int)
        if fonte is None or conexao not in (None, estado["conexao"]):
            return Response(status=204)
    return Response(fonte.video(), content_type="multipart/x-mixed-replace; boundary=quadro",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/estado")
def consultar_estado():
    """Retorna somente textos e resultados; o vídeo utiliza outra conexão."""
    with controle:
        fonte = estado["fonte"]
        ativo = fonte is not None and not fonte.encerrar.is_set()
        presenca = ""
        if ativo and estado["fundo"] is not None:
            presenca = "Peça presente" if estado["ocupada"] else "Região vazia"
        orientacao = ""
        if fonte is None:
            orientacao = "Clique em Acessar Fonte para iniciar!"
        elif ativo and estado["fundo"] is None:
            orientacao = "Calibre o fundo vazio para detectar a chegada das peças!"
        return jsonify(
            conectada=fonte is not None, ativo=ativo, conexao=estado["conexao"],
            pausado=fonte.pausa.is_set() if fonte else False,
            fim=fonte.fim if fonte else False, presenca=presenca, orientacao=orientacao,
            resultado=estado["resultado"], comando=estado["comando"],
            ocupado=estado["ocupado"],
            mensagem=fonte.erro if fonte and fonte.erro else estado["mensagem"],
        )


@app.post("/acao/<nome>")
def executar_acao(nome):
    """Atende aos botões, usando cópias do recorte na resolução original."""
    if not request.is_json:
        return jsonify(mensagem="Envie a ação pela interface do aplicativo!"), 400
    with controle:
        try:
            fonte = estado["fonte"]
            if nome == "acessar":
                if fonte is not None:
                    raise ValueError("Use Parar antes de acessar outra fonte!")
                endereco = URL_CAMERA
                if USAR_VIDEO:
                    arquivo = str((request.get_json(silent=True) or {}).get("arquivo", "")).strip()
                    endereco = str(PASTA / arquivo)
                    if not arquivo or not Path(endereco).is_file():
                        raise ValueError("Arquivo de vídeo não encontrado! Confira o caminho.")
                estado["fonte"] = Fonte(endereco)
                estado.update(fundo=None, ocupada=False, resultado=None, comando=None,
                              mensagem="", conexao=estado["conexao"] + 1,
                              ocupado=False, pendente=None)
            elif nome == "parar":
                if fonte is not None:
                    fonte.parar()
                estado.update(fonte=None, fundo=None, ocupada=False, resultado=None,
                              comando=None, mensagem="", ocupado=False, pendente=None)
            elif fonte is None or fonte.encerrar.is_set():
                raise ValueError("Acesse uma fonte antes de continuar!")
            elif nome == "calibrar":
                estado.update(fundo=fonte.imagem(), ocupada=False,
                              mensagem="Fundo vazio calibrado!", ocupado=False, pendente=None)
            elif nome == "classificar":
                if estado["ocupado"]:
                    raise ValueError("Aguarde a classificação atual!")
                recorte = fonte.imagem()
                if estado["fundo"] is not None:
                    estado["ocupada"] = detectar(recorte, estado["fundo"])
                estado.update(pendente=(recorte, fonte, estado["fundo"]), ocupado=True,
                              resultado=None, comando=None, mensagem="Classificando...")
            elif nome == "pausar" and USAR_VIDEO and not fonte.fim:
                if fonte.pausa.is_set():
                    fonte.pausa.clear()
                else:
                    fonte.pausa.set()
            else:
                raise ValueError("Ação indisponível!")
            return jsonify(ok=True)
        except ValueError as erro:
            estado["mensagem"] = str(erro)
        except (cv2.error, OSError):
            estado["mensagem"] = "Não foi possível acessar a fonte! Confira a configuração."
        return jsonify(mensagem=estado["mensagem"]), 400


if __name__ == "__main__":
    tarefa_inspecao = Thread(target=inspecionar, daemon=True)
    tarefa_inspecao.start()
    try:
        # Um único processo evita abrir a câmera ou executar a inspeção em duplicidade
        app.run(host="127.0.0.1", port=PORTA_APP, threaded=True,
                debug=False, use_reloader=False)
    finally:
        encerrar.set()
        with controle:
            if estado["fonte"] is not None:
                estado["fonte"].parar()
        tarefa_inspecao.join(timeout=4)
