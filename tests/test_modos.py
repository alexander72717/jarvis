"""Pruebas de los modos sin tocar el PC: los pasos se registran en vez de ejecutarse.
Ejecutar: .venv\\Scripts\\python -m tests.test_modos
"""
import asyncio
import tempfile
from pathlib import Path

from jarvis import modos as m
from jarvis.herramientas import Contexto


def main():
    tmp = Path(tempfile.mkdtemp())
    m.ARCHIVO, m.ACTIVO = tmp / "modos.json", tmp / "activo.json"
    pasos = []

    async def falso(nombre, args, ctx):
        pasos.append((nombre, args))
        return "Volumen en 25 % (antes 60)." if nombre == "volumen_pc" else "ok"

    m._paso = falso
    ctx = Contexto.__new__(Contexto)
    ctx.difundir = None
    correr = asyncio.run

    r = correr(m.entrar(ctx, "trabajo"))
    assert "PREGUNTA" in r and not pasos and m.activo() is None
    print("✓ el modo trabajo pregunta en qué vas a trabajar antes de abrir nada")

    correr(m.entrar(ctx, "trabajo", tarea="voy a diseñar una pcb nueva"))
    assert ("abrir", {"objetivo": "KiCad 10.0"}) in pasos and m.activo()["tarea"] == "una PCB"
    assert "MODO TRABAJO" in m.instruccion()
    print("✓ según el trabajo abre lo que corresponde y JARVIS cambia su estilo")

    pasos.clear()
    correr(m.entrar(ctx, "modo juego", juego="battlefield"))
    assert pasos[1] == ("abrir_juego", {"nombre": "battlefield"}) and m.activo()["nombre"] == "juego"
    print("✓ el modo juego pone música y abre el juego pedido (cambiar de modo cierra el anterior)")

    pasos.clear()
    correr(m.entrar(ctx, "noche"))
    correr(m.salir(ctx))
    assert pasos == [("volumen_pc", {"nivel": "25"}), ("volumen_pc", {"nivel": "60"})] and m.activo() is None
    print("✓ el modo noche baja el volumen y al salir lo devuelve como estaba")

    assert {t: m._tarea(m.BASE["trabajo"], t) for t in ("código de jarvis", "el robot de vex", "firmware del esp32")} == \
        {"código de jarvis": "codigo", "el robot de vex": "robot vex", "firmware del esp32": "arduino"}
    desde, hasta = correr(m._ventana_noche({"auto": {"desde": "19:00", "hasta": "06:00"}}))
    assert desde < hasta and (hasta - desde).total_seconds() == 11 * 3600
    print("✓ tareas por palabras clave y ventana de la noche")
    print("\nTodas las pruebas pasaron.")


if __name__ == "__main__":
    main()
