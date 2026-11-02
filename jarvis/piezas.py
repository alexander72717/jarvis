"""De una idea a una pieza: la IA escribe un modelo OpenSCAD, se convierte a STL y se abre en el visor 3D.

Imprimirla sigue el camino de siempre (visor y confirmación). OpenSCAD no ejecuta programas, y aquí además
se rechaza lo que lee archivos del PC.
"""
import asyncio
import os
import re
import subprocess
from pathlib import Path

from . import impresion3d
from .herramientas import P, herramienta

CARPETA = impresion3d.TRABAJOS / "piezas"
PROHIBIDO = re.compile(r"\b(import|include|use|surface)\b")
INSTRUCCIONES = """Eres un diseñador mecánico experto en OpenSCAD. Responde SOLO con código OpenSCAD, sin explicaciones.
Reglas: unidades en milímetros; la pieza apoyada en z=0 y sobre su cara más ancha; imprimible en FDM sin soportes si se puede;
paredes de al menos 1.6 mm; $fn=64; medidas como variables al inicio; una sola pieza de una sola forma sólida.
No uses import, include, use ni surface."""


def ejecutable() -> str | None:
    candidatos = [Path(os.environ.get(v, "")) / "OpenSCAD" / "openscad.exe" for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    return next((str(c) for c in candidatos if c.is_file()), None)


def limpiar(respuesta: str) -> str:
    """Saca el código aunque venga dentro de un bloque ``` y comprueba que no toque archivos."""
    m = re.search(r"```[a-zA-Z]*\n(.*?)```", respuesta, re.S)
    codigo = (m.group(1) if m else respuesta).strip()
    if not codigo:
        raise ValueError("la IA no devolvió código")
    if PROHIBIDO.search(re.sub(r"//.*|/\*.*?\*/", "", codigo, flags=re.S)):
        raise ValueError("el código intenta leer archivos")
    return codigo


def compilar(codigo: str, destino: Path) -> str | None:
    """Convierte a STL. Devuelve el error de OpenSCAD, o None si salió bien."""
    fuente = destino.with_suffix(".scad")
    fuente.write_text(codigo, encoding="utf-8")
    destino.unlink(missing_ok=True)
    r = subprocess.run([ejecutable(), "-o", str(destino), str(fuente)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode or not destino.is_file() or destino.stat().st_size < 100:
        return (r.stderr or "OpenSCAD no generó la pieza")[-800:]
    return None


@herramienta("crear_pieza", "Diseña una pieza para imprimir a partir de una descripción («un soporte de 40 mm para este sensor»), genera el STL "
             "y lo abre en el visor 3D. No imprime nada: eso sigue con imprimir_3d y su confirmación.",
             {"descripcion": P("string", "Qué pieza, con todas las medidas que dio el usuario"),
              "nombre": P("string", "Nombre corto para el archivo, ej. 'soporte sensor'")}, ["descripcion", "nombre"], solo_pc=True, timeout=300)
async def crear_pieza(ctx, descripcion, nombre):
    if not ejecutable():
        return "Falta OpenSCAD en este PC. Se instala con: winget install OpenSCAD.OpenSCAD"
    CARPETA.mkdir(parents=True, exist_ok=True)
    destino = CARPETA / (re.sub(r"[^\w-]+", "_", nombre.strip())[:50] + ".stl")
    mensajes = [{"role": "system", "content": INSTRUCCIONES}, {"role": "user", "content": descripcion}]
    error = "sin intentos"
    for _ in range(3):  # si no compila, la IA ve el error y lo corrige
        respuesta = await impresion3d.VER(mensajes, "inteligente", False)
        try:
            codigo = limpiar(respuesta)
        except ValueError as e:
            error = str(e)
            mensajes += [{"role": "assistant", "content": respuesta}, {"role": "user", "content": f"No sirve: {error}. Responde solo con código válido."}]
            continue
        error = await asyncio.to_thread(compilar, codigo, destino)
        if not error:
            abierto = await impresion3d.modelo_3d_abrir(ctx, str(destino))
            return f"Diseñé la pieza. {abierto}"
        mensajes += [{"role": "assistant", "content": respuesta}, {"role": "user", "content": f"OpenSCAD dio este error, corrígelo:\n{error}"}]
    return f"No pude generar la pieza: {error}"

