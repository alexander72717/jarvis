"""Impresión 3D con Bambu Lab: abrir un modelo, elegir piezas, copias y ajustes, organizar los platos y enviarlos.

- modelos3d.py separa el archivo en piezas y genera el visor 3D (GLB) para la interfaz.
- laminado.py usa la línea de comandos de Bambu Studio para orientar, organizar en platos y laminar.
- Este módulo reparte los platos entre las impresoras libres con el color pedido y los envía.

Bambu solo deja a programas externos enviar trabajos en «Modo desarrollador» (LAN, sin nube). Para conservar la nube
y Bambu Handy, el envío se hace manejando la ventana «Enviar trabajo de impresión» de Bambu Studio, que lista todas
las impresoras de tu cuenta (en casa o fuera) con su estado y los colores del AMS. Mientras envía, mueve el ratón.
"""
import asyncio
import ctypes
import hashlib
import json
import logging
import math
import os
import re
import struct
import subprocess
import time
from array import array
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path

from . import config, laminado, modelos3d
from .herramientas import P, _sin_tildes, herramienta

EXTENSIONES = (".3mf", ".stl", ".obj", ".step", ".stp")
EXE = laminado.EXE
DIALOGO = "Enviar trabajo de impresión"
TRABAJOS = config.DATOS / "impresion3d"
# UI Automation y los clics van siempre por un mismo hilo, uno a la vez
_hilo = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bambu")
log = logging.getLogger("jarvis.impresion3d")
ESTADO = {"modelo": None, "plan": None, "hwnd": None, "abierto": None, "impresoras": [], "cuando": 0.0}

# ───────────────────────── colores ─────────────────────────
COLORES = {
    "negro": (25, 25, 28), "blanco": (245, 245, 245), "gris": (140, 140, 145), "plateado": (190, 192, 196),
    "rojo": (200, 30, 35), "naranja": (240, 120, 30), "amarillo": (245, 215, 40), "verde": (40, 160, 70),
    "verde claro": (150, 215, 110), "azul": (25, 95, 200), "azul claro": (110, 190, 230), "celeste": (150, 210, 230),
    "morado": (130, 80, 170), "lila": (180, 150, 220), "rosa": (240, 130, 180), "café": (110, 70, 40),
    "beige": (220, 200, 160), "dorado": (200, 160, 60),
}
SINONIMOS = {"marron": "café", "cafe": "café", "violeta": "morado", "purpura": "morado", "rosado": "rosa",
             "plata": "plateado", "oro": "dorado", "cian": "celeste", "turquesa": "celeste", "crema": "beige",
             "black": "negro", "white": "blanco", "red": "rojo", "blue": "azul", "green": "verde"}


def _distancia(a, b) -> float:
    # «redmean»: distancia RGB ponderada que se parece a la percepción humana sin librerías
    rm = (a[0] + b[0]) / 2
    dr, dg, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return math.sqrt((2 + rm / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rm) / 256) * db * db)


def nombre_color(rgb) -> str:
    return min(COLORES, key=lambda n: _distancia(rgb, COLORES[n]))


def color_pedido(texto: str):
    """'rojo' / 'azul claro' / '#ff0000' -> (nombre, rgb) o None."""
    t = _sin_tildes(texto or "").strip()
    if m := re.fullmatch(r"#?([0-9a-f]{6})", t):
        rgb = tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
        return nombre_color(rgb), rgb
    for n in sorted(COLORES, key=len, reverse=True):
        if _sin_tildes(n) in t:
            return n, COLORES[n]
    for s, n in SINONIMOS.items():
        if s in t:
            return n, COLORES[n]
    return None


# ───────────────────────── Bambu Studio por UI Automation ─────────────────────────
def _ventanas() -> list[tuple[int, str]]:
    import psutil

    pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "bambu-studio.exe"}
    hs = []
    u32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value in pids and u32.IsWindowVisible(h):
            buf = ctypes.create_unicode_buffer(300)
            u32.GetWindowTextW(h, buf, 300)
            hs.append((h, buf.value))
        return True
    u32.EnumWindows(cb, 0)
    return hs


def _control(h):
    import uiautomation as auto

    for _ in range(5):  # Bambu ocupado laminando a veces rechaza la consulta: reintenta
        try:
            return auto.ControlFromHandle(h)
        except Exception:  # noqa: BLE001
            time.sleep(1)
    raise RuntimeError("Bambu Studio no responde.")


def _recorrer(c, prof=12):
    import uiautomation as auto

    try:
        yield from auto.WalkControl(c, maxDepth=prof)
    except Exception:  # noqa: BLE001
        return


def _centro(r):
    return (r.left + r.right) // 2, (r.top + r.bottom) // 2


# Los clics se mandan como mensajes a la ventana de Bambu Studio y las capturas se toman de la propia ventana:
# así JARVIS trabaja en segundo plano sin mover tu ratón y aunque tengas otra ventana encima.
_u32 = ctypes.windll.user32 if os.name == "nt" else None
_g32 = ctypes.windll.gdi32 if os.name == "nt" else None
if _u32:  # en 64 bits los HDC/HBITMAP no caben en un int de C: hay que declarar los tipos
    _V = ctypes.c_void_p
    for f, args, res in ((_u32.GetWindowDC, [_V], _V), (_u32.ReleaseDC, [_V, _V], ctypes.c_int),
                         (_u32.PrintWindow, [_V, _V, ctypes.c_uint], ctypes.c_int),
                         (_g32.CreateCompatibleDC, [_V], _V), (_g32.CreateCompatibleBitmap, [_V, ctypes.c_int, ctypes.c_int], _V),
                         (_g32.SelectObject, [_V, _V], _V), (_g32.DeleteObject, [_V], ctypes.c_int), (_g32.DeleteDC, [_V], ctypes.c_int),
                         (_g32.GetDIBits, [_V, _V, ctypes.c_uint, ctypes.c_uint, _V, _V, ctypes.c_uint], ctypes.c_int)):
        f.argtypes, f.restype = args, res


def _raiz(ref) -> int:
    h = ref if isinstance(ref, int) else (ref.NativeWindowHandle or ref.GetTopLevelControl().NativeWindowHandle)
    return h


def _hwnd_en(h: int, x: int, y: int) -> int:
    """La subventana más profunda de `h` bajo el punto de pantalla (x, y)."""
    for _ in range(30):
        p = wintypes.POINT(int(x), int(y))
        _u32.ScreenToClient(h, ctypes.byref(p))
        hijo = _u32.ChildWindowFromPointEx(h, p, 0x0001 | 0x0004)  # CWP_SKIPINVISIBLE | CWP_SKIPTRANSPARENT
        if not hijo or hijo == h:
            return h
        h = hijo
    return h


def _clic(x, y, ref):
    h = _hwnd_en(_raiz(ref), x, y)
    p = wintypes.POINT(int(x), int(y))
    _u32.ScreenToClient(h, ctypes.byref(p))
    lp = ((p.y & 0xFFFF) << 16) | (p.x & 0xFFFF)
    for msg, wp in ((0x0200, 0), (0x0201, 1), (0x0202, 0)):  # WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP
        _u32.PostMessageW(h, msg, wp, lp)
    time.sleep(0.4)


def _tocar(ctrl):
    _clic(*_centro(ctrl.BoundingRectangle), ctrl)


def _captura(r, ref):
    """Imagen del rectángulo de pantalla r tomada de su ventana (PrintWindow), aunque esté tapada."""
    from PIL import Image

    raiz = _u32.GetAncestor(_raiz(ref), 2)  # GA_ROOT
    rv = wintypes.RECT()
    _u32.GetWindowRect(raiz, ctypes.byref(rv))
    w, h = rv.right - rv.left, rv.bottom - rv.top
    g32 = _g32
    dc = _u32.GetWindowDC(raiz)
    mem = g32.CreateCompatibleDC(dc)
    bmp = g32.CreateCompatibleBitmap(dc, w, h)
    g32.SelectObject(mem, bmp)
    _u32.PrintWindow(raiz, mem, 2)  # PW_RENDERFULLCONTENT
    cab = ctypes.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0), 44)
    buf = ctypes.create_string_buffer(w * h * 4)
    g32.GetDIBits(mem, bmp, 0, h, buf, cab, 0)
    g32.DeleteObject(bmp)
    g32.DeleteDC(mem)
    _u32.ReleaseDC(raiz, dc)
    img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1).convert("RGB")
    return img.crop((r.left - rv.left, r.top - rv.top, r.right - rv.left, r.bottom - rv.top))


def _color_medio(img, fx, fy, radio=3):
    w, h = img.size
    x, y = int(w * fx), int(h * fy)
    pix = [img.getpixel((min(w - 1, max(0, x + dx)), min(h - 1, max(0, y + dy))))[:3]
           for dx in range(-radio, radio + 1) for dy in range(-radio, radio + 1)]
    return tuple(sorted(p[i] for p in pix)[len(pix) // 2] for i in range(3))


def _casilla_usable(img) -> bool:
    """Las casillas vacías («-») o con material incompatible salen gris claro y con el texto desvanecido."""
    w, h = img.size
    for y in range(int(h * 0.15), int(h * 0.9), 2):
        for x in range(int(w * 0.1), int(w * 0.9), 2):
            r, g, b = img.getpixel((x, y))[:3]
            if 0.3 * r + 0.59 * g + 0.11 * b < 120 or max(r, g, b) - min(r, g, b) > 60:
                return True
    return False


def _verde(c) -> bool:
    """Los botones de Bambu están verdes cuando se pueden pulsar y grises si no."""
    r = c.BoundingRectangle
    if r.width() <= 0:
        return False
    rgb = _color_medio(_captura(r, c), 0.2, 0.5, 2)
    return rgb[1] > rgb[0] + 50 and rgb[1] > rgb[2] + 20


def _boton(ven, nombre):
    return next((x for x, _ in _recorrer(ven, 7) if x.ControlTypeName == "PaneControl" and x.Name == nombre), None)


def _escala(h) -> float:
    try:
        return ctypes.windll.user32.GetDpiForWindow(h) / 96
    except Exception:  # noqa: BLE001
        return 1.0


def _cerrar_mia():
    """Cierra la ventana que abrió JARVIS antes (sin guardar), nunca las tuyas."""
    h = ESTADO["hwnd"]
    if not h or h not in {x for x, _ in _ventanas()}:
        return
    ctypes.windll.user32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
    for _ in range(10):
        time.sleep(0.7)
        if h not in {x for x, _ in _ventanas()}:
            return
        for hw, titulo in _ventanas():
            if hw == h:
                continue
            c = _control(hw)
            no = next((x for x, _ in _recorrer(c, 6) if (x.Name or "").lower() in ("no guardar", "no", "don't save", "descartar")), None)
            if no:
                _tocar(no)
                break


def _mias() -> set[int]:
    try:
        return set(json.loads((TRABAJOS / "ventanas.json").read_text()))
    except (OSError, ValueError):
        return set()


def _anotar(h: int | None, quitar=False):
    """Las ventanas que abre JARVIS quedan anotadas para poder cerrarlas aunque se reinicie."""
    mias = _mias() & {x for x, _ in _ventanas()}
    if h:
        mias = mias - {h} if quitar else mias | {h}
    TRABAJOS.mkdir(parents=True, exist_ok=True)
    (TRABAJOS / "ventanas.json").write_text(json.dumps(sorted(mias)))


def _limpiar():
    """Cierra las ventanas que JARVIS dejó abiertas (p. ej. si lo interrumpieron); nunca las tuyas."""
    _cerrar_dialogo(todos=True)
    for h in _mias() & {x for x, _ in _ventanas()}:
        ESTADO["hwnd"] = h
        _cerrar_mia()
    _anotar(None)
    ESTADO.update(hwnd=None, abierto=None)


def _abrir_en_studio(ruta: Path) -> str:
    if not EXE.exists():
        raise RuntimeError("No encuentro Bambu Studio instalado (bambu-studio.exe).")
    _limpiar()
    antes = {h for h, _ in _ventanas()}
    subprocess.Popen([str(EXE), str(ruta)])
    for _ in range(90):
        time.sleep(1)
        nuevas = [(h, t) for h, t in _ventanas() if h not in antes and t.endswith("BambuStudio") and DIALOGO not in t]
        if nuevas:
            ESTADO.update(hwnd=nuevas[0][0], abierto=str(ruta))
            _anotar(nuevas[0][0])
            time.sleep(4)  # termina de cargar el archivo
            for h, t in _ventanas():
                if t == "Peligro":  # p. ej. «El archivo no contiene ninguna información geométrica»
                    aviso = " ".join(x.Name for x, _ in _recorrer(_control(h), 6) if x.ControlTypeName == "TextControl" and x.Name)
                    si = next((x for x, _ in _recorrer(_control(h), 6) if x.Name in ("Sí", "Si", "Aceptar", "OK")), None)
                    if si:
                        _tocar(si)
                    ESTADO["abierto"] = None
                    raise RuntimeError(f"Bambu Studio no pudo abrir {ruta.name}: {aviso or 'error al cargar'}")
            # Bambu puede preguntar algo al abrir (versión del 3MF, perfiles): acepta lo propuesto
            ven = _control(nuevas[0][0])
            for x, _ in _recorrer(ven, 4):
                if x.ControlTypeName == "WindowControl" and x.Name and x.Name != ven.Name:
                    si = next((b for b, _ in _recorrer(x, 6) if (b.Name or "").lower() in ("sí", "si", "aceptar", "ok", "yes", "cargar")), None)
                    if si:
                        _tocar(si)
            return nuevas[0][1]
    raise RuntimeError("Bambu Studio no abrió la ventana del archivo.")


def _cerrar_dialogo(todos=False):
    """todos=False: el diálogo actual. todos=True: todos los que cuelgan de ventanas de JARVIS."""
    mias = _mias() | {ESTADO["hwnd"]}
    for h, t in _ventanas():
        if t != DIALOGO or (todos and ctypes.windll.user32.GetWindow(h, 4) not in mias):  # 4 = GW_OWNER
            continue
        ctypes.windll.user32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE: igual que la X, no envía nada
        time.sleep(1)
        if not todos:
            break


def _dialogo():
    h = next((h for h, t in _ventanas() if t == DIALOGO), None)
    return _control(h) if h else None


def _abrir_dialogo(ruta: Path):
    """Abre `ruta` (un .gcode.3mf ya laminado) en Bambu Studio y su ventana «Enviar trabajo de impresión»."""
    if (d := _dialogo()) and ESTADO["abierto"] == str(ruta):
        d.SetActive()
        return d
    _cerrar_dialogo()
    h = ESTADO["hwnd"]
    if ESTADO["abierto"] != str(ruta) or not h or h not in {x for x, _ in _ventanas()}:
        _abrir_en_studio(ruta)
    ven = _control(ESTADO["hwnd"])
    ven.SetActive()
    time.sleep(0.5)
    imprimir = _boton(ven, "Imprimir placa")
    if not imprimir:
        raise RuntimeError("No encuentro el botón «Imprimir placa» en Bambu Studio.")
    if not _verde(imprimir):  # la ventana vuelve a laminar con los ajustes guardados en el archivo
        laminar = _boton(ven, "Laminar placa")
        if laminar:
            _tocar(laminar)
        limite = time.time() + 900
        while time.time() < limite and not _verde(imprimir):
            time.sleep(2)
        if not _verde(imprimir):
            raise RuntimeError("Bambu Studio no terminó de laminar (revisa si muestra un error en la placa).")
    _tocar(imprimir)
    for _ in range(20):
        time.sleep(1)
        if d := _dialogo():
            time.sleep(2)  # carga el estado de la impresora y del AMS
            return d
    raise RuntimeError("No se abrió la ventana «Enviar trabajo de impresión».")


ETIQUETAS = {"impresora", "filamento", "timelapse", "boquilla", "ams", "externo", "pei"}


def _leer_dialogo(d) -> dict:
    textos = [x for x, _ in _recorrer(d, 10) if x.ControlTypeName == "TextControl" and x.Name]
    imp = next((x for x in textos if x.Name == "Impresora"), None)
    nombre = ""
    if imp:
        caja = imp.GetParentControl()
        nombre = next((x.Name for x, _ in _recorrer(caja, 4) if x.ControlTypeName == "PaneControl" and x.Name and x.Name != "panel"), "")
    avisos = [" ".join(x.Name.split()).rstrip(".") for x in textos if len(x.Name) > 22 and x.Name.lower() not in ETIQUETAS and not x.Name.endswith("...")
              and not re.search(r"\.(3mf|stl|obj|step|stp)$", x.Name, re.I)]
    enviar = next((x for x, _ in _recorrer(d, 6) if x.ControlTypeName == "PaneControl" and x.Name == "Enviar"), None)
    return {"nombre": nombre, "avisos": avisos, "lista": bool(enviar and _verde(enviar)), "_enviar": enviar}


def _cerrar_emergentes(d):
    r = d.BoundingRectangle
    _clic(r.left + r.width() * 0.45, r.top + 60 * _escala(d.NativeWindowHandle) / 1.25, d)  # zona vacía del diálogo
    time.sleep(0.6)


def _abrir_ams(d):
    """Abre el selector del primer filamento y devuelve (popup, casillas)."""
    fil = next((x for x, _ in _recorrer(d, 4) if x.ControlTypeName == "TextControl" and x.Name == "Filamento"), None)
    if not fil:
        return None, []
    e = _escala(d.NativeWindowHandle) / 1.25  # medidas tomadas a 125 %
    antes = {h for h, _ in _ventanas()}
    r = fil.BoundingRectangle
    _clic(r.left + 58 * e, r.bottom + 60 * e, d)
    time.sleep(1.5)
    for h, _ in _ventanas():
        if h in antes:
            continue
        pop = _control(h)
        paneles = [(x, dd) for x, dd in _recorrer(pop, 6)]
        externo = next((x.BoundingRectangle.top for x, _ in paneles if x.ControlTypeName == "TextControl" and x.Name == "Externo"), 10 ** 6)
        casillas = []
        for x, _ in paneles:
            b = x.BoundingRectangle
            if x.ControlTypeName == "PaneControl" and 40 * e <= b.width() <= 90 * e and 55 * e <= b.height() <= 110 * e:
                casillas.append((x, b.top > externo))
        if casillas:
            return pop, casillas
    return None, []


def _leer_ams(d) -> list[dict]:
    pop, casillas = _abrir_ams(d)
    slots = []
    ams_n = 0
    for i, (x, es_externo) in enumerate(sorted(casillas, key=lambda c: (c[0].BoundingRectangle.top, c[0].BoundingRectangle.left))):
        b = x.BoundingRectangle
        img = _captura(b, x)
        rgb = _color_medio(img, 0.14, 0.55)
        if es_externo:
            nombre = "Externo"
        else:
            nombre = f"{'ABCD'[ams_n // 4]}{ams_n % 4 + 1}"
            ams_n += 1
        if _casilla_usable(img):
            slots.append({"casilla": nombre, "rgb": rgb, "color": nombre_color(rgb), "hex": "#%02x%02x%02x" % rgb})
    if pop:
        _cerrar_emergentes(d)
    return slots


def _lista_impresoras(d):
    """Abre el desplegable de impresoras: devuelve (popup_rect, alto_fila, filas)."""
    info = _leer_dialogo(d)
    caja = next((x for x, _ in _recorrer(d, 8) if x.ControlTypeName == "PaneControl" and x.Name == info["nombre"]), None)
    if not caja:
        raise RuntimeError("No encuentro el selector de impresora en la ventana de envío.")
    b = caja.BoundingRectangle
    nuevo = None
    for _ in range(3):  # la lista aparece justo debajo del selector
        _tocar(caja)
        time.sleep(1.5)
        for h, t in _ventanas():
            c = _control(h) if t in ("panel", "") else None
            if c and abs(c.BoundingRectangle.top - b.bottom) < 40 and c.BoundingRectangle.height() > 30:
                nuevo = h
                break
        if nuevo:
            break
    if not nuevo:
        raise RuntimeError("No se abrió la lista de impresoras.")
    r = _control(nuevo).BoundingRectangle
    img = _captura(r, nuevo)
    # la fila elegida está resaltada: su alto es el de todas las filas
    col = [img.getpixel((int(img.width * 0.5), y))[:3] for y in range(img.height)]
    claro = [sum(p) > 735 for p in col]  # blanco
    ini = next((y for y, c in enumerate(claro) if not c), 0)
    fin = next((y for y in range(ini, len(claro)) if claro[y]), ini)
    alto = fin - ini if fin - ini > 15 else int(40 * _escala(nuevo))
    filas = max(1, round((r.height() - ini) / alto))
    return r, alto, filas, ini, nuevo


def _seleccionar(d, nombre: str, pista: int = 0) -> bool:
    """Elige la impresora por su nombre: el orden de la lista cambia entre una vez y otra."""
    if _leer_dialogo(d)["nombre"] == nombre:
        return True
    _, _, filas, _, _ = _lista_impresoras(d)
    _cerrar_emergentes(d)
    for k in [pista] + [x for x in range(filas) if x != pista]:
        if _elegir_fila(d, k) and _leer_dialogo(d)["nombre"] == nombre:
            return True
    return False


def _elegir_fila(d, i):
    r, alto, filas, ini, pop = _lista_impresoras(d)
    if i >= filas:
        _cerrar_emergentes(d)
        return False
    _clic(r.left + r.width() * 0.4, r.top + ini + alto * i + alto / 2, pop)
    time.sleep(2.5)  # actualiza estado y AMS de la impresora elegida
    return True


def _sonda() -> Path:
    """Un cubito laminado: basta para abrir la ventana de envío y revisar las impresoras sin un modelo."""
    carpeta = TRABAJOS / "sonda"
    final = carpeta / "sonda.gcode.3mf"
    if final.exists():
        return final
    carpeta.mkdir(parents=True, exist_ok=True)
    v = [(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0), (0, 0, 8), (20, 0, 8), (20, 20, 8), (0, 20, 8)]  # un cubo de 10 mm lo rechaza la CLI
    caras = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    tri = array("f", (c for t in caras for i in t for c in v[i]))
    modelos3d.guardar_stl(tri, carpeta / "u0.stl")
    perfiles = laminado.escribir_perfiles(carpeta, "A1", {})
    laminado.laminar({"u0": (carpeta / "u0.stl", 1)}, carpeta, perfiles, final.name)
    return final


def _explorar() -> list[dict]:
    d = _abrir_dialogo(Path(ESTADO["abierto"]) if ESTADO["abierto"] and _dialogo() else _sonda())
    _, _, filas, _, _ = _lista_impresoras(d)
    _cerrar_emergentes(d)
    vistas = []
    for i in range(min(filas, 20)):
        if not _elegir_fila(d, i):
            break
        info = _leer_dialogo(d)
        if any(v["nombre"] == info["nombre"] for v in vistas):
            continue
        # el aviso de «no compatible» solo dice que la sonda es para A1: la impresora sigue disponible
        incompatible = any("no es compatible" in a for a in info["avisos"])
        ocupada = any(re.search(r"ocupad|desconect|offline|sin conexi|mantenimiento|error", a, re.I) for a in info["avisos"])
        if incompatible and not re.search(r"\s[–-]\s", info["nombre"]):
            m = re.search(r"\(([^)]+)\) no es compatible", " ".join(info["avisos"]))
            modelo = m.group(1) if m else None  # Bambu dice «La impresora seleccionada (X1 Carbon) no es compatible…»
        else:
            modelo = _modelo_de(info["nombre"])
        vistas.append({"fila": i, "nombre": info["nombre"], "modelo": modelo,
                       "avisos": [a for a in info["avisos"] if "no es compatible" not in a],
                       "libre": bool(modelo) and (info["lista"] or (incompatible and not ocupada)), "ams": _leer_ams(d)})
    ESTADO.update(impresoras=vistas, cuando=time.time())
    return vistas


def _modelo_de(nombre: str) -> str:
    """'Taller – X1 Carbon' -> 'X1 Carbon' (Bambu muestra «nombre – modelo»)."""
    return re.split(r"\s+[–-]\s+", nombre)[-1].strip() if re.search(r"\s[–-]\s", nombre) else "A1"


def _impresoras(refrescar=False) -> list[dict]:
    if refrescar or time.time() - ESTADO["cuando"] > 180 or not ESTADO["impresoras"]:
        return _explorar()
    return ESTADO["impresoras"]


def _resumen(imps: list[dict]) -> str:
    lineas = []
    for p in imps:
        estado = "libre" if p["libre"] else ("; ".join(p["avisos"]) or "no disponible")
        colores = ", ".join(f"{s['casilla']} {s['color']}" for s in p["ams"]) or "sin AMS visible"
        lineas.append(f"- {p['nombre']}: {estado}. Colores: {colores}")
    return "\n".join(lineas)


def _casilla_para(imp: dict, pedido):
    """La casilla del AMS que mejor coincide con el color pedido (o la primera si no se pidió color)."""
    if not imp["ams"]:
        return None
    externa = lambda s: s["casilla"] == "Externo"  # noqa: E731 — puede estar configurada sin filamento puesto
    if not pedido:
        return sorted(imp["ams"], key=externa)[0]
    s = min(imp["ams"], key=lambda s: (s["color"] != pedido[0], externa(s), _distancia(s["rgb"], pedido[1])))
    return s if s["color"] == pedido[0] or _distancia(s["rgb"], pedido[1]) < 160 else None


def _enviar_plato(archivo: Path, imp: dict, casilla: dict) -> str:
    d = _abrir_dialogo(archivo)
    if not _seleccionar(d, imp["nombre"], imp["fila"]):
        raise RuntimeError(f"no encuentro {imp['nombre']} en la lista de Bambu Studio")
    pop, casillas = _abrir_ams(d)
    casillas = sorted(casillas, key=lambda c: (c[0].BoundingRectangle.top, c[0].BoundingRectangle.left))
    nombres = [x["casilla"] for x in _leer_ams_nombres(casillas)]
    if casilla["casilla"] not in nombres:
        _cerrar_emergentes(d)
        raise RuntimeError("el AMS cambió mientras elegía")
    _tocar(casillas[nombres.index(casilla["casilla"])][0])
    time.sleep(1)
    if pop and pop.NativeWindowHandle in {h for h, _ in _ventanas()}:
        _cerrar_emergentes(d)
    info = _leer_dialogo(d)
    if not info["lista"]:
        raise RuntimeError("Bambu Studio no deja enviar: " + ("; ".join(info["avisos"]) or "el botón Enviar está desactivado"))
    _tocar(info["_enviar"])
    for _ in range(120):
        time.sleep(1)
        if not _dialogo():
            imp["libre"] = False
            return f"{imp['nombre']} ({casilla['casilla']} {casilla['color']})"
    raise RuntimeError("pulsé Enviar pero la ventana sigue abierta; revisa Bambu Studio")


# ───────────────────────── plan de impresión ─────────────────────────
def _unidades(modelo: modelos3d.Modelo, piezas: str | None, copias: int) -> list[tuple[modelos3d.Pieza, int]]:
    """'p0:2, g1, cuerpo 3 x4' -> [(pieza, copias)]. Vacío o «todas» = todas las piezas, una de cada."""
    if not piezas or _sin_tildes(piezas).strip() in ("todas", "todo", "all", "*"):
        return [(p, copias) for p in modelo.piezas]
    elegidas: dict[str, int] = {}
    for trozo in re.split(r"[,;\n]+", piezas):
        trozo = trozo.strip()
        if not trozo:
            continue
        m = re.match(r"^(.*?)\s*(?:[:x×*]\s*(\d+))?$", trozo, re.I)
        clave, n = m.group(1).strip(), int(m.group(2) or 1)
        if clave in modelo.grupos:
            encontradas = modelo.de_grupo(clave)
        else:
            encontradas = [p for p in modelo.piezas if p.id == clave] or \
                          [p for p in modelo.piezas if _sin_tildes(clave) and _sin_tildes(clave) == _sin_tildes(p.nombre)] or \
                          [p for p in modelo.piezas if _sin_tildes(clave) and _sin_tildes(clave) in _sin_tildes(p.nombre)]
        if not encontradas:
            raise ValueError(f"No encuentro la pieza «{clave}». Piezas:\n{modelo.resumen()}")
        for p in encontradas:
            elegidas[p.id] = elegidas.get(p.id, 0) + n
    return [(p, elegidas[p.id] * copias) for p in modelo.piezas if p.id in elegidas]


def _tiempo(s: int) -> str:
    h, m = divmod(round(s / 60), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def _planear(a: dict) -> dict:
    modelo = ESTADO["modelo"]
    if a.get("archivo") and (not modelo or str(modelo.ruta) != a["archivo"]):
        modelo = _cargar(Path(a["archivo"]))
    if not modelo:
        raise RuntimeError("Primero abre un modelo (dime qué archivo quieres imprimir).")
    copias = max(1, min(500, int(a.get("copias") or 1)))
    unidades = _unidades(modelo, a.get("piezas"), copias)
    if not unidades:
        raise RuntimeError("No hay piezas elegidas.")
    cambios = laminado.ajustes(a.get("perfil") or "basico", a.get("paredes"), a.get("relleno"), a.get("patron"),
                               a.get("soportes"), a.get("altura_capa"), a.get("borde"))
    orientar = _sin_tildes(str(a.get("orientar", "si"))) not in ("no", "false", "0")
    material = (a.get("material") or "PLA").upper()
    pedido = color_pedido(a["color"]) if a.get("color") and _sin_tildes(a["color"]) not in ("cualquiera", "auto", "") else None
    if a.get("color") and not pedido and _sin_tildes(a["color"]) not in ("cualquiera", "auto"):
        raise ValueError(f"No reconozco el color «{a['color']}». Dime uno como rojo, negro, azul claro o un código #rrggbb.")

    clave = json.dumps([str(modelo.ruta), [(p.id, n) for p, n in unidades], cambios, orientar, material], sort_keys=True)
    carpeta = TRABAJOS / hashlib.sha1(clave.encode()).hexdigest()[:12]
    carpeta.mkdir(parents=True, exist_ok=True)
    stls = {}
    for k, (p, n) in enumerate(unidades):
        ruta = carpeta / f"u{k}.stl"
        if not ruta.exists():
            modelos3d.guardar_stl(p.tri, ruta)
        stls[f"u{k}"] = (ruta, n, p)

    # impresoras: libres, con el color pedido, y si el usuario nombró alguna, solo esas
    imps = _impresoras(bool(a.get("refrescar")))
    if a.get("impresora"):
        nombres = [_sin_tildes(x) for x in re.split(r"[,;]| y ", a["impresora"]) if x.strip()]
        imps = [p for p in imps if any(n.strip() in _sin_tildes(p["nombre"]) for n in nombres)]
    candidatas = [(p, c) for p in imps if p["libre"] for c in [_casilla_para(p, pedido)] if c]
    candidatas.sort(key=lambda pc: (pc[1]["color"] != (pedido[0] if pedido else pc[1]["color"]), pc[0]["fila"]))

    modelo_cama = candidatas[0][0]["modelo"] if candidatas else "A1"
    base = carpeta / _sin_tildes(modelo_cama).replace(" ", "_")
    base.mkdir(exist_ok=True)
    perfiles = laminado.escribir_perfiles(base, modelo_cama, cambios, material)
    platos, giros = laminado.organizar({k: (r, n) for k, (r, n, _) in stls.items()}, base, perfiles, orientar)
    if orientar:  # cada pieza queda girada como la orientó Bambu; luego solo se acomoda en la cama
        for k, (ruta, n, p) in list(stls.items()):
            girada = carpeta / f"girada_{k}.stl"
            if k in giros and not girada.exists():
                modelos3d.guardar_stl(modelos3d.girar(p.tri, giros[k]), girada)
            if girada.exists():
                stls[k] = (girada, n, p)

    plan = {"modelo": str(modelo.ruta), "carpeta": str(carpeta), "cambios": cambios, "material": material, "orientar": orientar,
            "color": pedido[0] if pedido else None, "platos": [], "creado": time.time(), "clave": clave}
    for i, cuenta in enumerate(platos):
        plato = {"n": i + 1, "piezas": cuenta, "impresora": None, "casilla": None, "archivo": None, "segundos": 0, "gramos": 0.0}
        # una impresora con cama más chica (p. ej. A1 mini) puede no tener espacio: se prueba la siguiente
        while candidatas:
            imp, casilla = candidatas.pop(0)
            datos = _laminar_plato(carpeta, stls, cuenta, imp["modelo"], cambios, material, orientar, i + 1)
            if datos["subplatos"] == 1:
                plato.update(impresora=imp, casilla=casilla, **datos)
                break
        plan["platos"].append(plato)
    for plato in plan["platos"]:  # los que quedan en espera igual muestran su tiempo (calculado para la A1)
        if not plato["archivo"]:
            datos = _laminar_plato(carpeta, stls, plato["piezas"], modelo_cama, cambios, material, orientar, plato["n"])
            plato.update(segundos=datos["segundos"], gramos=datos["gramos"])
    plan["texto"] = _texto_plan(plan, stls, imps, pedido)
    ESTADO["plan"] = plan
    return plan


def _laminar_plato(carpeta, stls, cuenta, modelo_imp, cambios, material, orientar, n) -> dict:
    sub = carpeta / _sin_tildes(modelo_imp).replace(" ", "_")
    sub.mkdir(exist_ok=True)
    archivo = sub / f"plato_{n}.gcode.3mf"
    if not archivo.exists():
        perfiles = laminado.escribir_perfiles(sub, modelo_imp, cambios, material)
        info = laminado.laminar({k: (stls[k][0], c) for k, c in cuenta.items()}, sub, perfiles, archivo.name)
        (sub / f"plato_{n}.json").write_text(json.dumps(info), encoding="utf-8")
    info = json.loads((sub / f"plato_{n}.json").read_text(encoding="utf-8"))
    extra = f" (Bambu lo partió en {len(info)} platos)" if len(info) > 1 else ""
    return {"archivo": str(archivo), "segundos": sum(x["segundos"] for x in info), "gramos": sum(x["gramos"] for x in info),
            "nota": extra, "subplatos": len(info)}


def _texto_plan(plan, stls, imps, pedido) -> str:
    nombres = {k: v[2].nombre for k, v in stls.items()}
    total = sum(n for _, n, _ in stls.values())
    c = plan["cambios"]
    ajustes_txt = ", ".join(f"{k}={v}" for k, v in c.items()) or "perfil básico (0,20 mm, 2 paredes, 15 % de relleno, sin soportes)"
    lineas = [f"{total} pieza(s) en {len(plan['platos'])} plato(s) · {plan['material']}{' ' + pedido[0] if pedido else ''} · {ajustes_txt}"]
    for p in plan["platos"]:
        piezas = ", ".join(f"{n}× {nombres[k]}" for k, n in p["piezas"].items())
        t = f" · {_tiempo(p['segundos'])}, {p['gramos']:.0f} g" if p["segundos"] else ""
        destino = f"→ {p['impresora']['nombre']} ({p['casilla']['casilla']} {p['casilla']['color']})" if p["impresora"] else "→ en espera (no hay otra impresora libre con ese color)"
        lineas.append(f"Plato {p['n']}{t}: {piezas} {destino}{p.get('nota', '')}")
    if not any(p["impresora"] for p in plan["platos"]):
        lineas.append("Ninguna impresora libre tiene ese color ahora:\n" + _resumen(imps))
    return "\n".join(lineas)


def _imprimir(a: dict) -> str:
    plan = ESTADO.get("plan")
    if not a.get("pendientes") or not plan:
        plan = _planear(a)
    else:  # reparte los platos que quedaron en espera entre las impresoras que se liberaron
        modelo_cama = "A1"
        imps = _impresoras(True)
        pedido = color_pedido(plan["color"]) if plan["color"] else None
        libres = [(p, c) for p in imps if p["libre"] for c in [_casilla_para(p, pedido)] if c]
        carpeta = Path(plan["carpeta"])
        stls = {f"u{k}": (carpeta / f"u{k}.stl", 0, None) for k in range(500) if (carpeta / f"u{k}.stl").exists()}
        for plato in plan["platos"]:
            if not plato["impresora"] and not plato.get("enviado") and libres:
                imp, casilla = libres.pop(0)
                plato.update(impresora=imp, casilla=casilla)
                plato.update(_laminar_plato(carpeta, stls, plato["piezas"], imp["modelo"] or modelo_cama, plan["cambios"],
                                            plan["material"], plan["orientar"], plato["n"]))
    enviados, errores = [], []
    for plato in plan["platos"]:
        if not plato["impresora"] or plato.get("enviado"):
            continue
        try:
            enviados.append(f"Plato {plato['n']} → " + _enviar_plato(Path(plato["archivo"]), plato["impresora"], plato["casilla"]))
            plato["enviado"] = True
            _guardar_trabajos(_trabajos() + [{"impresora": plato["impresora"]["nombre"].split(" (")[0], "plato": plato["n"],
                                              "modelo": Path(plan["modelo"]).name, "desde": time.time()}])
        except Exception as e:  # noqa: BLE001
            errores.append(f"Plato {plato['n']}: {e}")
    espera = [p["n"] for p in plan["platos"] if not p.get("enviado")]
    partes = []
    if enviados:
        partes.append("Enviado:\n" + "\n".join(enviados))
    if errores:
        partes.append("No se pudo:\n" + "\n".join(errores))
    if espera:
        partes.append(f"En espera: plato(s) {', '.join(map(str, espera))}. Cuando se libere una impresora, pídeme «imprime los pendientes».")
    return "\n\n".join(partes) or plan["texto"]


def _cargar(ruta: Path) -> modelos3d.Modelo:
    modelo = modelos3d.cargar(ruta, EXE)
    ESTADO.update(modelo=modelo, plan=None)
    return modelo


def _leer_ams_nombres(casillas):
    n = 0
    out = []
    for _, externo in casillas:
        out.append({"casilla": "Externo" if externo else f"{'ABCD'[n // 4]}{n % 4 + 1}"})
        n += 0 if externo else 1
    return out


async def _en_hilo(fn, *args):
    def correr():
        import uiautomation as auto

        with auto.UIAutomationInitializerInThread():
            return fn(*args)
    return await asyncio.get_running_loop().run_in_executor(_hilo, correr)


# ───────────────────────── vigilancia de impresiones ─────────────────────────
# La pestaña «Multidispositivo» de Bambu Studio (Preferencias → Administración de varios dispositivos) muestra
# hasta 6 impresoras con su trabajo y estado; se lee con el OCR de Windows. El detalle (porcentaje, capa,
# error con código) sale de la pestaña Dispositivo al pulsar «Vista».
REGISTRO_TRABAJOS = TRABAJOS / "trabajos.json"
CADA = 180  # segundos entre revisiones
ATASCADA = 15 * 60  # sin avanzar este tiempo = algo pasa (filamento, atasco…)
MALOS = re.compile(r"fallo|error|pausa|detenid|cancelad|introduzca|inserte|atasc|obstru", re.I)


def _clave(nombre: str) -> str:
    """'Taller — A1' / 'Taller – A1 (Fuera de lí…' -> 'taller' (el OCR confunde guiones y letras)."""
    return re.sub(r"[^a-z0-9]", "", _sin_tildes(re.split(r"\s*[–—-]\s*|\(", nombre)[0]))


def _trabajos() -> list[dict]:
    try:
        return json.loads(REGISTRO_TRABAJOS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _guardar_trabajos(lista: list[dict]):
    TRABAJOS.mkdir(parents=True, exist_ok=True)
    REGISTRO_TRABAJOS.write_text(json.dumps(lista, ensure_ascii=False), encoding="utf-8")


def _ventana_vigia():
    """Una ventana de Bambu Studio de JARVIS (si no hay, abre la sonda)."""
    h = next((h for h in _mias() if h in {x for x, _ in _ventanas()}), None)
    if not h:
        _abrir_en_studio(_sonda())
        h = ESTADO["hwnd"]
    ESTADO["hwnd"] = h
    if _u32.IsIconic(h):  # minimizada no se puede leer: se restaura sin quitarte el foco
        _u32.ShowWindow(h, 4)  # SW_SHOWNOACTIVATE
        time.sleep(1.5)
    return _control(h)


def _pestana(ven, nombre: str):
    b = next((x for x, _ in _recorrer(ven, 5) if (x.Name or "").strip() == nombre), None)
    if not b:
        raise RuntimeError(f"Bambu Studio no muestra la pestaña «{nombre}» (actívala en Preferencias → "
                           "Administración de varios dispositivos y reinicia Bambu Studio)." if nombre == "Multidispositivo" else
                           f"No encuentro la pestaña «{nombre}».")
    _tocar(b)
    time.sleep(2.5)


def _tabla() -> list[dict]:
    """Filas de la pestaña Multidispositivo: [{nombre, tarea, estado, y}]"""
    from . import ocr

    ven = _ventana_vigia()
    _pestana(ven, "Multidispositivo")
    r = ven.BoundingRectangle
    lineas = ocr.leer(_captura(r, ven))
    cab = {l["texto"].split(",")[-1].strip(): l["caja"] for l in lineas if "Nombre del dispositivo" in l["texto"] or
           "Nombre de la tarea" in l["texto"] or "Estado del dispositivo" in l["texto"] or l["texto"] == "Acciones"}
    col = {k: v[0] for k, v in cab.items()}
    if "Nombre del dispositivo" not in col:
        return []
    x_tarea, x_estado = col.get("Nombre de la tarea", 10 ** 6), col.get("Estado del dispositivo", 10 ** 6)
    x_acc = col.get("Acciones", 10 ** 6)
    y_cab = cab["Nombre del dispositivo"][3]
    filas: dict[int, dict] = {}
    for l in lineas:
        x0, y0, x1, y1 = l["caja"]
        if y0 <= y_cab or x0 < col["Nombre del dispositivo"] - 5:
            continue
        fila = filas.setdefault(round((y0 + y1) / 2 / 20), {"y": r.top + (y0 + y1) / 2, "nombre": "", "tarea": "", "estado": ""})
        campo = "nombre" if x0 < x_tarea - 5 else "tarea" if x0 < x_estado - 5 else "estado" if x0 < x_acc - 5 else None
        if campo:
            fila[campo] = (fila[campo] + " " + l["texto"]).strip()
    out = [f for f in filas.values() if f["nombre"]]
    for f in out:
        f["clave"], f["vista_x"] = _clave(f["nombre"]), r.left + x_acc + 50
        f["fuera"] = "fuera de l" in f["nombre"].lower()
    return sorted(out, key=lambda f: f["y"])


def _detalle(fila: dict) -> dict:
    """Pulsa «Vista» y lee la pestaña Dispositivo: archivo, %, capa, restante, estado y error."""
    ven = _ventana_vigia()
    _clic(fila["vista_x"], fila["y"], ven)
    time.sleep(3)
    textos = [(x.Name or "").strip() for x, _ in _recorrer(ven, 16) if x.ControlTypeName == "TextControl" and (x.Name or "").strip()]
    d = {"porcentaje": None, "capa": None, "restante": None, "estado": "", "error": None}
    if "Progreso de la impresión" in textos:
        resto = textos[textos.index("Progreso de la impresión") + 1:]
        d["archivo"] = resto[0] if resto else ""
        for k, t in enumerate(resto):
            if t == "%" and k and resto[k - 1].isdigit():
                d["porcentaje"] = int(resto[k - 1])
            elif t.startswith("Capa:"):
                d["capa"] = t[5:].strip()
            elif re.fullmatch(r"-?\d+h?\d*min\d*s?|-?\d+s", t):
                d["restante"] = t.lstrip("-")
            elif t.startswith("Tiempo estimado") and k:
                d["estado"] = resto[k - 1] if not resto[k - 1].startswith(("Capa", "-")) else ""
    for k, t in enumerate(textos):
        m = re.search(r"\[([0-9A-F]{4}-[0-9A-F]{4})", t)
        if m:
            d["error"] = (" ".join(textos[k - 1].split()) if k else "") + f" [{m.group(1)}]"
            break
    return d


def _resumen_estado(filas: list[dict], detalles: dict) -> str:
    lineas = []
    for f in filas:
        d = detalles.get(f["clave"]) or {}
        partes = [f["estado"] or "?"]
        if f["tarea"] and "ninguna" not in f["tarea"].lower():
            partes.insert(0, f["tarea"])
        if d.get("porcentaje") is not None:
            partes.append(f"{d['porcentaje']} %")
        if d.get("capa"):
            partes.append(f"capa {d['capa']}")
        if d.get("restante"):
            partes.append(f"faltan {d['restante']}")
        if d.get("error"):
            partes.append("⚠ " + d["error"])
        lineas.append(f"- {f['nombre'].split('(')[0].strip()}: " + " · ".join(partes) + (" (fuera de línea)" if f["fuera"] else ""))
    return "\n".join(lineas)


def _boton_control(ven, cual: str):
    """Los botones ⏸/▶ y ■ están dibujados a la derecha de la barra de progreso: se ubican por color."""
    capa = next((x for x, _ in _recorrer(ven, 16) if x.ControlTypeName == "TextControl" and (x.Name or "").startswith("Capa:")), None)
    if not capa:
        raise RuntimeError("no veo un trabajo en curso en esa impresora")
    vr, cr = ven.BoundingRectangle, capa.BoundingRectangle
    img = _captura(vr, ven)
    x0, x1 = cr.right - vr.left + 120, min(img.width - 1, cr.right - vr.left + 320)
    y0, y1 = cr.top - vr.top, min(img.height - 1, cr.bottom - vr.top + 60)
    manchas, ini = [], None
    for x in range(x0, x1):
        ys = [y for y in range(y0, y1) if sum(img.getpixel((x, y))) < 500]
        if ys and ini is None:
            ini, yy = x, ys
        elif ys:
            yy += ys
        elif ini is not None:
            manchas.append(((ini + x) / 2 + vr.left, sum(yy) / len(yy) + vr.top))
            ini = None
    if len(manchas) < 2:
        raise RuntimeError("no encuentro los botones de pausa y detener")
    return manchas[-1] if cual == "detener" else manchas[-2]


def _controlar(impresora: str, accion: str) -> str:
    filas = _tabla()
    clave = _clave(impresora)
    fila = next((f for f in filas if clave and clave in f["clave"]), None)
    if not fila:
        return f"«{impresora}» no está en la vista de varios dispositivos. Veo: " + ", ".join(f["nombre"] for f in filas)
    _detalle(fila)
    ven = _ventana_vigia()
    ven.SetActive()  # los diálogos de confirmación de Bambu solo responden con la ventana al frente
    time.sleep(0.4)
    antes = {h for h, _ in _ventanas()}
    _clic(*_boton_control(ven, "detener" if accion == "cancelar" else "pausa"), ven)
    time.sleep(2)
    for h, t in _ventanas():
        if h in antes:
            continue
        c = _control(h)
        si = next((x for x, _ in _recorrer(c, 8) if (x.Name or "") in ("Sí", "Si", "Aceptar", "OK")), None)
        if si:
            _tocar(si)
            time.sleep(2)
    time.sleep(5)
    nueva = next((f for f in _tabla() if f["clave"] == fila["clave"]), fila)
    if accion == "cancelar":
        _guardar_trabajos([t for t in _trabajos() if _clave(t["impresora"]) != fila["clave"]])
    estado = nueva["estado"] or "sin estado"
    if accion == "cancelar" and "fallo" in estado.lower():
        estado = "cancelada (Bambu la marca como «Fallo en la impresión»)"
    return f"{fila['nombre'].split('(')[0].strip()} → {accion}: ahora está {estado}" +         (f" · tarea {nueva['tarea']}" if nueva["tarea"] and "ninguna" not in nueva["tarea"].lower() else "")


def _revisar() -> tuple[list[str], str]:
    """Una revisión: devuelve (avisos nuevos, resumen). Avisa de errores y de impresiones terminadas."""
    filas = _tabla()
    trabajos = _trabajos()
    avisos, detalles = [], {}
    for t in trabajos:
        f = next((x for x in filas if x["clave"] == _clave(t["impresora"])), None)
        if not f:
            continue
        d = _detalle(f) if not f["fuera"] else {}
        detalles[f["clave"]] = d
        problema = d.get("error") or (f["estado"] if MALOS.search(f["estado"]) else None)
        if "finalizad" in f["estado"].lower() and time.time() - t["desde"] > 120:
            avisos.append(f"✅ {t['impresora']} terminó el plato {t['plato']} ({t['modelo']}).")
            t["fin"] = True
        elif problema and problema != t.get("aviso"):
            avisos.append(f"⚠ {t['impresora']} se detuvo con el plato {t['plato']} ({t['modelo']}): {problema}")
            t["aviso"] = problema
        if d.get("porcentaje") is not None and d["porcentaje"] != t.get("porcentaje"):
            if t.get("porcentaje") is not None:  # la primera lectura no cuenta como avance
                t["avanzo"] = time.time()
            t["porcentaje"] = d["porcentaje"]
        # sin mensaje de error pero sin avanzar (p. ej. esperando filamento): también se avisa, una vez
        quieto = time.time() - t.get("avanzo", t["desde"])
        if not t.get("fin") and not f["fuera"] and quieto > ATASCADA and not t.get("aviso_quieto"):
            avisos.append(f"⚠ {t['impresora']} lleva {int(quieto // 60)} min sin avanzar con el plato {t['plato']} "
                          f"({f['estado'] or 'sin estado'}, {t.get('porcentaje', 0)} %). Revisa la impresora.")
            t["aviso_quieto"] = True
    _guardar_trabajos([t for t in trabajos if not t.get("fin")])
    return avisos, _resumen_estado(filas, detalles)


def _cola() -> list[str]:
    """Manda los platos en espera del último plan a las impresoras que se liberaron."""
    plan = ESTADO.get("plan")
    if not plan or not any(not p["impresora"] and not p.get("enviado") for p in plan["platos"]):
        return []
    res = _imprimir({"pendientes": "si"})
    return [f"🖨 Cola de impresión: {res}"] if res.startswith("Enviado") else []


async def vigilante(avisar):
    """Tarea de fondo del PC: cada pocos minutos revisa las impresoras con trabajos de JARVIS."""
    if os.name != "nt":
        return
    while True:
        await asyncio.sleep(CADA)
        if not _trabajos() and not (ESTADO.get("plan") and any(not p.get("enviado") for p in ESTADO["plan"]["platos"])):
            continue
        try:
            avisos, _ = await _en_hilo(_revisar)
            avisos += await _en_hilo(_cola)
        except Exception as e:  # noqa: BLE001 — una revisión fallida no detiene la vigilancia
            log.warning("vigilancia de impresoras: %s", e)
            continue
        for texto in avisos:
            await avisar(texto)


# ───────────────────────── herramientas ─────────────────────────
def _resolver(nombre: str) -> list[Path]:
    p = Path(os.path.expandvars(os.path.expanduser(nombre.strip('"'))))
    if p.is_file():
        return [p]
    palabras = [_sin_tildes(x) for x in re.findall(r"[\w]+", nombre) if len(x) > 1 and _sin_tildes(x) not in ("stl", "3mf", "obj", "step")]
    casa = Path.home()
    raices = [casa / "Desktop", casa / "OneDrive" / "Desktop", casa / "Downloads", casa / "Documents", casa / "OneDrive" / "Documents"]
    hallados = []
    for raiz in (r for r in raices if r.is_dir()):
        for actual, dirs, ficheros in os.walk(raiz):
            dirs[:] = [x for x in dirs if not x.startswith(".") and x not in (".venv", "node_modules", "AppData")]
            for f in ficheros:
                if f.lower().endswith(EXTENSIONES) and not f.lower().endswith(".gcode.3mf") and all(w in _sin_tildes(f) for w in palabras):
                    ruta = Path(actual) / f
                    hallados.append((ruta.stat().st_mtime, ruta))
    return [r for _, r in sorted(hallados, reverse=True)[:8]]


AJUSTES = {
    "piezas": P("string", "Qué imprimir: 'todas' (por defecto), o ids/nombres con copias opcionales, ej. 'p0:2, p3, cuerpo 5 x4' o 'g1' (un grupo entero)"),
    "copias": P("string", "Cuántas veces imprimir lo elegido (por defecto 1)"),
    "perfil": P("string", "basico (por defecto: 0,20 mm, 2 paredes, 15 %), resistente, rapido o detalle", ["basico", "resistente", "rapido", "detalle"]),
    "paredes": P("string", "Número de paredes (1-20), opcional"),
    "relleno": P("string", "Relleno en %, ej. '20', opcional"),
    "patron": P("string", "Patrón de relleno: gyroid, rejilla, cubico, panal, lineas, rayo (opcional)"),
    "soportes": P("string", "no, auto (árbol), normal o cama (solo desde la cama), opcional"),
    "altura_capa": P("string", "Altura de capa en mm, ej. '0.16', opcional"),
    "borde": P("string", "Borde de adherencia (brim): auto, si o no (opcional)"),
    "material": P("string", "PLA (por defecto), PETG, ABS, ASA, TPU…"),
    "color": P("string", "Color del filamento, ej. 'rojo', 'negro', 'azul claro', '#ff8800' o 'cualquiera'"),
    "impresora": P("string", "Nombre(s) de impresora si el usuario eligió, ej. 'taller' o 'taller, oficina' (opcional)"),
    "orientar": P("string", "si (por defecto) para que Bambu oriente cada pieza; no para dejarlas como en el diseño"),
    "archivo": P("string", "Ruta del modelo (solo si no es el último abierto)"),
}


@herramienta("modelo_3d_abrir", "Abre un modelo 3D (STL, 3MF, OBJ, STEP) para imprimir: lo busca en el PC, lo separa en piezas "
             "(componentes del diseño o cuerpos sueltos) y muestra un visor 3D interactivo en las pantallas del usuario, donde puede "
             "elegir piezas, copias y ajustes. Primer paso de toda impresión 3D.",
             {"archivo": P("string", "Nombre aproximado o ruta del archivo, ej. 'soporte camara' o 'fantasma.3mf'")}, ["archivo"], solo_pc=True)
async def modelo_3d_abrir(ctx, archivo):
    rutas = await asyncio.to_thread(_resolver, archivo)
    if not rutas:
        return f"No encontré modelos 3D con «{archivo}» en Escritorio, Descargas ni Documentos."
    if len(rutas) > 1 and not Path(archivo).is_file():
        exactos = [r for r in rutas if _sin_tildes(r.stem) == _sin_tildes(Path(archivo).stem)]
        if len(exactos) != 1:
            return "Encontré varios, ¿cuál abro?\n" + "\n".join(f"- {r}" for r in rutas)
        rutas = exactos
    try:
        modelo = await asyncio.to_thread(_cargar, rutas[0])
    except Exception as e:  # noqa: BLE001
        return f"No pude leer {rutas[0].name}: {e}"
    visor = TRABAJOS / "visor"
    visor.mkdir(parents=True, exist_ok=True)
    glb = visor / (re.sub(r"[^\w.-]+", "_", modelo.ruta.stem)[:60] + ".glb")
    await asyncio.to_thread(modelos3d.guardar_glb, modelo, glb)
    from .herramientas import enviar_archivo

    await enviar_archivo(ctx, str(glb))
    avisos = (" " + " ".join(modelo.avisos)) if modelo.avisos else ""
    return (f"Abrí {modelo.ruta.name}: {len(modelo.piezas)} pieza(s) en {len(modelo.grupos)} grupo(s). El visor 3D ya está en las "
            f"pantallas del usuario (puede girarlo, elegir piezas, copias y ajustes y pulsar Imprimir).{avisos}\nPiezas:\n{modelo.resumen()}\n"
            "Si el usuario lo pide por voz: impresion_3d_planear para ver platos y tiempos, imprimir_3d para enviarlo.")


@herramienta("impresoras_3d", "Lista las impresoras Bambu Lab de la cuenta del usuario: si están libres u ocupadas y qué colores "
             "tienen cargados en el AMS (tarda ~10 s por impresora).", solo_pc=True, timeout=600)
async def impresoras_3d(ctx):
    try:
        return _resumen(await _en_hilo(_explorar)) or "Bambu Studio no muestra impresoras (¿iniciaste sesión?)."
    except Exception as e:  # noqa: BLE001
        return f"No pude revisar las impresoras: {e}"


@herramienta("impresion_3d_planear", "Calcula cómo se imprimiría el modelo abierto SIN enviarlo: orienta y organiza las piezas en "
             "platos, reparte los platos entre impresoras libres con el color pedido y da tiempo y gramos de cada plato.",
             AJUSTES, solo_pc=True, timeout=1800)
async def impresion_3d_planear(ctx, **a):
    try:
        return (await _en_hilo(_planear, a))["texto"]
    except Exception as e:  # noqa: BLE001
        return f"No pude planear la impresión: {e}"


@herramienta("imprimir_3d", "Imprime el modelo abierto: elige piezas y copias, aplica los ajustes, orienta y organiza los platos "
             "(si no caben en una impresora usa varias) y envía cada plato por la nube de Bambu a una impresora libre con el color pedido. "
             "Con pendientes='si' envía los platos que quedaron en espera.",
             {**AJUSTES, "pendientes": P("string", "'si' para enviar los platos en espera del último plan")},
             peligrosa=True, solo_pc=True, timeout=3600)
async def imprimir_3d(ctx, **a):
    try:
        return await _en_hilo(_imprimir, a)
    except Exception as e:  # noqa: BLE001
        return f"No pude enviar la impresión: {e}"


@herramienta("estado_impresoras_3d", "Estado en vivo de las impresoras Bambu Lab: qué imprime cada una, porcentaje, capa, "
             "tiempo restante y errores (p. ej. «se acabó el filamento»). Para «¿cómo va la impresión?».", solo_pc=True, timeout=600)
async def estado_impresoras_3d(ctx):
    def leer():
        filas = _tabla()
        detalles = {f["clave"]: _detalle(f) for f in filas if f["tarea"] and "ninguna" not in f["tarea"].lower() and not f["fuera"]}
        return _resumen_estado(filas, detalles)
    try:
        return await _en_hilo(leer) or "La vista de varios dispositivos de Bambu Studio está vacía."
    except Exception as e:  # noqa: BLE001
        return f"No pude leer el estado de las impresoras: {e}"


@herramienta("controlar_impresion_3d", "Pausa, reanuda o cancela la impresión en curso de una impresora Bambu Lab.",
             {"impresora": P("string", "Nombre de la impresora, ej. 'Taller'"),
              "accion": P("string", "pausar, reanudar o cancelar", ["pausar", "reanudar", "cancelar"])},
             ["impresora", "accion"], peligrosa=True, solo_pc=True, timeout=300)
async def controlar_impresion_3d(ctx, impresora, accion):
    try:
        return await _en_hilo(_controlar, impresora, accion)
    except Exception as e:  # noqa: BLE001
        return f"No pude {accion} la impresión: {e}"
