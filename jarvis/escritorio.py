"""Atajos del PC: portapapeles con memoria y teclas globales para explicar y dictar.

Win+Shift+J explica el texto seleccionado en cualquier programa. Win+Shift+K dicta en el programa que esté en primer plano.
El historial del portapapeles vive solo en la RAM y no guarda lo que parece una contraseña o una llave.
"""
import asyncio
import ctypes
import logging
import re
import threading
import time
from collections import deque
from ctypes import wintypes

from . import config
from .herramientas import P, herramienta

log = logging.getLogger("jarvis.escritorio")
HISTORIAL: deque = deque(maxlen=50)
MAXIMO_TEXTO = 2000
PREFIJOS = re.compile(r"^(sk-|gsk_|ghp_|gho_|github_pat_|AIza|tskey-|xox[bp]-|AKIA|Bearer\s)")
MOD_ALT, MOD_CTRL, MOD_SHIFT, MOD_WIN, MOD_SIN_REPETIR = 1, 2, 4, 8, 0x4000
VK_J, VK_K, VK_C, VK_CONTROL, VK_SHIFT, VK_LWIN, VK_RWIN = 0x4A, 0x4B, 0x43, 0x11, 0x10, 0x5B, 0x5C
WM_HOTKEY = 0x312
CF_UNICODETEXT = 13


def parece_secreto(texto: str) -> bool:
    t = texto.strip()
    if PREFIJOS.match(t):
        return True
    if not 8 <= len(t) <= 128 or re.search(r"\s", t):
        return False
    return sum(bool(re.search(p, t)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^\w]")) >= 3


def anotar(texto: str, ahora: float | None = None):
    t = (texto or "").strip()
    if not t or parece_secreto(t) or (HISTORIAL and HISTORIAL[-1]["texto"] == t[:MAXIMO_TEXTO]):
        return
    HISTORIAL.append({"texto": t[:MAXIMO_TEXTO], "cuando": ahora or time.time()})


def _buscar(consulta: str | None = None, cuantos: int = 10) -> list[dict]:
    filas = list(HISTORIAL)
    if consulta:
        c = consulta.lower()
        filas = [f for f in filas if c in f["texto"].lower()]
    return filas[-cuantos:][::-1]


@herramienta("portapapeles_historial", "Busca lo que el usuario copió hace poco en el PC: «¿qué copié sobre el sensor?». "
             "No guarda contraseñas ni llaves.", {"buscar": P("string", "Palabras a buscar (opcional)")}, solo_pc=True)
async def portapapeles_historial(ctx, buscar=None):
    filas = _buscar(buscar)
    if not filas:
        return "No tengo nada así en el historial (solo recuerdo lo copiado desde que arrancó JARVIS)."
    return "\n".join(f"- {time.strftime('%H:%M', time.localtime(f['cuando']))}: {f['texto'][:300]}" for f in filas)


# ---------- solo Windows ----------
def _api():
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.GetClipboardData.restype = ctypes.c_void_p
    u.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    k.GlobalAlloc.restype = ctypes.c_void_p
    k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    return u, k


def leer_portapapeles() -> str | None:
    u, k = _api()
    # los gestores de contraseñas marcan así lo que no debe guardarse
    privado = u.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
    if u.IsClipboardFormatAvailable(privado) or not u.IsClipboardFormatAvailable(CF_UNICODETEXT) or not u.OpenClipboard(None):
        return None
    try:
        h = u.GetClipboardData(CF_UNICODETEXT)
        p = k.GlobalLock(h) if h else None
        if not p:
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def poner_portapapeles(texto: str):
    u, k = _api()
    datos = texto.encode("utf-16-le") + b"\0\0"
    h = k.GlobalAlloc(0x0002, len(datos))  # GMEM_MOVEABLE
    p = k.GlobalLock(h)
    ctypes.memmove(p, datos, len(datos))
    k.GlobalUnlock(h)
    if u.OpenClipboard(None):
        u.EmptyClipboard()
        u.SetClipboardData(CF_UNICODETEXT, h)  # desde aquí el portapapeles es dueño de la memoria
        u.CloseClipboard()


def _vigilar_portapapeles(parar: threading.Event):
    u = ctypes.windll.user32
    visto = u.GetClipboardSequenceNumber()
    while not parar.wait(1):
        actual = u.GetClipboardSequenceNumber()
        if actual != visto:
            visto = actual
            try:
                anotar(leer_portapapeles() or "")
            except OSError as e:
                log.debug("portapapeles: %s", e)


def seleccion() -> str:
    """Copia lo seleccionado en el programa activo y devuelve al portapapeles el texto que tenía (una imagen copiada se pierde)."""
    u = ctypes.windll.user32
    fin = time.time() + 1.5
    while time.time() < fin and any(u.GetAsyncKeyState(t) & 0x8000 for t in (VK_SHIFT, VK_LWIN, VK_RWIN)):
        time.sleep(0.02)  # con Win o Shift aún abajo, Ctrl+C sería otra combinación
    time.sleep(0.08)  # Windows aún está procesando el atajo: un Ctrl+C inmediato a veces se pierde
    previa = u.GetClipboardSequenceNumber()
    guardado = leer_portapapeles()
    for intento in range(3):  # hay programas lentos para copiar; si no respondió, se insiste
        u.keybd_event(VK_CONTROL, 0, 0, 0)
        u.keybd_event(VK_C, 0, 0, 0)
        time.sleep(0.02)
        u.keybd_event(VK_C, 0, 2, 0)
        u.keybd_event(VK_CONTROL, 0, 2, 0)
        for _ in range(30):
            time.sleep(0.02)
            if u.GetClipboardSequenceNumber() != previa:
                time.sleep(0.05)
                texto = leer_portapapeles() or ""
                if guardado is not None:
                    poner_portapapeles(guardado)
                log.info("selección copiada en el intento %d (%d caracteres)", intento + 1, len(texto))
                return texto
    log.info("no se copió nada: sin selección, o el programa no deja copiar")
    return ""


def _ver_error(tarea):
    if not tarea.cancelled() and tarea.exception():
        log.error("el atajo falló: %r", tarea.exception())


def _teclas(loop, explicar, dictar, parar: threading.Event):
    u = ctypes.windll.user32
    mod = MOD_WIN | MOD_SHIFT | MOD_SIN_REPETIR
    if not (u.RegisterHotKey(None, 1, mod, VK_J) and u.RegisterHotKey(None, 2, mod, VK_K)):
        log.warning("no pude registrar Win+Shift+J o Win+Shift+K (¿otro programa las usa?)")
    log.info("atajos listos: Win+Shift+J explica, Win+Shift+K dicta")
    m = wintypes.MSG()
    while not parar.is_set() and u.GetMessageW(ctypes.byref(m), None, 0, 0) > 0:
        if m.message == WM_HOTKEY:
            log.info("atajo %s pulsado", m.wParam)
            tarea = asyncio.run_coroutine_threadsafe(explicar() if m.wParam == 1 else dictar(), loop)
            tarea.add_done_callback(_ver_error)


async def escuchar(explicar, dictar):
    """Arranca los hilos del PC y se queda esperando hasta que JARVIS se apague."""
    if not config.ES_WINDOWS:
        return
    parar = threading.Event()
    loop = asyncio.get_running_loop()
    threading.Thread(target=_vigilar_portapapeles, args=(parar,), daemon=True).start()
    threading.Thread(target=_teclas, args=(loop, explicar, dictar, parar), daemon=True).start()
    try:
        await asyncio.Event().wait()
    finally:
        parar.set()
