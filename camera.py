"""Captura contínua e fluxo MJPEG local, separados da inspeção didática."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Condition, Event, Thread

import cv2

from transformacao import transformar


class FluxoVideo(BaseHTTPRequestHandler):
    """Entrega os quadros JPEG pela mesma conexão HTTP, sem atualizar a página."""

    def do_GET(self):
        if self.path != "/video":
            self.send_error(404)
            return
        camera = self.server.camera
        self.connection.settimeout(3)
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=quadro")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        numero = -1
        try:
            while not camera._encerrar.is_set():
                with camera._condicao:
                    camera._condicao.wait_for(
                        lambda: camera._numero != numero or camera._encerrar.is_set(),
                        timeout=3,
                    )
                    if camera._encerrar.is_set():
                        break
                    if camera._numero == numero:
                        continue
                    numero = camera._numero
                    jpeg = camera._jpeg
                if jpeg is None:
                    continue
                cabecalho = (
                    b"--quadro\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(jpeg)).encode() + b"\r\n\r\n"
                )
                self.wfile.write(cabecalho + jpeg + b"\r\n")
                self.wfile.flush()
        except OSError:
            pass  # O navegador pode fechar a conexão ao parar ou recarregar

    def log_message(self, formato, *args):
        """Evita uma mensagem no terminal a cada acesso ao fluxo."""
        pass


class Camera:
    """Guarda o quadro original mais recente e publica uma prévia recortada."""

    def __init__(self, fonte, roi):
        self.roi = roi
        self.url = ""
        self._quadro = None
        self._jpeg = None
        self._numero = 0
        self._condicao = Condition()
        self._encerrar = Event()
        self._tarefa = None
        self._servidor = None
        self._http = None
        self._captura = cv2.VideoCapture(fonte, cv2.CAP_FFMPEG, [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000,
        ])
        try:
            if self._captura.isOpened():
                ok, imagem = self._captura.read()
                if ok:
                    self._publicar(imagem)
                    # A porta é escolhida automaticamente e atende apenas este computador
                    self._servidor = ThreadingHTTPServer(("127.0.0.1", 0), FluxoVideo)
                    self._servidor.daemon_threads = True
                    self._servidor.camera = self
                    self.url = f"http://127.0.0.1:{self._servidor.server_port}/video"
                    self._http = Thread(target=self._servidor.serve_forever, daemon=True)
                    self._http.start()
                    self._tarefa = Thread(target=self._capturar, daemon=True)
                    self._tarefa.start()
        except (cv2.error, OSError):
            self.release()

    def _publicar(self, imagem):
        """Mantém o original para inspeção e reduz apenas a prévia enviada à tela."""
        jpeg = None
        altura, largura = imagem.shape[:2]
        x1, y1, x2, y2 = self.roi
        if 0 <= x1 < x2 <= largura and 0 <= y1 < y2 <= altura:
            recorte = transformar(imagem, self.roi)
            altura, largura = recorte.shape[:2]
            escala = min(480 / largura, 480 / altura, 1)
            previa = cv2.resize(recorte, (max(1, int(largura * escala)),
                                         max(1, int(altura * escala))))
            ok, arquivo = cv2.imencode(".jpg", previa, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                jpeg = arquivo.tobytes()
        with self._condicao:
            self._quadro = imagem
            self._jpeg = jpeg
            self._numero += 1
            self._condicao.notify_all()

    def _capturar(self):
        """Lê continuamente, substituindo o quadro anterior sem formar uma fila."""
        try:
            while not self._encerrar.is_set():
                ok, imagem = self._captura.read()
                if not ok:
                    break
                self._publicar(imagem)
        except cv2.error:
            pass
        finally:
            self._encerrar.set()
            with self._condicao:
                self._condicao.notify_all()
            self._captura.release()

    def isOpened(self):
        """Informa se a captura começou e continua ativa."""
        return self._tarefa is not None and not self._encerrar.is_set()

    def read(self):
        """Entrega uma cópia do último quadro completo, na resolução original."""
        with self._condicao:
            if self._encerrar.is_set() or self._quadro is None:
                return False, None
            return True, self._quadro.copy()

    def release(self):
        """Encerra a leitura e o servidor HTTP quando o usuário clica em Parar."""
        self._encerrar.set()
        with self._condicao:
            self._condicao.notify_all()
        if self._tarefa is not None:
            self._tarefa.join(timeout=4)
        else:
            self._captura.release()
        if self._servidor is not None:
            if self._http is not None and self._http.is_alive():
                self._servidor.shutdown()
            self._servidor.server_close()
            self._servidor = None
