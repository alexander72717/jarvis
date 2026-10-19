"""Impresión 3D con Bambu Lab: abrir un modelo, previsualizarlo y mandarlo a la impresora libre que tenga el color pedido.

Bambu solo deja a programas externos enviar trabajos en «Modo desarrollador» (LAN, sin nube). Para conservar la nube
y Bambu Handy, JARVIS maneja Bambu Studio como lo harías tú: abre el archivo, lamina y usa «Enviar trabajo de
impresión», que ya lista todas las impresoras de tu cuenta (en casa o fuera) con su estado y los colores del AMS.
Requisitos: Bambu Studio instalado y con tu cuenta iniciada. Mientras trabaja, JARVIS mueve el ratón en esa ventana.
"""
import asyncio
import ctypes
import math
import os
import re
import struct
import subprocess
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path

from . import config
from .herramientas import P, _sin_tildes, herramienta

EXTENSIONES = (".3mf", ".stl", ".obj", ".step", ".stp")
EXE = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Bambu Studio" / "bambu-studio.exe"
DIALOGO = "Enviar trabajo de impresión"
PREVIAS = config.DATOS / "previas3d"
# UI Automation y los clics van siempre por un mismo hilo, uno a la vez
_hilo = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bambu")
ESTADO = {"ruta": None, "hwnd": None, "impresoras": [], "cuando": 0.0}

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


# ───────────────────────── previsualización ─────────────────────────
def _triangulos_stl(datos: bytes):
    if len(datos) >= 84 and 84 + 50 * struct.unpack_from("<I", datos, 80)[0] == len(datos):
        n = struct.unpack_from("<I", datos, 80)[0]
        for i in range(n):
            v = struct.unpack_from("<9f", datos, 84 + 50 * i + 12)
            yield (v[0:3], v[3:6], v[6:9])
        return
    nums = re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", datos)
    for i in range(0, len(nums) - 2, 3):
        yield tuple(tuple(float(x) for x in nums[i + k]) for k in range(3))


def _triangulos_3mf(ruta: Path):
    with zipfile.ZipFile(ruta) as z:
        for nombre in z.namelist():
            if not nombre.endswith(".model"):
                continue
            xml = z.read(nombre)
            vs = [tuple(float(x) for x in m) for m in re.findall(rb'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', xml)]
            for a, b, c in re.findall(rb'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"', xml):
                yield vs[int(a)], vs[int(b)], vs[int(c)]


def _triangulos_obj(texto: str):
    vs = []
    for linea in texto.splitlines():
        p = linea.split()
        if not p:
            continue
        if p[0] == "v":
            vs.append(tuple(float(x) for x in p[1:4]))
        elif p[0] == "f":
            idx = [int(x.split("/")[0]) for x in p[1:]]
            idx = [i - 1 if i > 0 else len(vs) + i for i in idx]
            for k in range(1, len(idx) - 1):
                yield vs[idx[0]], vs[idx[k]], vs[idx[k + 1]]


def _renderizar(tris: list, salida: Path, lado=640):
    """Vista isométrica con sombreado plano, en Python puro (sin numpy)."""
    from PIL import Image, ImageDraw

    if len(tris) > 120_000:  # recorta los modelos enormes: para una vista previa sobra
        paso = len(tris) / 120_000
        tris = [tris[int(i * paso)] for i in range(120_000)]
    xs = [v[0] for t in tris for v in t]
    ys = [v[1] for t in tris for v in t]
    zs = [v[2] for t in tris for v in t]
    medidas = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    cx, cy, cz = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, (max(zs) + min(zs)) / 2
    ca, sa = math.cos(math.radians(-35)), math.sin(math.radians(-35))
    ce, se = math.cos(math.radians(55)), math.sin(math.radians(55))

    def ver(v):
        x, y, z = v[0] - cx, v[1] - cy, v[2] - cz
        x, y = x * ca - y * sa, x * sa + y * ca          # gira alrededor de Z
        y, z = y * ce - z * se, y * se + z * ce          # inclina la cámara
        return x, -z, y                                   # pantalla x, pantalla y, profundidad

    luz = (-0.4, -0.6, -0.7)
    norma = math.sqrt(sum(c * c for c in luz))
    luz = tuple(c / norma for c in luz)
    caras = []
    for t in tris:
        a, b, c = ver(t[0]), ver(t[1]), ver(t[2])
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        w = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
        ln = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) or 1
        brillo = abs(sum(n[i] / ln * luz[i] for i in range(3)))  # abs: STL con normales al revés también se ven
        caras.append((a[2] + b[2] + c[2], (a, b, c), brillo))
    caras.sort(key=lambda f: f[0], reverse=True)          # pintor: de atrás hacia adelante
    px = [p[0] for f in caras for p in f[1]]
    py = [p[1] for f in caras for p in f[1]]
    ancho, alto = (max(px) - min(px)) or 1, (max(py) - min(py)) or 1
    s = 2
    escala = (lado * s * 0.86) / max(ancho, alto)
    ox = lado * s / 2 - (max(px) + min(px)) / 2 * escala
    oy = lado * s / 2 - (max(py) + min(py)) / 2 * escala
    img = Image.new("RGB", (lado * s, lado * s), (24, 28, 36))
    d = ImageDraw.Draw(img)
    for _, pts, brillo in caras:
        k = 0.35 + 0.65 * brillo
        col = (int(95 * k + 20), int(185 * k + 20), int(215 * k + 20))
        d.polygon([(ox + p[0] * escala, oy + p[1] * escala) for p in pts], fill=col)
    img.resize((lado, lado), Image.LANCZOS).save(salida)
    return medidas


def previsualizar(ruta: Path) -> tuple[Path | None, str]:
    """Devuelve (png, texto con medidas). Usa la miniatura de Bambu si el 3MF la trae."""
    PREVIAS.mkdir(parents=True, exist_ok=True)
    salida = PREVIAS / (re.sub(r"[^\w.-]+", "_", ruta.stem)[:60] + ".png")
    ext = ruta.suffix.lower()
    try:
        if ext == ".3mf":
            with zipfile.ZipFile(ruta) as z:
                for n in ("Metadata/plate_1.png", "Auxiliaries/.thumbnails/thumbnail_3mf.png", "Metadata/thumbnail.png"):
                    if n in z.namelist():
                        salida.write_bytes(z.read(n))
                        break
                else:
                    tris = list(_triangulos_3mf(ruta))
                    m = _renderizar(tris, salida)
                    return salida, f"{m[0]:.0f} × {m[1]:.0f} × {m[2]:.0f} mm"
            tris = list(_triangulos_3mf(ruta))
            if tris:
                xs, ys, zs = ([v[i] for t in tris for v in t] for i in range(3))
                return salida, f"{max(xs) - min(xs):.0f} × {max(ys) - min(ys):.0f} × {max(zs) - min(zs):.0f} mm (sin escalar)"
            return salida, ""
        if ext == ".stl":
            tris = list(_triangulos_stl(ruta.read_bytes()))
        elif ext == ".obj":
            tris = list(_triangulos_obj(ruta.read_text(errors="ignore")))
        else:
            return None, "(los STEP no tienen vista previa propia: se ven en Bambu Studio)"
        if not tris:
            return None, "el archivo no tiene triángulos"
        m = _renderizar(tris, salida)
        return salida, f"{m[0]:.0f} × {m[1]:.0f} × {m[2]:.0f} mm, {len(tris):,} triángulos".replace(",", ".").replace("mm.", "mm,")
    except Exception as e:  # noqa: BLE001 — una vista previa rota no debe impedir imprimir
        return None, f"(no pude generar la vista previa: {e})"


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


def _clic(x, y):
    import uiautomation as auto

    auto.Click(int(x), int(y), waitTime=0.4)


def _captura(r):
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)


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
    rgb = _color_medio(_captura(r), 0.2, 0.5, 2)
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
                _clic(*_centro(no.BoundingRectangle))
                break


def _abrir_en_studio(ruta: Path) -> str:
    if not EXE.exists():
        raise RuntimeError("No encuentro Bambu Studio instalado (bambu-studio.exe).")
    _cerrar_mia()
    antes = {h for h, _ in _ventanas()}
    subprocess.Popen([str(EXE), str(ruta)])
    for _ in range(90):
        time.sleep(1)
        nuevas = [(h, t) for h, t in _ventanas() if h not in antes and t.endswith("BambuStudio") and DIALOGO not in t]
        if nuevas:
            ESTADO.update(hwnd=nuevas[0][0], ruta=str(ruta), impresoras=[], cuando=0)
            time.sleep(4)  # termina de importar el modelo
            # Bambu puede preguntar algo al abrir (unidades, perfil del 3MF): acepta lo propuesto
            ven = _control(nuevas[0][0])
            for x, _ in _recorrer(ven, 4):
                if x.ControlTypeName == "WindowControl" and x.Name and x.Name != ven.Name:
                    si = next((b for b, _ in _recorrer(x, 6) if (b.Name or "").lower() in ("sí", "si", "aceptar", "ok", "yes", "cargar")), None)
                    if si:
                        _clic(*_centro(si.BoundingRectangle))
            return nuevas[0][1]
    raise RuntimeError("Bambu Studio no abrió la ventana del modelo.")


def _mi_ventana():
    h = ESTADO["hwnd"]
    if not h or h not in {x for x, _ in _ventanas()}:
        if not ESTADO["ruta"]:
            raise RuntimeError("Primero abre un modelo (dime qué archivo quieres imprimir).")
        _abrir_en_studio(Path(ESTADO["ruta"]))
        h = ESTADO["hwnd"]
    ven = _control(h)
    ven.SetActive()
    time.sleep(0.4)
    return ven


def _dialogo():
    h = next((h for h, t in _ventanas() if t == DIALOGO), None)
    return _control(h) if h else None


def _abrir_dialogo():
    if d := _dialogo():
        d.SetActive()
        return d
    ven = _mi_ventana()
    imprimir = _boton(ven, "Imprimir placa")
    if not imprimir:
        raise RuntimeError("No encuentro el botón «Imprimir placa» en Bambu Studio.")
    if not _verde(imprimir):
        laminar = _boton(ven, "Laminar placa")
        if laminar:
            _clic(*_centro(laminar.BoundingRectangle))
        limite = time.time() + 600
        while time.time() < limite and not _verde(imprimir):
            time.sleep(2)
        if not _verde(imprimir):
            raise RuntimeError("Bambu Studio no terminó de laminar (revisa si muestra un error en la placa).")
    _clic(*_centro(imprimir.BoundingRectangle))
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
    _clic(r.left + r.width() * 0.45, r.top + 60 * _escala(d.NativeWindowHandle) / 1.25)  # zona vacía del diálogo
    time.sleep(0.6)


def _abrir_ams(d):
    """Abre el selector del primer filamento y devuelve (popup, casillas)."""
    fil = next((x for x, _ in _recorrer(d, 4) if x.ControlTypeName == "TextControl" and x.Name == "Filamento"), None)
    if not fil:
        return None, []
    e = _escala(d.NativeWindowHandle) / 1.25  # medidas tomadas a 125 %
    antes = {h for h, _ in _ventanas()}
    r = fil.BoundingRectangle
    _clic(r.left + 58 * e, r.bottom + 60 * e)
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
        img = _captura(b)
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
    antes = {h for h, _ in _ventanas()}
    _clic(*_centro(caja.BoundingRectangle))
    time.sleep(1.5)
    nuevo = next((h for h, _ in _ventanas() if h not in antes), None)
    if not nuevo:
        raise RuntimeError("No se abrió la lista de impresoras.")
    r = _control(nuevo).BoundingRectangle
    img = _captura(r)
    # la fila elegida está resaltada: su alto es el de todas las filas
    col = [img.getpixel((int(img.width * 0.5), y))[:3] for y in range(img.height)]
    claro = [sum(p) > 735 for p in col]  # blanco
    ini = next((y for y, c in enumerate(claro) if not c), 0)
    fin = next((y for y in range(ini, len(claro)) if claro[y]), ini)
    alto = fin - ini if fin - ini > 15 else int(40 * _escala(nuevo))
    filas = max(1, round((r.height() - ini) / alto))
    return r, alto, filas, ini


def _elegir_fila(d, i):
    r, alto, filas, ini = _lista_impresoras(d)
    if i >= filas:
        _cerrar_emergentes(d)
        return False
    _clic(r.left + r.width() * 0.4, r.top + ini + alto * i + alto / 2)
    time.sleep(2.5)  # actualiza estado y AMS de la impresora elegida
    return True


def _explorar() -> list[dict]:
    d = _abrir_dialogo()
    _, _, filas, _ = _lista_impresoras(d)
    _cerrar_emergentes(d)
    vistas = []
    for i in range(min(filas, 20)):
        if not _elegir_fila(d, i):
            break
        info = _leer_dialogo(d)
        if any(v["nombre"] == info["nombre"] for v in vistas):
            continue
        vistas.append({"fila": i, "nombre": info["nombre"], "avisos": info["avisos"], "libre": info["lista"],
                       "ams": _leer_ams(d)})
    ESTADO.update(impresoras=vistas, cuando=time.time())
    return vistas


def _resumen(imps: list[dict]) -> str:
    lineas = []
    for p in imps:
        estado = "libre" if p["libre"] else ("; ".join(p["avisos"]) or "no disponible")
        colores = ", ".join(f"{s['casilla']} {s['color']}" for s in p["ams"]) or "sin AMS visible"
        lineas.append(f"- {p['nombre']}: {estado}. Colores: {colores}")
    return "\n".join(lineas)


def _imprimir(color, impresora) -> str:
    pedido = color_pedido(color) if color else None
    if color and not pedido:
        return f"No reconozco el color «{color}». Dime uno como rojo, negro, azul claro o un código #rrggbb."
    imps = ESTADO["impresoras"] if time.time() - ESTADO["cuando"] < 180 and _dialogo() else _explorar()
    candidatas = imps
    if impresora:
        clave = _sin_tildes(impresora)
        candidatas = [p for p in imps if clave in _sin_tildes(p["nombre"])]
        if not candidatas:
            return f"No veo ninguna impresora llamada «{impresora}». Tengo:\n{_resumen(imps)}"
    opciones = []
    for p in candidatas:
        for s in p["ams"]:
            dist = _distancia(s["rgb"], pedido[1]) if pedido else 0
            if not pedido or s["color"] == pedido[0] or dist < 160:
                opciones.append((not p["libre"], s["color"] != (pedido[0] if pedido else s["color"]), dist, p, s))
    if not opciones:
        return f"Ninguna impresora tiene {pedido[0]} cargado ahora mismo.\n{_resumen(candidatas)}"
    opciones.sort(key=lambda o: o[:3])
    ocupada, _, _, p, s = opciones[0]
    if ocupada:
        return (f"Las impresoras con {pedido[0] if pedido else 'ese filamento'} no están libres:\n"
                f"{_resumen([o[3] for o in opciones])}")
    d = _abrir_dialogo()
    if _leer_dialogo(d)["nombre"] != p["nombre"]:
        _elegir_fila(d, p["fila"])
    if _leer_dialogo(d)["nombre"] != p["nombre"]:
        return f"No pude seleccionar {p['nombre']} en Bambu Studio."
    pop, casillas = _abrir_ams(d)
    casillas = sorted(casillas, key=lambda c: (c[0].BoundingRectangle.top, c[0].BoundingRectangle.left))
    nombres = [x["casilla"] for x in _leer_ams_nombres(casillas)]
    if s["casilla"] not in nombres:
        _cerrar_emergentes(d)
        return "El AMS cambió mientras elegía; vuelve a pedírmelo."
    _clic(*_centro(casillas[nombres.index(s["casilla"])][0].BoundingRectangle))
    time.sleep(1)
    if pop and pop.NativeWindowHandle in {h for h, _ in _ventanas()}:
        _cerrar_emergentes(d)
    info = _leer_dialogo(d)
    if not info["lista"]:
        return f"Bambu Studio no deja enviar a {p['nombre']}: {'; '.join(info['avisos']) or 'el botón Enviar está desactivado'}."
    _clic(*_centro(info["_enviar"].BoundingRectangle))
    for _ in range(90):
        time.sleep(1)
        if not _dialogo():
            ESTADO["cuando"] = 0
            return f"Enviado a {p['nombre']} con el filamento {s['casilla']} ({s['color']}). ¡Ya empieza a imprimir!"
    return f"Pulsé Enviar para {p['nombre']}, pero la ventana sigue abierta: revisa Bambu Studio."


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
                if f.lower().endswith(EXTENSIONES) and all(w in _sin_tildes(f) for w in palabras):
                    ruta = Path(actual) / f
                    hallados.append((ruta.stat().st_mtime, ruta))
    return [r for _, r in sorted(hallados, reverse=True)[:8]]


@herramienta("modelo_3d_abrir", "Abre un modelo 3D (STL, 3MF, OBJ, STEP) para imprimirlo: lo busca en el PC, muestra una "
             "vista previa en las pantallas del usuario y lo carga en Bambu Studio. Primer paso de toda impresión 3D.",
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
    ruta = rutas[0]
    png, medidas = await asyncio.to_thread(previsualizar, ruta)
    if png and png.is_file():
        from .herramientas import enviar_archivo

        await enviar_archivo(ctx, str(png))
    try:
        await _en_hilo(_abrir_en_studio, ruta)
        studio = "Ya está cargado en Bambu Studio."
    except Exception as e:  # noqa: BLE001
        studio = f"No pude abrirlo en Bambu Studio: {e}"
    return (f"Modelo: {ruta.name} ({medidas}). {'Vista previa enviada a tus pantallas. ' if png else ''}{studio} "
            "Cuando el usuario diga de qué color (y opcionalmente en qué impresora), usa imprimir_3d.")


@herramienta("impresoras_3d", "Lista las impresoras Bambu Lab de la cuenta del usuario: si están libres u ocupadas y qué colores "
             "tienen cargados en el AMS. Necesita un modelo abierto con modelo_3d_abrir (tarda ~10 s por impresora).", solo_pc=True)
async def impresoras_3d(ctx):
    try:
        return _resumen(await _en_hilo(_explorar)) or "Bambu Studio no muestra impresoras (¿iniciaste sesión?)."
    except Exception as e:  # noqa: BLE001
        return f"No pude revisar las impresoras: {e}"


@herramienta("imprimir_3d", "Manda a imprimir el modelo abierto: lamina, elige una impresora libre que tenga el color pedido "
             "en su AMS y envía el trabajo por la nube de Bambu.",
             {"color": P("string", "Color del filamento, ej. 'rojo', 'negro', 'azul claro' o '#ff8800'"),
              "impresora": P("string", "Nombre de la impresora si el usuario eligió una (opcional), ej. 'taller'")},
             ["color"], peligrosa=True, solo_pc=True)
async def imprimir_3d(ctx, color, impresora=None):
    if not ESTADO["ruta"]:
        return "Primero abre un modelo con modelo_3d_abrir."
    try:
        return await _en_hilo(_imprimir, color, impresora)
    except Exception as e:  # noqa: BLE001
        return f"No pude enviar la impresión: {e}"
