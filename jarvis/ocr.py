"""Leer texto de una imagen con el OCR que trae Windows (gratis, sin internet).

Bambu Studio dibuja algunas listas (impresoras, tabla de varios dispositivos) como imagen: esto las vuelve texto.
"""
import asyncio


async def _leer(img, escala):
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    img = img.convert("RGBA")
    if escala != 1:
        img = img.resize((int(img.width * escala), int(img.height * escala)))
    w, h = img.size
    escritor = DataWriter()
    escritor.write_bytes(img.tobytes("raw", "BGRA"))
    bmp = SoftwareBitmap.create_copy_from_buffer(escritor.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
    motor = OcrEngine.try_create_from_user_profile_languages()
    if motor is None:
        raise RuntimeError("Windows no tiene un idioma con reconocimiento de texto instalado.")
    res = await motor.recognize_async(bmp)
    lineas = []
    for linea in res.lines:
        cajas = [p.bounding_rect for p in linea.words]
        x0 = min(c.x for c in cajas) / escala
        y0 = min(c.y for c in cajas) / escala
        x1 = max(c.x + c.width for c in cajas) / escala
        y1 = max(c.y + c.height for c in cajas) / escala
        lineas.append({"texto": linea.text, "caja": (x0, y0, x1, y1)})
    return lineas


def leer(img, escala=2.0) -> list[dict]:
    """[{'texto': 'Taller – A1', 'caja': (x0, y0, x1, y1)}, ...] en píxeles de la imagen original."""
    return asyncio.run(_leer(img, escala))
