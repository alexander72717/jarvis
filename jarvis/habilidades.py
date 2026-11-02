"""Habilidades aprendidas: lo que Claude Code resolvió una vez queda como un script que JARVIS repite solo.

Cada habilidad es un archivo en data/habilidades/ (.ps1 o .py). La primera línea es un comentario con lo que hace:
    # Prende el sistema smaky pruebas (servidor y WhatsApp)
Recibe sus parámetros como argumentos ($args en PowerShell, sys.argv en Python). JARVIS las consulta con habilidades
y las usa con usar_habilidad (siempre pide confirmación); si una falla, se lo pasa a Claude para que la arregle.
"""
import asyncio
import re
import subprocess
import sys

from . import config
from .herramientas import P, herramienta

CARPETA = config.DATOS / "habilidades"
TIPOS = {".ps1", ".py"}


def todas() -> dict[str, dict]:
    """nombre -> {ruta, desc}"""
    out = {}
    for f in sorted(CARPETA.glob("*")) if CARPETA.is_dir() else []:
        if f.suffix.lower() not in TIPOS:
            continue
        try:
            primera = f.read_text(encoding="utf-8", errors="ignore").lstrip("﻿").splitlines()[0]
        except (OSError, IndexError):
            primera = ""
        out[f.stem] = {"ruta": f, "desc": re.sub(r"^\s*#\s*", "", primera).strip()[:200] or f.stem}
    return out


def _correr(ruta, argumentos: list[str], segundos: int) -> str:
    if ruta.suffix.lower() == ".py":
        cmd = [sys.executable, str(ruta), *argumentos]
    else:
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ruta), *argumentos]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=segundos,
                       cwd=str(ruta.parent), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    salida = (r.stdout or "") + (f"\n[error] {r.stderr}" if r.stderr.strip() else "")
    estado = "terminó bien" if r.returncode == 0 else f"FALLÓ (código {r.returncode})"
    return f"La habilidad {estado}.\n{salida.strip()[:6000]}"


@herramienta("habilidades", "Lista lo que ya aprendiste a hacer solo en el PC (scripts que dejó Claude Code).", solo_pc=True)
async def habilidades(ctx):
    hab = todas()
    return "\n".join(f"- {n}: {h['desc']}" for n, h in list(hab.items())[:40]) or "Todavía no he aprendido ninguna habilidad."


@herramienta("usar_habilidad", "Ejecuta una habilidad que ya aprendiste (míralas con habilidades). Si falla, pásale el error a "
             "Claude con encargar_a_claude para que la arregle.",
             {"nombre": P("string", "Nombre exacto de la habilidad"),
              "argumentos": P("array", "Parámetros, si la habilidad los necesita", items={"type": "string"})},
             ["nombre"], peligrosa=True, solo_pc=True, timeout=600)
async def usar_habilidad(ctx, nombre, argumentos=None):
    h = todas().get(str(nombre))
    if not h:
        return f"No tengo una habilidad llamada «{nombre}». Las que sé: {', '.join(todas()) or 'ninguna todavía'}."
    if isinstance(argumentos, str):
        argumentos = [argumentos]
    try:
        return await asyncio.to_thread(_correr, h["ruta"], [str(a) for a in argumentos or []], 300)
    except subprocess.TimeoutExpired:
        return "La habilidad tardó más de 5 minutos y la detuve."
    except OSError as e:
        return f"No pude ejecutar la habilidad: {e}"
