"""Memoria por proyecto: «¿en qué quedamos con el cohete?» devuelve decisiones, pendientes y novedades.

Vive en el cerebro, como la memoria general: el mismo proyecto se ve desde el PC y el celular.
"""
import json
import os
import re
import unicodedata
from datetime import datetime

from . import config
from .herramientas import P, herramienta

ARCHIVO = config.DATOS / "proyectos.json"
TIPOS = ("decision", "pendiente", "nota")
MAXIMO = 60  # notas por proyecto: lo viejo ya resuelto no debe llenar las instrucciones


def _todos() -> dict:
    try:
        return json.loads(ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar(datos: dict):
    temporal = ARCHIVO.with_suffix(".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporal, ARCHIVO)


def _clave(nombre: str) -> str:
    t = unicodedata.normalize("NFD", nombre.lower())
    return re.sub(r"[^a-z0-9ñ]+", " ", "".join(c for c in t if unicodedata.category(c) != "Mn")).strip()


def buscar(nombre: str, datos: dict | None = None) -> str | None:
    """La clave del proyecto por nombre exacto o parcial («cohete» encuentra «caja negra del cohete»)."""
    datos = _todos() if datos is None else datos
    c = _clave(nombre)
    if c in datos:
        return c
    return next((k for k in datos if c and (c in k or k in c)), None)


def anotar(nombre: str, texto: str, tipo: str = "nota", ahora: datetime | None = None) -> str:
    datos = _todos()
    clave = buscar(nombre, datos) or _clave(nombre)
    if not clave:
        return "Dime de qué proyecto se trata."
    p = datos.setdefault(clave, {"nombre": nombre.strip(), "notas": []})
    fecha = (ahora or datetime.now()).strftime("%Y-%m-%d")
    p["notas"].append({"tipo": tipo if tipo in TIPOS else "nota", "texto": texto.strip()[:300], "fecha": fecha, "hecha": False})
    abiertas = [n for n in p["notas"] if n["tipo"] != "pendiente" or not n["hecha"]]
    p["notas"] = (abiertas if len(p["notas"]) > MAXIMO else p["notas"])[-MAXIMO:]
    p["actualizado"] = fecha
    _guardar(datos)
    return f"Anotado en «{p['nombre']}»."


def resumen(nombre: str) -> str:
    datos = _todos()
    clave = buscar(nombre, datos)
    if not clave:
        return f"No tengo ningún proyecto «{nombre}». Los que tengo: {', '.join(p['nombre'] for p in datos.values()) or 'ninguno todavía'}."
    p = datos[clave]
    grupos = (("Decisiones", lambda n: n["tipo"] == "decision"), ("Pendientes", lambda n: n["tipo"] == "pendiente" and not n["hecha"]),
              ("Notas", lambda n: n["tipo"] == "nota"))
    partes = [f"Proyecto «{p['nombre']}» (última novedad: {p.get('actualizado', '?')})"]
    for titulo, cumple in grupos:
        filas = [f"- {n['texto']} ({n['fecha']})" for n in p["notas"] if cumple(n)][-12:]
        if filas:
            partes.append(titulo + ":\n" + "\n".join(filas))
    return "\n".join(partes)


def resolver(nombre: str, pendiente: str) -> str:
    datos = _todos()
    clave = buscar(nombre, datos)
    if not clave:
        return f"No tengo ningún proyecto «{nombre}»."
    c = _clave(pendiente)
    hallada = next((n for n in datos[clave]["notas"] if n["tipo"] == "pendiente" and not n["hecha"] and c in _clave(n["texto"])), None)
    if not hallada:
        return f"No encontré ese pendiente en «{datos[clave]['nombre']}»."
    hallada["hecha"] = True
    _guardar(datos)
    return f"Listo, «{hallada['texto']}» quedó hecho."


@herramienta("proyecto_anotar", "Guarda en un proyecto una decisión, un pendiente o una nota. Úsala sola cuando en la charla se decida algo "
             "o quede algo por hacer en un proyecto concreto (el cohete, Smurfit, JARVIS…).",
             {"proyecto": P("string", "Nombre del proyecto"), "texto": P("string", "Qué guardar, en una frase"),
              "tipo": P("string", "Qué es", list(TIPOS))}, ["proyecto", "texto"])
async def proyecto_anotar(ctx, proyecto, texto, tipo="nota"):
    return anotar(proyecto, texto, tipo)


@herramienta("proyecto_ver", "Cuenta en qué va un proyecto: «¿en qué quedamos con el cohete?», «qué falta en…». Sin nombre, lista los proyectos.",
             {"proyecto": P("string", "Nombre del proyecto (opcional)")})
async def proyecto_ver(ctx, proyecto=None):
    if proyecto:
        return resumen(proyecto)
    datos = _todos()
    return "\n".join(f"- {p['nombre']} ({p.get('actualizado', '?')})" for p in datos.values()) or "Todavía no hay proyectos."


@herramienta("proyecto_resolver", "Marca como hecho un pendiente de un proyecto.",
             {"proyecto": P("string", "Nombre del proyecto"), "pendiente": P("string", "Palabras del pendiente")}, ["proyecto", "pendiente"])
async def proyecto_resolver(ctx, proyecto, pendiente):
    return resolver(proyecto, pendiente)
