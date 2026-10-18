"""Genera el ícono de la app (PNG en varios tamaños + .ico de Windows).

Los archivos generados se versionan junto al código, así el build no depende
de Pillow. Solo hace falta volver a correrlo si se cambia el diseño:

    python packaging/icons/generar_icono.py

El dibujo: una libreta (la bitácora) con anillos y un tilde, en el degradé
neón de la app (verde azulado → verde → lima) sobre fondo oscuro.
"""

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

AQUI = Path(__file__).resolve().parent
STATIC = AQUI.parent.parent / "app" / "static"
LADO = 1024
FONDO_ARRIBA = (24, 30, 28)
FONDO_ABAJO = (10, 13, 12)
NEON = [(45, 212, 191), (74, 222, 128), (163, 230, 53)]  # --neon-1, --neon-2, --neon-3 (tema oscuro)


def _degrade_diagonal(colores) -> Image.Image:
    """Degradé a 135° (de arriba a la izquierda hacia abajo a la derecha)."""
    chico = 256
    imagen = Image.new("RGB", (chico, chico))
    pixeles = imagen.load()
    tramos = len(colores) - 1
    for y in range(chico):
        for x in range(chico):
            t = (x + y) / (2 * (chico - 1))
            i = min(int(t * tramos), tramos - 1)
            local = t * tramos - i
            a, b = colores[i], colores[i + 1]
            pixeles[x, y] = tuple(round(ca + (cb - ca) * local) for ca, cb in zip(a, b))
    return imagen.resize((LADO, LADO), Image.BICUBIC)


def _forma() -> Image.Image:
    mascara = Image.new("L", (LADO, LADO), 0)
    ImageDraw.Draw(mascara).rounded_rectangle((40, 40, LADO - 40, LADO - 40), radius=220, fill=255)
    return mascara


def _fondo() -> Image.Image:
    degradado = Image.new("RGB", (LADO, LADO))
    pixeles = degradado.load()
    for y in range(LADO):
        t = y / (LADO - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(FONDO_ARRIBA, FONDO_ABAJO))
        for x in range(LADO):
            pixeles[x, y] = color
    icono = Image.new("RGBA", (LADO, LADO), (0, 0, 0, 0))
    icono.paste(degradado, (0, 0), _forma())
    # Borde fino con el degradé neón, como las tarjetas de la app.
    borde = Image.new("L", (LADO, LADO), 0)
    ImageDraw.Draw(borde).rounded_rectangle((40, 40, LADO - 40, LADO - 40), radius=220, outline=150, width=10)
    icono.paste(_degrade_diagonal(NEON), (0, 0), borde)
    return icono


def _libreta() -> Image.Image:
    """Máscara (blanco = trazo) de la libreta con anillos y tilde."""
    mascara = Image.new("L", (LADO, LADO), 0)
    d = ImageDraw.Draw(mascara)
    grosor = 46
    # Tapa.
    izq, arriba, der, abajo = 270, 210, 770, 830
    d.rounded_rectangle((izq, arriba, der, abajo), radius=70, outline=255, width=grosor)
    # Anillos sobre el borde izquierdo.
    for y in (330, 520, 710):
        d.rounded_rectangle((izq - 70, y - 26, izq + 70, y + 26), radius=26, fill=255)
    # Renglones.
    for y, largo in ((370, 300), (470, 240)):
        d.rounded_rectangle((izq + 110, y - 18, izq + 110 + largo, y + 18), radius=18, fill=255)
    # Tilde.
    puntos = [(izq + 120, 640), (izq + 210, 730), (izq + 390, 550)]
    d.line(puntos, fill=255, width=grosor + 6, joint="curve")
    for x, y in (puntos[0], puntos[-1]):
        r = (grosor + 6) / 2
        d.ellipse((x - r, y - r, x + r, y + r), fill=255)
    return mascara


def dibujar() -> Image.Image:
    icono = _fondo()
    trazo = _libreta()
    neon = _degrade_diagonal(NEON)
    # Resplandor: el mismo trazo desenfocado y más tenue, por detrás.
    brillo = trazo.filter(ImageFilter.GaussianBlur(28))
    brillo = ImageChops.multiply(brillo, Image.new("L", (LADO, LADO), 150))
    icono.paste(neon, (0, 0), ImageChops.multiply(brillo, _forma()))
    icono.paste(neon, (0, 0), trazo)
    return icono


def main():
    icono = dibujar()
    for tamano in (16, 32, 48, 64, 128, 256, 512):
        icono.resize((tamano, tamano), Image.LANCZOS).save(AQUI / f"icono-{tamano}.png")
    icono.resize((256, 256), Image.LANCZOS).save(
        AQUI / "icono.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )
    STATIC.mkdir(parents=True, exist_ok=True)
    icono.resize((64, 64), Image.LANCZOS).save(STATIC / "icono.png")
    print("Íconos generados en", AQUI)


if __name__ == "__main__":
    main()
