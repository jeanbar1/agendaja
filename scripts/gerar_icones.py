"""
Gera os icones do PWA.

O navegador so oferece "instalar" se o manifesto apontar icones de 192 e
512 pixels. Em vez de guardar imagem pronta no repositorio, deixei o
script que desenha - assim da pra mudar a cor ou o formato sem abrir
editor de imagem.

Uso:
    pip install pillow
    python scripts/gerar_icones.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

# Mesma cor do theme-color do base.html.
VERDE = (20, 82, 79)
BRANCO = (255, 255, 255)

DESTINO = Path(__file__).resolve().parent.parent / "static" / "icones"


def desenhar(lado, margem_do_fundo=0.0):
    """Desenha o icone num quadrado de `lado` pixels.

    A margem existe por causa do icone "maskable": o Android recorta o
    icone em circulo, entao o desenho precisa caber no meio com folga,
    senao as pontas somem.
    """
    imagem = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    pincel = ImageDraw.Draw(imagem)

    # Fundo arredondado ocupando tudo.
    pincel.rounded_rectangle(
        [(0, 0), (lado - 1, lado - 1)],
        radius=int(lado * 0.22),
        fill=VERDE,
    )

    # Area util do desenho, ja descontando a margem do maskable.
    folga = lado * (0.22 + margem_do_fundo)
    x0, y0 = folga, folga
    x1, y1 = lado - folga, lado - folga
    largura = x1 - x0

    traco = max(2, int(largura * 0.09))

    # Corpo do calendario.
    topo = y0 + largura * 0.16
    pincel.rounded_rectangle(
        [(x0, topo), (x1, y1)],
        radius=int(largura * 0.12),
        outline=BRANCO,
        width=traco,
    )

    # Faixa do cabecalho.
    pincel.line(
        [(x0, topo + largura * 0.22), (x1, topo + largura * 0.22)],
        fill=BRANCO,
        width=traco,
    )

    # As duas argolas de cima.
    for proporcao in (0.3, 0.7):
        px = x0 + largura * proporcao
        pincel.line([(px, y0), (px, topo + largura * 0.08)],
                    fill=BRANCO, width=traco)

    # O "check" dentro do calendario.
    pincel.line(
        [
            (x0 + largura * 0.26, topo + largura * 0.52),
            (x0 + largura * 0.44, topo + largura * 0.68),
            (x0 + largura * 0.76, topo + largura * 0.34),
        ],
        fill=BRANCO,
        width=traco,
        joint="curve",
    )
    return imagem


def main():
    DESTINO.mkdir(parents=True, exist_ok=True)

    arquivos = [
        ("icone-192.png", 192, 0.0),
        ("icone-512.png", 512, 0.0),
        # O maskable tem margem maior porque o Android recorta.
        ("icone-maskable-512.png", 512, 0.06),
    ]

    for nome, lado, margem in arquivos:
        caminho = DESTINO / nome
        desenhar(lado, margem).save(caminho, "PNG")
        print(f"  {caminho.relative_to(DESTINO.parent.parent)}  ({lado}x{lado})")

    print(f"\n{len(arquivos)} ícones gerados em {DESTINO}")


if __name__ == "__main__":
    main()
