"""Tareas programadas: JARVIS hace algo solo a cierta hora («todos los días a las 7 revisa X») y te avisa.

Las lleva quien piensa (el cerebro, siempre encendido). Corren sin nadie presente: lo peligroso se deniega solo,
porque nadie puede confirmarlo, y una tarea no puede crear otras tareas.
"""
import asyncio
import json
import logging
import os
import re
import unicodedata
import uuid
from datetime import datetime

from . import config
from .herramientas import P, herramienta

log = logging.getLogger("jarvis.tareas")
ARCHIVO = config.DATOS / "tareas.json"
MAXIMO = 30
GRACIA = 60  # minutos: si el cerebro estaba reiniciándose a la hora justa, igual se hace
LIMITE = 180  # segundos que puede tardar una tarea
DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
RESUMEN = ("Haz mi resumen de la mañana como quien saluda a un amigo: el clima de hoy, TODO lo que tengo hoy según calendario_ver, mis recordatorios y "
           "lo pendiente de mis proyectos con proyecto_ver. No toques las impresoras 3D ni Bambu Studio. Solo lo que importe, máximo cuatro frases para oír, sin listas, "
           "y termina preguntando por dónde quiero empezar.")
CIERRE = ("Haz mi cierre del día: mira registro_de_acciones de hoy y proyecto_ver, y cuéntame en pocas frases qué se hizo hoy, "
          "qué quedó pendiente y qué sería lo primero de mañana. Si hoy no hubo actividad, dilo en una frase.")
ANUNCIAR = None  # async fn(texto): lo conecta el servidor (pantallas, celular y Telegram)


def _todas() -> list[dict]:
    try:
        return json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _guardar(lista: list[dict]):
    temporal = ARCHIVO.with_suffix(".tmp")
    temporal.write_text(json.dumps(lista, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporal, ARCHIVO)


def _hora(texto: str) -> str | None:
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*", str(texto))
    if not m or int(m[1]) > 23 or int(m[2] or 0) > 59:
        return None
    return f"{int(m[1]):02d}:{m[2] or '00'}"


def _dias(texto: str | None) -> list[int] | None:
    """Índices de lunes (0) a domingo (6). None si no entiende el texto."""
    t = "".join(c for c in unicodedata.normalize("NFD", (texto or "").lower()) if unicodedata.category(c) != "Mn")
    if not t.strip() or "todos" in t or "diario" in t:
        return list(range(7))
    if "entre semana" in t or "laboral" in t:
        return list(range(5))
    if "fin de semana" in t or "fines de semana" in t:
        return [5, 6]
    hallados = [i for i, d in enumerate(DIAS) if d in t]
    return hallados or None


def _texto_dias(dias: list[int]) -> str:
    if len(dias) == 7:
        return "todos los días"
    return "los " + ", ".join(DIAS[d] for d in dias)


def toca(t: dict, ahora: datetime) -> bool:
    if ahora.weekday() not in t["dias"] or t.get("ultima") == ahora.strftime("%Y-%m-%d"):
        return False
    hh, mm = (int(x) for x in t["hora"].split(":"))
    pasaron = ahora.hour * 60 + ahora.minute - (hh * 60 + mm)
    return 0 <= pasaron < GRACIA


def crear(pedido: str, hora: str, dias: str | None = None, ahora: datetime | None = None) -> str:
    h, d = _hora(hora), _dias(dias)
    if not h:
        return f"No entendí la hora «{hora}». Dámela como 07:30."
    if d is None:
        return f"No entendí los días «{dias}»."
    lista = _todas()
    if len(lista) >= MAXIMO:
        return f"Ya hay {MAXIMO} tareas programadas; cancela alguna primero."
    ahora = ahora or datetime.now()
    t = {"id": uuid.uuid4().hex[:5], "pedido": pedido.strip()[:500], "hora": h, "dias": d}
    if toca(t, ahora):  # recién creada, con la hora ya pasada: empieza en la próxima, no ahora mismo
        t["ultima"] = ahora.strftime("%Y-%m-%d")
    _guardar(lista + [t])
    return f"Listo (#{t['id']}): {_texto_dias(d)} a las {h}."


def _desde_tarea(ctx) -> bool:
    return ctx.canal == "tarea"


@herramienta("programar_tarea", "Programa algo que harás solo a cierta hora, repetido: «todos los días a las 7 revisa…», «los lunes a las 9 "
             "dime…». Para un aviso de una sola vez usa crear_recordatorio. Corre sin nadie presente: no podrá hacer nada que pida confirmación.",
             {"pedido": P("string", "Qué hacer, como si te lo pidieran en ese momento"), "hora": P("string", "Hora en formato HH:MM de 24 horas"),
              "dias": P("string", "'todos' (por defecto), 'entre semana', 'fin de semana' o días como 'lunes, jueves'")}, ["pedido", "hora"])
async def programar_tarea(ctx, pedido, hora, dias=None):
    if _desde_tarea(ctx):
        return "Una tarea programada no puede crear otras tareas."
    return crear(pedido, hora, dias)


@herramienta("resumen_de_la_manana", "Programa el resumen diario de la mañana: clima, calendario de hoy y recordatorios, en voz y como aviso al celular.",
             {"hora": P("string", "Hora en formato HH:MM de 24 horas (por defecto 07:00)"),
              "dias": P("string", "'todos' (por defecto), 'entre semana', 'fin de semana' o días concretos")})
async def resumen_de_la_manana(ctx, hora="07:00", dias=None):
    if _desde_tarea(ctx):
        return "Una tarea programada no puede crear otras tareas."
    if any(t["pedido"] == RESUMEN for t in _todas()):
        return "El resumen de la mañana ya está programado; para cambiarlo, cancélalo primero con tareas_programadas."
    return crear(RESUMEN, hora, dias)


@herramienta("cierre_del_dia", "Programa el cierre diario: qué se hizo hoy, qué quedó pendiente y qué sigue mañana.",
             {"hora": P("string", "Hora en formato HH:MM de 24 horas (por defecto 21:00)"),
              "dias": P("string", "'todos' (por defecto), 'entre semana', 'fin de semana' o días concretos")})
async def cierre_del_dia(ctx, hora="21:00", dias=None):
    if _desde_tarea(ctx):
        return "Una tarea programada no puede crear otras tareas."
    if any(t["pedido"] == CIERRE for t in _todas()):
        return "El cierre del día ya está programado; para cambiarlo, cancélalo primero con tareas_programadas."
    return crear(CIERRE, hora, dias)


@herramienta("tareas_programadas", "Muestra o cancela las tareas programadas.",
             {"cancelar": P("string", "id de la tarea a cancelar, o 'todas' (opcional)")})
async def tareas_programadas(ctx, cancelar=None):
    lista = _todas()
    if cancelar:
        if _desde_tarea(ctx):
            return "Una tarea programada no puede cancelar tareas."
        quedan = [] if cancelar == "todas" else [t for t in lista if t["id"] != cancelar.lstrip("#")]
        _guardar(quedan)
        return f"Cancelé {len(lista) - len(quedan)} tarea(s)."
    if not lista:
        return "No hay tareas programadas."
    return "\n".join(f"- #{t['id']}: {_texto_dias(t['dias'])} a las {t['hora']}: {t['pedido'][:80]}" for t in lista)


async def correr(t: dict, agente, anunciar=None):
    async def nada(_):
        pass

    async def denegar(*_):  # nadie está presente para autorizar
        return False

    pedido = f"[Tarea programada: no hay nadie presente, no preguntes nada y responde en pocas frases.] {t['pedido']}"
    try:
        final = await asyncio.wait_for(agente.responder("tareas", pedido, nada, denegar, canal="tarea"), LIMITE)
    except Exception as e:  # noqa: BLE001 - una tarea rota no tumba las demás
        log.warning("tarea %s: %s: %s", t["id"], type(e).__name__, e)
        final = f"No pude hacer la tarea programada «{t['pedido'][:60]}»."
    anunciar = anunciar or ANUNCIAR
    if anunciar and final.strip():
        await anunciar(final)


async def vigilar(agente):
    """Bucle de fondo del cerebro: cada 30 s mira qué toca (una vez por día, por tarea)."""
    while True:
        await asyncio.sleep(30)
        try:
            ahora = datetime.now()
            lista = _todas()
            pendientes = [t for t in lista if toca(t, ahora)]
            if pendientes:
                for t in pendientes:  # se marca antes: si se reinicia a mitad, no se repite
                    t["ultima"] = ahora.strftime("%Y-%m-%d")
                _guardar(lista)
                for t in pendientes:
                    log.info("tarea programada: #%s", t["id"])
                    asyncio.create_task(correr(t, agente))
        except Exception as e:  # noqa: BLE001
            log.warning("tareas programadas: %s", e)
