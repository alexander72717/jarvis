"""Calendario: lo que TÚ tienes que hacer cada día. No confundir con las tareas programadas, que las hace JARVIS solo.

Vive en el cerebro, como los proyectos: la misma semana se ve desde el PC y el celular.
"""
import json
import os
import re
import uuid
from datetime import date, timedelta

from . import config
from .herramientas import P, herramienta
from . import proyectos, tareas

ARCHIVO = config.DATOS / "calendario.json"
MAXIMO = 400
NOMBRES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def _todas() -> list[dict]:
    try:
        return json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _guardar(lista: list[dict]):
    temporal = ARCHIVO.with_suffix(".tmp")
    temporal.write_text(json.dumps(lista, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporal, ARCHIVO)


def dia(texto: str | None, hoy: date | None = None) -> date | None:
    """Convierte «hoy», «mañana», «el viernes», «próximo lunes» o AAAA-MM-DD en una fecha. None si no la entiende."""
    hoy = hoy or date.today()
    crudo = (texto or "hoy").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", crudo):
        try:
            return date.fromisoformat(crudo)
        except ValueError:
            return None
    t = proyectos._clave(crudo)
    for palabra, salto in (("pasado manana", 2), ("manana", 1), ("hoy", 0), ("ayer", -1)):
        if palabra in t:
            return hoy + timedelta(salto)
    semana = next((i for i, d in enumerate(tareas.DIAS) if d in t), None)
    if semana is None:
        return None
    salto = (semana - hoy.weekday()) % 7
    if salto == 0 and any(p in t for p in ("proximo", "siguiente", "que viene")):
        salto = 7
    return hoy + timedelta(salto)


def legible(f: date) -> str:
    return f"{NOMBRES[f.weekday()]} {f.day} de {MESES[f.month - 1]}"


def _cae_en(item: dict, f: date) -> bool:
    iso = f.isoformat()
    if item.get("fecha"):
        return item["fecha"] == iso
    return f.weekday() in item["dias"] and iso >= item["desde"] and iso not in item.get("omitidas", [])


def del_dia(f: date, lista: list[dict] | None = None) -> list[dict]:
    lista = _todas() if lista is None else lista
    filas = [{"id": i["id"], "texto": i["texto"], "hora": i.get("hora"), "hecha": f.isoformat() in i.get("hechas", []),
              "repite": tareas._texto_dias(i["dias"]) if i.get("dias") else None} for i in lista if _cae_en(i, f)]
    return sorted(filas, key=lambda x: x["hora"] or "99:99")  # lo que tiene hora primero, en orden


def semana(desde: str | None = None) -> dict:
    """Los siete días, de lunes a domingo, de la semana que contiene `desde` (por defecto, la actual)."""
    hoy = date.today()
    base = dia(desde) or hoy
    lunes = base - timedelta(base.weekday())
    domingo = lunes + timedelta(6)
    lista = _todas()
    dias = [lunes + timedelta(n) for n in range(7)]
    titulo = f"{lunes.day} de {MESES[lunes.month - 1]} al {domingo.day} de {MESES[domingo.month - 1]}"
    return {"titulo": titulo, "desde": lunes.isoformat(), "hoy": hoy.isoformat(),
            "dias": [{"fecha": f.isoformat(), "nombre": NOMBRES[f.weekday()], "numero": f.day, "items": del_dia(f, lista)} for f in dias]}


def agregar(texto: str, cuando: str | None = "hoy", hora: str | None = None, repetir: str | None = None, hoy: date | None = None) -> str:
    hoy = hoy or date.today()
    texto = (texto or "").strip()[:200]
    if not texto:
        return "No me dijiste qué anotar."
    h = tareas._hora(hora) if hora else None
    if hora and not h:
        return f"No entendí la hora «{hora}». Dámela como 15:30."
    f = dia(cuando, hoy)
    if not f:
        return f"No entendí el día «{cuando}»."
    item = {"id": uuid.uuid4().hex[:5], "texto": texto, "hora": h, "hechas": []}
    if repetir:
        dias = tareas._dias(repetir)
        if dias is None:
            return f"No entendí cada cuánto se repite: «{repetir}»."
        item.update(dias=dias, desde=f.isoformat())
    else:
        item["fecha"] = f.isoformat()
    lista = _todas()
    if len(lista) >= MAXIMO:  # lo primero que sobra es lo de una sola vez que ya pasó
        lista = [i for i in lista if i.get("dias") or i["fecha"] >= hoy.isoformat()]
    if len(lista) >= MAXIMO:
        return f"Ya hay {MAXIMO} cosas en el calendario; quita alguna primero."
    _guardar(lista + [item])
    return f"Anotado: {texto}, {tareas._texto_dias(item['dias']) if repetir else legible(f)}{f' a las {h}' if h else ''}."


def _hallar(lista: list[dict], tarea: str) -> list[dict]:
    ident = tarea.strip().lstrip("#")
    exacta = [i for i in lista if i["id"] == ident]
    c = proyectos._clave(tarea)
    return exacta or [i for i in lista if c and c in proyectos._clave(i["texto"])]


def marcar(tarea: str, cuando: str | None = "hoy", hecha: bool = True) -> str:
    f = dia(cuando)
    if not f:
        return f"No entendí el día «{cuando}»."
    lista = _todas()
    iso = f.isoformat()
    hallados = _hallar([i for i in lista if _cae_en(i, f)], tarea)
    if not hallados:
        return f"No encontré «{tarea}» el {legible(f)}."
    item = next((i for i in hallados if (iso in i["hechas"]) != hecha), hallados[0])
    if hecha and iso not in item["hechas"]:
        item["hechas"] = (item["hechas"] + [iso])[-120:]
    elif not hecha and iso in item["hechas"]:
        item["hechas"].remove(iso)
    _guardar(lista)
    return f"Listo, «{item['texto']}» quedó {'hecha' if hecha else 'pendiente'}."


def quitar(tarea: str, cuando: str | None = None) -> str:
    """Con día, en algo que se repite, solo salta ese día. Sin día, lo quita del todo."""
    f = dia(cuando) if cuando else None
    if cuando and not f:
        return f"No entendí el día «{cuando}»."
    lista = _todas()
    hallados = _hallar([i for i in lista if _cae_en(i, f)] if f else lista, tarea)
    if not hallados:
        return f"No encontré «{tarea}» en el calendario."
    if len(hallados) > 1:  # borrar lo que no era no tiene vuelta atrás
        return "Hay varias parecidas, ¿cuál quito? " + "; ".join(f"#{i['id']} {i['texto']}" for i in hallados[:6])
    item = hallados[0]
    if f and item.get("dias"):
        item["omitidas"] = (item.get("omitidas", []) + [f.isoformat()])[-120:]
        _guardar(lista)
        return f"Listo, el {legible(f)} no va «{item['texto']}»."
    _guardar([i for i in lista if i is not item])
    return f"Quité «{item['texto']}» del calendario."


def _fila(i: dict) -> str:
    return f"- {i['hora'] + ' ' if i['hora'] else ''}{i['texto']}{' (hecha)' if i['hecha'] else ''} [#{i['id']}]"


def ver(cuando: str | None = "hoy", hoy: date | None = None) -> str:
    hoy = hoy or date.today()
    t = proyectos._clave(cuando or "hoy")
    if "semana" in t:
        base = hoy + timedelta(7) if any(p in t for p in ("proxima", "siguiente", "que viene")) else hoy
        lunes = base - timedelta(base.weekday())
        lista = _todas()
        partes = []
        for f in (lunes + timedelta(n) for n in range(7)):
            filas = del_dia(f, lista)
            if filas:
                partes.append(legible(f) + ":\n" + "\n".join(_fila(i) for i in filas))
        return "\n".join(partes) or "No tienes nada en el calendario esa semana."
    f = dia(cuando, hoy)
    if not f:
        return f"No entendí el día «{cuando}»."
    filas = del_dia(f)
    if not filas:
        return f"No tienes nada para el {legible(f)}."
    return f"{legible(f)}:\n" + "\n".join(_fila(i) for i in filas)


CUANDO = "'hoy', 'mañana', un día de la semana ('viernes') o AAAA-MM-DD"


@herramienta("calendario_agregar", "Anota en el calendario algo que el usuario TIENE QUE HACER: tareas del día, citas, entregas, o cosas diarias o "
             "semanales («todos los días repasar inglés», «el viernes tengo examen»). Lo que deba hacer JARVIS solo va en programar_tarea.",
             {"tarea": P("string", "Qué tiene que hacer, en pocas palabras"), "cuando": P("string", CUANDO + " (si se repite, desde cuándo)"),
              "hora": P("string", "HH:MM de 24 horas, solo si dijo una hora"),
              "repetir": P("string", "Solo si se repite: 'todos', 'entre semana', 'fin de semana' o días como 'lunes, jueves'")}, ["tarea"])
async def calendario_agregar(ctx, tarea, cuando="hoy", hora=None, repetir=None):
    return agregar(tarea, cuando, hora, repetir)


@herramienta("calendario_ver", "Dice lo que el usuario tiene en un día o semana: «¿qué tengo hoy?», «¿qué hay el jueves?», «¿cómo viene la semana?».",
             {"cuando": P("string", CUANDO + ", 'semana' o 'próxima semana'")})
async def calendario_ver(ctx, cuando="hoy"):
    return ver(cuando)


@herramienta("calendario_completar", "Marca como hecha (o pendiente otra vez) una tarea del calendario en un día.",
             {"tarea": P("string", "Palabras de la tarea o su #id"), "cuando": P("string", CUANDO),
              "hecha": P("boolean", "false para volverla pendiente")}, ["tarea"])
async def calendario_completar(ctx, tarea, cuando="hoy", hecha=True):
    return marcar(tarea, cuando, hecha)


@herramienta("calendario_quitar", "Quita algo del calendario. Con 'cuando', en algo repetido salta solo ese día; sin 'cuando', lo quita del todo.",
             {"tarea": P("string", "Palabras de la tarea o su #id"), "cuando": P("string", CUANDO + " (opcional)")}, ["tarea"])
async def calendario_quitar(ctx, tarea, cuando=None):
    return quitar(tarea, cuando)


@herramienta("calendario_mostrar", "Abre en pantalla el panel con toda la semana y sus tareas: «muéstrame la semana», «abre el calendario».",
             {"cuando": P("string", "Un día de la semana a mostrar, si no es la actual (opcional)")})
async def calendario_mostrar(ctx, cuando=None):
    if not ctx.difundir:
        return "Desde aquí no puedo abrir el panel, pero te cuento la semana con calendario_ver."
    await ctx.difundir({"type": "calendario_mostrar", "desde": semana(cuando)["desde"]})
    return "Listo, tu semana está en pantalla."
