"""Claude Code como compañero de JARVIS para las tareas que lo superan (programar, revisar un proyecto, investigar a
fondo, diseñar algo complejo). No usa la API: ejecuta la línea de comandos de Claude Code con TU cuenta de Claude
(el plan Pro comparte su límite con el chat).

Instalar una vez (PowerShell):  irm https://claude.ai/install.ps1 | iex   → luego  claude  e inicia sesión.

- preguntar_a_claude: solo lee y responde (modo «plan»: no cambia nada).
- encargar_a_claude: trabaja de verdad en una carpeta (edita archivos; pide confirmación antes).
Las tareas corren en segundo plano: JARVIS avisa en tus pantallas cuando Claude termina, y puedes seguir la misma
conversación («dile a Claude que también…»).
"""
import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from . import config
from .herramientas import P, _sin_tildes, herramienta

log = logging.getLogger("jarvis.claude")
CARPETA = config.DATOS / "claude"
AVISAR = None  # async fn(texto): lo conecta el servidor al arrancar (todas las pantallas + Telegram)
TAREAS: dict[str, asyncio.Task] = {}
LIMITE = 45 * 60  # una tarea no puede pasar de 45 min
INSTRUCCIONES = ("Te llama JARVIS, el asistente por voz del usuario, para una tarea que pidió. Responde en español. "
                 "Al final escribe una línea que empiece por «RESUMEN:» con 1-2 frases para leerle en voz alta.")


def ejecutable() -> str | None:
    """La línea de comandos instalada (la copia de la app de escritorio no sirve: no tiene la sesión iniciada)."""
    for c in (shutil.which("claude"), str(Path.home() / ".local" / "bin" / "claude.exe"), str(Path.home() / ".local" / "bin" / "claude")):
        if c and Path(c).is_file():
            return c
    return None


SIN_INSTALAR = ("Claude Code no está instalado en el PC. Instálalo una vez en PowerShell con "
                "«irm https://claude.ai/install.ps1 | iex», abre una terminal nueva, escribe «claude» e inicia sesión con tu cuenta.")


def _carpeta(nombre: str | None) -> Path:
    """'jarvis', 'proyecto smurfit', una ruta… -> carpeta existente (por defecto el Escritorio)."""
    casa = Path.home()
    if not nombre:
        return casa / "Desktop"
    p = Path(os.path.expandvars(os.path.expanduser(nombre.strip('"'))))
    if p.is_absolute() and p.is_dir():  # una ruta relativa dependería de dónde corre JARVIS
        return p
    clave = _sin_tildes(nombre)
    palabras = [w for w in re.findall(r"\w+", clave) if w not in ("proyecto", "carpeta", "el", "la", "de", "del", "mi")]
    mejores = []
    for raiz in (casa / "Desktop", casa / "Documents", casa / "Desktop" / "proyectos"):
        if not raiz.is_dir():
            continue
        for actual, dirs, _ in os.walk(raiz):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", ".venv", "__pycache__", "data")]
            if len(Path(actual).relative_to(raiz).parts) >= 4:
                dirs[:] = []
            for d in dirs:
                n = _sin_tildes(d)
                if palabras and all(w in n for w in palabras):
                    mejores.append((len(n), Path(actual) / d))
    if not mejores:
        raise ValueError(f"No encuentro la carpeta «{nombre}» en el Escritorio ni en Documentos.")
    return min(mejores)[1]


def _guardar(t: dict):
    CARPETA.mkdir(parents=True, exist_ok=True)
    (CARPETA / f"{t['id']}.json").write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")


def _todas() -> list[dict]:
    if not CARPETA.exists():
        return []
    out = []
    for f in CARPETA.glob("*.json"):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return sorted(out, key=lambda t: t["creada"], reverse=True)


def _resumen(texto: str) -> str:
    m = re.search(r"RESUMEN:\s*(.+)", texto or "")
    if m:
        return m.group(1).strip()[:400]
    limpio = " ".join((texto or "").split())
    return limpio[:300] + ("…" if len(limpio) > 300 else "")


async def _correr(t: dict):
    exe = ejecutable()
    args = [exe, "-p", t["pedido"], "--output-format", "json", "--append-system-prompt", INSTRUCCIONES,
            "--permission-mode", "acceptEdits" if t["modo"] == "hacer" else "plan"]
    if t.get("sesion"):
        args += ["--resume", t["sesion"]]
    t.update(estado="trabajando", inicio=time.time())
    _guardar(t)
    try:
        proc = await asyncio.create_subprocess_exec(*args, cwd=t["carpeta"], stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL)
        salida, error = await asyncio.wait_for(proc.communicate(), LIMITE)
        try:
            r = json.loads(salida.decode("utf-8", errors="ignore"))
        except ValueError:
            r = {"is_error": True, "result": (error or salida).decode("utf-8", errors="ignore")[-800:]}
        texto = r.get("result") or ""
        t.update(estado="error" if r.get("is_error") else "lista", respuesta=texto, sesion=r.get("session_id") or t.get("sesion"),
                 negados=[d.get("tool_name") for d in r.get("permission_denials") or []], fin=time.time())
        if "Not logged in" in texto or "/login" in texto:
            t["respuesta"] = "Claude Code no tiene la sesión iniciada: abre una terminal, escribe «claude» e inicia sesión."
    except asyncio.TimeoutError:
        proc.kill()
        t.update(estado="error", respuesta=f"Claude tardó más de {LIMITE // 60} min y detuve la tarea.", fin=time.time())
    except Exception as e:  # noqa: BLE001
        t.update(estado="error", respuesta=f"No pude ejecutar Claude Code: {e}", fin=time.time())
    _guardar(t)
    minutos = max(1, round((t["fin"] - t["inicio"]) / 60))
    if t["estado"] == "lista":
        aviso = f"🤖 Claude terminó «{t['titulo']}» ({minutos} min): {_resumen(t['respuesta'])}"
        if t.get("negados"):
            aviso += f" (No pudo usar: {', '.join(sorted(set(t['negados'])))}; pídeme repetir con permiso si hace falta.)"
    else:
        aviso = f"🤖 Claude no pudo con «{t['titulo']}»: {_resumen(t['respuesta'])}"
    if AVISAR:
        await AVISAR(aviso)


def _lanzar(pedido: str, carpeta: str | None, modo: str, sesion: str | None = None, titulo: str | None = None) -> dict:
    if not ejecutable():
        raise RuntimeError(SIN_INSTALAR)
    t = {"id": uuid.uuid4().hex[:6], "titulo": titulo or " ".join(pedido.split())[:60], "pedido": pedido, "modo": modo,
         "carpeta": str(_carpeta(carpeta)), "sesion": sesion, "creada": time.time(), "estado": "en cola"}
    _guardar(t)
    TAREAS[t["id"]] = asyncio.create_task(_correr(t))
    return t


def _texto_lanzada(t: dict) -> str:
    que = "trabajando (puede editar archivos)" if t["modo"] == "hacer" else "revisando (solo lee, no cambia nada)"
    return (f"Le pasé la tarea a Claude Code (#{t['id']}), que ya está {que} en {t['carpeta']}. "
            "Te aviso en tus pantallas cuando termine; mientras tanto puedes seguir hablando conmigo.")


@herramienta("preguntar_a_claude", "Le pasa a Claude Code (IA experta, con tu cuenta de Claude, no la API) una pregunta o "
             "análisis complejo que te supera: revisar o explicar un proyecto o código, encontrar un error, planear cómo hacer "
             "algo difícil, investigar a fondo. Solo lee, no cambia nada. Corre en segundo plano y avisa al terminar.",
             {"pedido": P("string", "La tarea completa y con contexto, tal como se la explicarías a un experto"),
              "carpeta": P("string", "Carpeta o proyecto donde trabajar, ej. 'jarvis', 'proyecto smurfit' (opcional)")},
             ["pedido"], solo_pc=True)
async def preguntar_a_claude(ctx, pedido, carpeta=None):
    try:
        return _texto_lanzada(_lanzar(pedido, carpeta, "consultar"))
    except Exception as e:  # noqa: BLE001
        return str(e)


@herramienta("encargar_a_claude", "Encarga a Claude Code un trabajo real en una carpeta del PC: programar una función, "
             "arreglar un error, crear un script o un proyecto, refactorizar. Puede crear y editar archivos de esa carpeta. "
             "Corre en segundo plano y avisa al terminar.",
             {"pedido": P("string", "Qué debe hacer, con todo el contexto y cómo saber que quedó bien"),
              "carpeta": P("string", "Carpeta o proyecto donde trabajar, ej. 'jarvis', 'robot carritos'"),
              "continuar": P("string", "id de una tarea anterior para seguir esa misma conversación (opcional)")},
             ["pedido"], peligrosa=True, solo_pc=True)
async def encargar_a_claude(ctx, pedido, carpeta=None, continuar=None):
    previa = next((t for t in _todas() if t["id"] == continuar), None) if continuar else None
    try:
        if previa:
            return _texto_lanzada(_lanzar(pedido, previa["carpeta"], "hacer", sesion=previa.get("sesion"),
                                          titulo=f"{previa['titulo']} (seguimiento)"))
        if not carpeta:
            return "Dime en qué carpeta o proyecto debe trabajar Claude."
        return _texto_lanzada(_lanzar(pedido, carpeta, "hacer"))
    except Exception as e:  # noqa: BLE001
        return str(e)


@herramienta("seguir_con_claude", "Continúa la misma conversación con Claude Code de una tarea anterior (le recuerda todo lo "
             "anterior): «dile a Claude que también…», «pregúntale por qué…». Sin id usa la última tarea.",
             {"mensaje": P("string", "Lo que hay que decirle a Claude"),
              "tarea": P("string", "id de la tarea (de tareas_claude); vacío = la última")}, ["mensaje"], solo_pc=True)
async def seguir_con_claude(ctx, mensaje, tarea=None):
    previas = [t for t in _todas() if t.get("sesion")]
    t = next((x for x in previas if x["id"] == tarea), None) if tarea else (previas[0] if previas else None)
    if not t:
        return "No hay una conversación anterior con Claude para continuar."
    if t["estado"] == "trabajando":
        return f"Claude todavía está con «{t['titulo']}»; espera a que termine para seguir."
    try:  # el seguimiento solo lee; para que vuelva a editar archivos: encargar_a_claude con continuar=<id> (pide confirmación)
        nueva = _lanzar(mensaje, t["carpeta"], "consultar", sesion=t["sesion"], titulo=f"{t['titulo']} (seguimiento)")
        return _texto_lanzada(nueva)
    except Exception as e:  # noqa: BLE001
        return str(e)


@herramienta("tareas_claude", "Muestra las tareas que JARVIS le pasó a Claude Code: cuáles siguen trabajando y la respuesta "
             "completa de las terminadas.", {"tarea": P("string", "id para ver una respuesta completa (opcional)")}, solo_pc=True)
async def tareas_claude(ctx, tarea=None):
    todas = _todas()
    if tarea:
        t = next((x for x in todas if x["id"] == tarea), None)
        return f"#{t['id']} «{t['titulo']}» ({t['estado']}):\n{t.get('respuesta') or '(sin respuesta aún)'}" if t else "No existe esa tarea."
    if not todas:
        return "Todavía no le he pasado tareas a Claude."
    lineas = []
    for t in todas[:8]:
        dur = f", {round(((t.get('fin') or time.time()) - t.get('inicio', t['creada'])) / 60)} min" if t.get("inicio") else ""
        lineas.append(f"- #{t['id']} «{t['titulo']}» — {t['estado']}{dur}" +
                      (f": {_resumen(t.get('respuesta', ''))}" if t["estado"] != "trabajando" else ""))
    return "\n".join(lineas)
