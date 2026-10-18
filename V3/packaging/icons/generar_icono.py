"""Genera el ícono de la app (PNG en varios tamaños + .ico de Windows).

Los archivos generados se versionan junto al código, así el build no depende
de Pillow ni de fuentes instaladas. Solo hace falta volver a correrlo si se
cambia el diseño:

    python packaging/icons/generar_icono.py
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

AQUI = Path(__file__).resolve().parent
STATIC = AQUI.parent.parent / "app" / "static"
LADO = 1024
COLOR_ARRIBA = (22, 163, 74)   # verde
COLOR_ABAJO = (15, 118, 110)   # verde azulado
FUENTES = [
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def _fuente(tamano):
    for ruta in FUENTES:
        if Path(ruta).exists():
            return ImageFont.truetype(ruta, tamano)
    return ImageFont.load_default(tamano)


def dibujar() -> Image.Image:
    # Fondo: cuadrado redondeado con degradé vertical verde.
    degradado = Image.new("RGB", (LADO, LADO))
    pixeles = degradado.load()
    for y in range(LADO):
        t = y / (LADO - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(COLOR_ARRIBA, COLOR_ABAJO))
        for x in range(LADO):
            pixeles[x, y] = color
    mascara = Image.new("L", (LADO, LADO), 0)
    ImageDraw.Draw(mascara).rounded_rectangle((40, 40, LADO - 40, LADO - 40), radius=220, fill=255)
    icono = Image.new("RGBA", (LADO, LADO), (0, 0, 0, 0))
    icono.paste(degradado, (0, 0), mascara)

    dibujo = ImageDraw.Draw(icono)
    blanco = (255, 255, 255, 255)

    # El "rulo": un arco casi completo con punta de flecha.
    centro, radio, grosor = LADO / 2, 300, 64
    caja = (centro - radio, centro - radio, centro + radio, centro + radio)
    inicio, fin = -60, 245
    dibujo.arc(caja, start=inicio, end=fin, fill=blanco, width=grosor)
    angulo = math.radians(fin)
    punta_x, punta_y = centro + radio * math.cos(angulo), centro + radio * math.sin(angulo)
    tangente = angulo + math.pi / 2  # dirección de avance del arco (sentido horario)
    largo, ancho = 120, 95
    frente = (punta_x + largo * math.cos(tangente), punta_y + largo * math.sin(tangente))
    normal = tangente + math.pi / 2
    lado_a = (punta_x + ancho * math.cos(normal), punta_y + ancho * math.sin(normal))
    lado_b = (punta_x - ancho * math.cos(normal), punta_y - ancho * math.sin(normal))
    dibujo.polygon([frente, lado_a, lado_b], fill=blanco)

    # Signo $ al centro.
    fuente = _fuente(400)
    dibujo.text((centro, centro + 10), "$", font=fuente, fill=blanco, anchor="mm")
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
