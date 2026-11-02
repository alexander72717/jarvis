"""Registro de acciones: lo que JARVIS hizo con sus herramientas, quién lo pidió y si se autorizó."""
import json
import threading
from datetime import datetime

from . import config
from .herramientas import P, herramienta

ARCHIVO = config.DATOS / "acciones.jsonl"
MAXIMO = 2000
_candado = threading.Lock()


def anotar(nombre: str, args: dict, canal: str, resultado: str, autorizada: bool | None = None):
    """autorizada: None si no necesitaba permiso. Los datos se recortan: esto es un rastro, no una copia."""
    fila = {"cuando": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "accion": nombre, "datos": json.dumps(args, ensure_ascii=False)[:200],
            "canal": canal, "autorizada": autorizada, "resultado": resultado[:200]}
    with _candado:
        with ARCHIVO.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fila, ensure_ascii=False) + "\n")
        if ARCHIVO.stat().st_size > 400 * MAXIMO:  # recorta de vez en cuando, no en cada escritura
            lineas = ARCHIVO.read_text(encoding="utf-8").splitlines()[-MAXIMO // 2:]
            ARCHIVO.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def ultimas(cuantas: int = 20, dia: str | None = None) -> list[dict]:
    try:
        filas = [json.loads(x) for x in ARCHIVO.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []
    if dia:
        filas = [f for f in filas if f["cuando"].startswith(dia)]
    return filas[-cuantas:]


def _linea(f: dict) -> str:
    permiso = {True: " (autorizada)", False: " (NO autorizada)", None: ""}[f.get("autorizada")]
    return f"{f['cuando'][5:16]} · {f['accion']}{permiso} · {f['canal']}"


@herramienta("registro_de_acciones", "Muestra lo que JARVIS ha hecho: qué herramientas usó, cuándo, desde dónde y si se autorizó.",
             {"cuantas": P("integer", "Cuántas acciones (por defecto 20)"), "solo_hoy": P("boolean", "Solo las de hoy")})
async def registro_de_acciones(ctx, cuantas=20, solo_hoy=False):
    filas = ultimas(min(int(cuantas), 100), datetime.now().strftime("%Y-%m-%d") if solo_hoy else None)
    return "\n".join(_linea(f) for f in filas) or "No hay acciones registradas."
