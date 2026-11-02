"""Pruebas de las tareas programadas, sin gastar IAs ni mandar avisos.
Ejecutar: python -m tests.test_tareas
"""
import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from jarvis import herramientas as h
from jarvis import tareas as t

LUNES = datetime(2026, 11, 2, 7, 0)


class AgenteFalso:
    def __init__(self, respuesta="Hoy hace sol."):
        self.respuesta, self.vistos = respuesta, []

    async def responder(self, sesion, texto, emit, confirmar, canal="web", imagenes=None):
        self.vistos.append((sesion, canal, await confirmar("borrar", {})))
        if isinstance(self.respuesta, Exception):
            raise self.respuesta
        return self.respuesta


def main():
    t.ARCHIVO = Path(tempfile.mkdtemp()) / "tareas.json"
    assert t._hora("7") == "07:00" and t._hora("7:30") == "07:30" and t._hora("25:00") is None and t._hora("siete") is None
    assert t._dias("todos") == list(range(7)) and t._dias("entre semana") == [0, 1, 2, 3, 4]
    assert t._dias("Miércoles y sábado") == [2, 5] and t._dias("fin de semana") == [5, 6] and t._dias("cuando sea") is None
    print("✓ entiende horas y días")

    tarea = {"id": "a", "pedido": "x", "hora": "07:00", "dias": [0]}
    assert t.toca(tarea, LUNES) and t.toca(tarea, LUNES.replace(minute=59)) and not t.toca(tarea, LUNES.replace(hour=8))
    assert not t.toca(tarea, LUNES.replace(hour=6, minute=59)), "todavía no"
    assert not t.toca(tarea, datetime(2026, 11, 3, 7, 0)), "martes no"
    assert not t.toca({**tarea, "ultima": "2026-11-02"}, LUNES), "una vez al día"
    print("✓ corre a su hora, su día y una sola vez")

    assert "No entendí" in t.crear("x", "mañana")
    assert "Listo" in t.crear("x", "07:00", "lunes", ahora=LUNES.replace(minute=30))
    assert not t.toca(t._todas()[0], LUNES.replace(minute=31)), "recién creada no se dispara por una hora ya pasada"
    assert "Listo" in asyncio.run(t.resumen_de_la_manana(SimpleNamespace(canal="web")))
    assert "ya está programado" in asyncio.run(t.resumen_de_la_manana(SimpleNamespace(canal="web")))
    print("✓ crear valida, no se dispara al crearla y el resumen no se duplica")

    ctx = SimpleNamespace(canal="tarea")
    assert "no puede" in asyncio.run(t.programar_tarea(ctx, "x", "08:00"))
    assert "no puede" in asyncio.run(t.tareas_programadas(ctx, "todas"))
    assert not h.HERRAMIENTAS["programar_tarea"]["solo_pc"]
    avisos = []

    async def anunciar(texto):
        avisos.append(texto)

    t.ANUNCIAR = anunciar
    agente = AgenteFalso()
    asyncio.run(t.correr(tarea, agente))
    assert agente.vistos == [("tareas", "tarea", False)] and avisos == ["Hoy hace sol."], "lo peligroso se deniega solo"
    asyncio.run(t.correr(tarea, AgenteFalso(RuntimeError("caída"))))
    assert "No pude" in avisos[-1]
    print("✓ sin nadie presente nada peligroso pasa, una tarea no crea tareas y los fallos se avisan")

    assert "Cancelé 2" in asyncio.run(t.tareas_programadas(SimpleNamespace(canal="web"), "todas")) and not t._todas()
    print("✓ se cancelan\n\nTodas las pruebas pasaron.")


if __name__ == "__main__":
    main()
