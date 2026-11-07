"""Pipeline de transformação da imagem antes da inspeção."""


def transformar(imagem, roi):
    """Recebe a imagem original e retorna uma cópia da região de inspeção.

    roi contém (esquerda, topo, direita, base), em pixels da imagem original.
    O recorte é a transformação inicial; outras etapas podem ser incluídas aqui.
    """
    # Recorta a região de inspeção da imagem original
    x1, y1, x2, y2 = roi
    # Em NumPy, selecionamos primeiro as linhas (y) e depois as colunas (x)
    return imagem[y1:y2, x1:x2].copy()
