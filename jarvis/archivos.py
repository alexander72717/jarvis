"""Almacén de archivos del cerebro: el puente para pasar archivos entre el PC y el celular.

Los archivos viven en data/archivos/ del cerebro (tu servidor, dentro de Tailscale) y se borran solos
a los 7 días. Nada sale a internet.
"""
import json
import mimetypes
import re
import secrets
import time
from pathlib import Path

from . import config

CARPETA = config.DATOS / "archivos"
INDICE = CARPETA / "indice.json"
MAXIMO = 300 * 1024 * 1024  # 300 MB por archivo
DIAS = 7


def _indice() -> dict:
    try:
        return json.loads(INDICE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar_indice(d: dict):
    CARPETA.mkdir(parents=True, exist_ok=True)
    INDICE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def nombre_seguro(nombre: str) -> str:
    n = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", Path(nombre or "archivo").name).strip(" .")
    return n[:150] or "archivo"


def limpiar():
    d, ahora = _indice(), time.time()
    for i in [i for i, a in d.items() if ahora - a["ts"] > DIAS * 86400]:
        (CARPETA / i).unlink(missing_ok=True)
        del d[i]
    _guardar_indice(d)


async def guardar(subida, origen: str) -> dict:
    """Guarda un UploadFile en disco por partes (no lo carga entero en memoria)."""
    limpiar()
    CARPETA.mkdir(parents=True, exist_ok=True)
    ident = secrets.token_urlsafe(10)
    destino, total = CARPETA / ident, 0
    with open(destino, "wb") as f:
        while trozo := await subida.read(1024 * 1024):
            total += len(trozo)
            if total > MAXIMO:
                f.close()
                destino.unlink(missing_ok=True)
                raise ValueError(f"El archivo supera {MAXIMO // 1024 // 1024} MB.")
            f.write(trozo)
    nombre = nombre_seguro(subida.filename)
    datos = {"id": ident, "nombre": nombre, "tamano": total, "origen": origen, "ts": time.time(),
             "tipo": subida.content_type or mimetypes.guess_type(nombre)[0] or "application/octet-stream"}
    d = _indice()
    d[ident] = datos
    _guardar_indice(d)
    return publico(datos)


def guardar_ruta(ruta: Path, origen: str) -> dict:
    """Copia un archivo del disco local al almacén (cuando este mismo equipo es el cerebro)."""
    import shutil

    limpiar()
    CARPETA.mkdir(parents=True, exist_ok=True)
    ident = secrets.token_urlsafe(10)
    shutil.copyfile(ruta, CARPETA / ident)
    nombre = nombre_seguro(ruta.name)
    datos = {"id": ident, "nombre": nombre, "tamano": ruta.stat().st_size, "origen": origen, "ts": time.time(),
             "tipo": mimetypes.guess_type(nombre)[0] or "application/octet-stream"}
    d = _indice()
    d[ident] = datos
    _guardar_indice(d)
    return publico(datos)


def publico(a: dict) -> dict:
    return {**a, "url": f"/api/archivos/{a['id']}", "cuando": time.strftime("%d/%m %H:%M", time.localtime(a["ts"]))}


def lista() -> list[dict]:
    limpiar()
    return [publico(a) for a in sorted(_indice().values(), key=lambda a: -a["ts"])]


def obtener(ident: str) -> tuple[Path, dict] | None:
    a = _indice().get(ident)
    if not a or not (CARPETA / ident).exists():
        return None
    return CARPETA / ident, a


def legible(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"
