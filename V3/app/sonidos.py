"""Sonidos de las alarmas.

Los tonos se generan acá (WAV mono de 16 bits), así no hay archivos de
audio que empaquetar. Cada uno dura un par de segundos y está pensado para
repetirse en bucle mientras la alarma suena.

`Timbre` los hace sonar desde Python en la app de escritorio: así suenan
aunque la ventana esté escondida y sin depender de que el navegador deje
reproducir audio sin un clic previo. En el navegador los toca la página.
"""

import io
import logging
import math
import shutil
import struct
import subprocess
import sys
import threading
import wave
from functools import cache
from pathlib import Path

logger = logging.getLogger(__name__)

SONIDOS = {"clasica": "Clásica", "digital": "Digital", "suave": "Suave", "campana": "Campana"}
FRECUENCIA = 22050


def _tono(hz: float, segundos: float, volumen: float = 0.5, forma: str = "seno", decae: float = 0.0) -> list[float]:
    """Muestras de un tono. `decae`: qué tan rápido se apaga (0 = parejo).
    Entrada y salida suaves para que no haga clic."""
    total = int(FRECUENCIA * segundos)
    rampa = min(int(FRECUENCIA * 0.01), total // 2)
    muestras = []
    for i in range(total):
        t = i / FRECUENCIA
        fase = 2 * math.pi * hz * t
        if forma == "cuadrada":
            # Cuadrada suavizada (pocas armónicas): se oye fuerte sin chillar.
            valor = math.sin(fase) + math.sin(3 * fase) / 3 + math.sin(5 * fase) / 5
            valor *= 0.8
        elif forma == "campana":
            valor = math.sin(fase) + 0.5 * math.sin(2.76 * fase) + 0.25 * math.sin(5.4 * fase)
            valor /= 1.75
        else:
            valor = math.sin(fase)
        envolvente = math.exp(-decae * t) if decae else 1.0
        if i < rampa:
            envolvente *= i / rampa
        elif i > total - rampa:
            envolvente *= (total - i) / rampa
        muestras.append(valor * envolvente * volumen)
    return muestras


def _silencio(segundos: float) -> list[float]:
    return [0.0] * int(FRECUENCIA * segundos)


def _patron(nombre: str) -> list[float]:
    if nombre == "digital":
        pitido = _tono(1000, 0.08, 0.45, "cuadrada") + _silencio(0.06)
        return pitido * 4 + _silencio(0.6)
    if nombre == "suave":
        notas = [523.25, 659.25, 783.99, 1046.5]  # do, mi, sol, do
        muestras = []
        for hz in notas:
            muestras += _tono(hz, 0.35, 0.5, "seno", decae=4)
        return muestras + _silencio(0.8)
    if nombre == "campana":
        return _tono(880, 1.6, 0.6, "campana", decae=2.2) + _silencio(0.3)
    # Clásica: el bip-bip del despertador.
    pitido = _tono(880, 0.12, 0.5, "cuadrada") + _silencio(0.08)
    return pitido * 2 + _silencio(0.1) + pitido * 2 + _silencio(0.5)


@cache
def wav(nombre: str) -> bytes:
    """El sonido `nombre` como archivo WAV (la clásica si no existe)."""
    muestras = _patron(nombre if nombre in SONIDOS else "clasica")
    datos = b"".join(struct.pack("<h", max(-32767, min(32767, int(m * 32767)))) for m in muestras)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as archivo:
        archivo.setnchannels(1)
        archivo.setsampwidth(2)
        archivo.setframerate(FRECUENCIA)
        archivo.writeframes(datos)
    return buffer.getvalue()


def archivo(nombre: str, carpeta: Path) -> Path:
    """El sonido guardado en disco (los reproductores del sistema piden un archivo)."""
    destino = carpeta / f"alarma-{nombre if nombre in SONIDOS else 'clasica'}.wav"
    contenido = wav(nombre)
    if not destino.exists() or destino.stat().st_size != len(contenido):
        carpeta.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(contenido)
    return destino


class Timbre:
    """Hace sonar un WAV en bucle hasta que se lo detiene. En Windows usa
    winsound (viene con Python); en Linux, el reproductor que haya
    (pw-play, paplay o aplay)."""

    def __init__(self, carpeta: Path):
        self.carpeta = carpeta
        self.sonando: str | None = None
        self._parar = threading.Event()
        self._proceso: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def sonar(self, nombre: str) -> None:
        with self._lock:
            if self.sonando == nombre:
                return
            self._detener()
            try:
                ruta = archivo(nombre, self.carpeta)
            except OSError:
                logger.warning("No se pudo preparar el sonido de la alarma", exc_info=True)
                return
            self.sonando = nombre
            if sys.platform == "win32":
                import winsound

                winsound.PlaySound(str(ruta), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP
                                   | winsound.SND_NODEFAULT)
            else:
                self._parar = threading.Event()
                threading.Thread(target=self._bucle, args=(ruta, self._parar), daemon=True, name="timbre").start()

    def detener(self) -> None:
        with self._lock:
            self._detener()

    def _detener(self) -> None:
        if self.sonando is None:
            return
        self.sonando = None
        if sys.platform == "win32":
            import winsound

            winsound.PlaySound(None, 0)
            return
        self._parar.set()
        proceso = self._proceso
        if proceso and proceso.poll() is None:
            proceso.terminate()

    def _bucle(self, ruta: Path, parar: threading.Event) -> None:
        from .recordatorios import _entorno_sistema

        programa = next((p for p in ("pw-play", "paplay", "aplay") if shutil.which(p)), None)
        if programa is None:
            logger.info("Sin reproductor de audio (pw-play, paplay o aplay): la alarma no suena")
            return
        comando = [programa, "-q", str(ruta)] if programa == "aplay" else [programa, str(ruta)]
        while not parar.is_set():
            try:
                self._proceso = subprocess.Popen(comando, env=_entorno_sistema(), stdout=subprocess.DEVNULL,
                                                 stderr=subprocess.DEVNULL)
                if parar.is_set():  # se detuvo justo mientras arrancaba
                    self._proceso.terminate()
                self._proceso.wait()
            except OSError:
                logger.warning("No se pudo reproducir la alarma", exc_info=True)
                return
