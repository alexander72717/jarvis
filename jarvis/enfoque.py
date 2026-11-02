"""Modo enfoque: por un rato JARVIS no interrumpe, Windows calla sus notificaciones y al final te avisa.

Lo que se cambia en Windows queda anotado: si el PC se apaga a mitad, se devuelve al arrancar.
"""
import asyncio
import json
import logging
import time

from . import config
from .herramientas import P, herramienta

log = logging.getLogger("jarvis.enfoque")
ARCHIVO = config.DATOS / "enfoque.json"
CLAVE = r"Software\Microsoft\Windows\CurrentVersion\PushNotifications"
AVISAR = None  # async fn(texto): lo conecta el servidor


def _estado() -> dict:
    try:
        return json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def activo() -> bool:
    return _estado().get("hasta", 0) > time.time()


def _notificaciones(valor: int | None) -> int | None:
    """Pone ToastEnabled y devuelve el valor anterior (None si no existía o no es Windows)."""
    if not config.ES_WINDOWS:
        return None
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CLAVE) as k:
        try:
            previo = winreg.QueryValueEx(k, "ToastEnabled")[0]
        except OSError:
            previo = None
        if valor is None:
            try:
                winreg.DeleteValue(k, "ToastEnabled")
            except OSError:
                pass
        else:
            winreg.SetValueEx(k, "ToastEnabled", 0, winreg.REG_DWORD, valor)
    return previo


def empezar(minutos: float) -> str:
    previo = _estado()
    toast = previo["toast"] if "toast" in previo else _notificaciones(0)  # repetirlo no pisa el valor original
    ARCHIVO.write_text(json.dumps({"hasta": time.time() + minutos * 60, "toast": toast}), encoding="utf-8")
    return f"Modo enfoque por {minutos:g} minutos. Te aviso al terminar."


def terminar() -> bool:
    e = _estado()
    if not e:
        return False
    _notificaciones(e.get("toast"))
    ARCHIVO.unlink(missing_ok=True)
    return True


@herramienta("modo_enfoque", "Modo enfoque: durante esos minutos no interrumpes, se silencian las notificaciones de Windows y avisas al terminar.",
             {"minutos": P("integer", "Duración en minutos (por defecto 50)")}, solo_pc=True)
async def modo_enfoque(ctx, minutos=50):
    return await asyncio.to_thread(empezar, max(1, min(int(minutos), 480)))


@herramienta("salir_enfoque", "Termina el modo enfoque antes de tiempo y devuelve las notificaciones.", solo_pc=True)
async def salir_enfoque(ctx):
    return "Listo, salí del modo enfoque." if await asyncio.to_thread(terminar) else "No estabas en modo enfoque."


async def vigilar():
    while True:
        try:
            e = _estado()
            if e and e.get("hasta", 0) <= time.time():
                await asyncio.to_thread(terminar)
                if AVISAR:
                    await AVISAR("Terminó tu tiempo de enfoque.")
        except Exception as ex:  # noqa: BLE001
            log.warning("enfoque: %s", ex)
        await asyncio.sleep(15)
