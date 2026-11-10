# Bancada de visão computacional

Câmera ou vídeo → recorte da ROI → presença com OpenCV → classificação com YOLO → comando simulado ou Node-RED.

## Código

| Arquivo | Responsabilidade |
|---|---|
| `app.py` | Interface Streamlit, captura e sequência da inspeção. |
| `camera.py` | Captura contínua, último quadro disponível e fluxo MJPEG local da câmera. |
| `transformacao.py` | `transformar(imagem, roi)`: recorta a região de inspeção. |
| `inspecao.py` | `detectar()`, `classificar()` e `enviar()`, com suas configurações. |
| `camera_teste.py` | Teste independente da câmera completa no navegador. |
| `calibracao/calibrar_roi.py` | Utilitário independente do professor para obter as coordenadas da ROI. |
| `treinamento.ipynb` | Treinamento do classificador no Google Colab com imagens já recortadas. |

## Executar

Com seu ambiente virtual ativado, na raiz do projeto:

```powershell
python -m pip install -r requirements.txt
python app.py
```

Abra `http://localhost:5000` no navegador.

Copie os pesos treinados para `best.pt`, na raiz. A captura e a calibração funcionam sem esses pesos; a classificação precisa deles.

## Configurar

No início de `app.py`:

- `URL_CAMERA`: endereço RTSP completo da câmera.
- `TRANSPORTE_RTSP`: transporte da conexão; a Intelbras iME 500 Full Color usa `tcp` nesta aplicação.
- `ROI`: `(esquerda, topo, direita, base)`, em pixels da imagem original.
- `INTERVALO_CAMERA`: intervalo da detecção de presença, em segundos; o player é independente.
- `USAR_VIDEO` e `ENVIAR_NODE_RED`: situação de operação, sem seletor na interface.

| Situação | `USAR_VIDEO` | `ENVIAR_NODE_RED` |
|---|---|---|
| Câmera com envio real | `False` | `True` |
| Câmera com comando simulado | `False` | `False` |
| Vídeo com comando simulado | `True` | `False` |

Vídeos sempre usam simulação, mesmo com `ENVIAR_NODE_RED = True`.

No início de `inspecao.py`, as configurações estão agrupadas por ação:

- **DETECTAR:** diferença mínima entre pixels e proporção de alteração para indicar presença.
- **CLASSIFICAR:** arquivo de pesos, tamanho de entrada e dispositivo (`cpu` ou GPU).
- **ENVIAR:** protocolo HTTP, IP, porta, rota, tempo limite e mapeamento `COMANDOS` por classe.

`classificar()` carrega o YOLO na primeira chamada e reutiliza o modelo. Reinicie a aplicação ao substituir os pesos. `enviar()` retorna o comando na simulação; no envio real, exige conexão configurada e classe mapeada, e faz um POST com JSON.

## Usar a interface

1. Clique em **Acessar Fonte**.
2. Deixe a região vazia e clique em **Calibrar Fundo**.
3. Coloque a peça: a chegada dispara uma classificação e um comando.
4. Retire a peça antes do próximo ciclo. Enquanto ela permanece, o comando não se repete.
5. Use **Parar** para liberar a fonte antes de fechar a aplicação.

**Classificar Agora** analisa o quadro atual mesmo sem calibrar o fundo. Não há repetição automática de comandos após falhas.

A interface mostra somente o recorte. A prévia reduzida não altera a resolução usada na inspeção. **Região vazia / Peça presente** aparece acima da imagem; a orientação para calibrar aparece abaixo dela.

Com a câmera, `camera.py` lê continuamente e guarda apenas o quadro mais recente. O navegador recebe o recorte por um fluxo contínuo MJPEG via HTTP, sem esperar pelo YOLO. **Calibrar Fundo** copia um quadro como referência; **Classificar Agora** utiliza um quadro recente. Na classificação automática, o modelo recebe o mesmo recorte que indicou a chegada da peça. Os três caminhos usam a resolução original, antes da redução da prévia.

Abra pelo endereço `http://localhost:8501` no próprio computador. O fluxo de vídeo usa um servidor HTTP local, em uma porta escolhida automaticamente. Use **Parar** antes de fechar a página.

Com `USAR_VIDEO = True`, informe o caminho do arquivo na interface. Caminhos relativos partem da raiz do projeto. O vídeo inicia pausado; use **Continuar vídeo / Pausar vídeo** para escolher um fundo vazio. No fim, use **Parar** e **Acessar Fonte** para reiniciar. O vídeo precisa ser compatível com a ROI configurada.
