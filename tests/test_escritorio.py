"""Portapapeles con memoria, modo enfoque, modos sugeridos y respaldo local sin internet.
Ejecutar: python -m tests.test_escritorio
"""
import asyncio
import tempfile
import time
from pathlib import Path

from jarvis import enfoque, escritorio as e, proactivo
from jarvis.agente import Agente
from jarvis.router import SinProveedores


class RouterSinNube:
    """La nube falla; la IA local responde."""
    def __init__(self):
        self.niveles = []

    def hay_local(self):
        return True

    def cuotas_bajas(self):
        return False

    async def stream(self, mensajes, esquemas, nivel, vision=False):
        self.niveles.append(nivel)
        if nivel != "local":
            raise SinProveedores("sin internet")
        yield {"type": "delta", "text": "Sigo aquí."}
        yield {"type": "fin", "texto": "Sigo aquí.", "llamadas": [], "proveedor": "local", "modelo": "x", "segundos": 1, "tokens": 1}


class MemoriaFalsa:
    def guardar_mensaje(self, *a, **k): pass
    def historial(self, s, n): return [{"role": "user", "content": "hola"}]
    def buscar(self, *a): return []
    def hechos(self, *a): return []
    def contar_usuario(self, s): return 1


def main():
    for secreto in ("sk-abc123456789", "AIzaSyA1234567890", "Tr0ub4dor&3xyz", "ghp_abcdefghijklmnop"):
        assert e.parece_secreto(secreto), secreto
    for normal in ("hola mundo", "SpixBuild", "https://ejemplo.com/pagina", "el sensor BMP280 está dañado", "12345678"):
        assert not e.parece_secreto(normal), normal
    e.HISTORIAL.clear()
    e.anotar("el sensor BMP280 está dañado")
    e.anotar("el sensor BMP280 está dañado")
    e.anotar("Tr0ub4dor&3xyz")
    e.anotar("receta de café")
    assert [f["texto"] for f in e._buscar()] == ["receta de café", "el sensor BMP280 está dañado"]
    assert len(e._buscar("sensor")) == 1 and e._buscar("clave") == []
    print("✓ el portapapeles recuerda, busca y no guarda secretos")

    enfoque.ARCHIVO = Path(tempfile.mkdtemp()) / "enfoque.json"
    cambios = []
    enfoque._notificaciones = lambda v: (cambios.append(v), 1)[1]
    assert not enfoque.activo()
    enfoque.empezar(10)
    assert enfoque.activo() and cambios == [0]
    enfoque.empezar(20)
    assert cambios == [0], "repetirlo no pisa el valor original"
    assert enfoque.terminar() and cambios == [0, 1] and not enfoque.activo() and not enfoque.terminar()
    print("✓ el modo enfoque calla Windows y devuelve lo que estaba")

    v = proactivo.Vigilante()
    v.estado = {"temas": {}, "ultimo": 0, "hoy": "", "dichos_hoy": 0}
    assert v.puede("disco", 2, False, True)
    proactivo.enfoque_activo = lambda: True
    assert not v.puede("disco", 2, False, True) and v.puede("bateria", 3, False, True), "en enfoque solo lo urgente"
    proactivo.enfoque_activo = lambda: False
    assert v.puede("modo:juego", 1, False, True) and not v.puede("modo:juego", 1, False, True, "trabajo"), "con un modo activo no sugiere otro"
    assert proactivo.nota_modo("steamwebhelper")[0] == "modo:juego" and proactivo.nota_modo("Fusion360")[0] == "modo:trabajo"
    assert proactivo.nota_modo("notepad") is None
    print("✓ en enfoque no interrumpe y sugiere el modo según lo que abres")

    router = RouterSinNube()
    agente = Agente(router, MemoriaFalsa())

    async def sistema(*a, **k):
        return "s"

    async def contexto(*a):
        return ""

    agente.sistema, agente.contexto_local = sistema, contexto
    final = asyncio.run(agente.responder("web", "cuéntame algo largo sobre el universo y su historia completa", lambda ev: asyncio.sleep(0), None))
    assert final == "Sigo aquí." and "local" in router.niveles, router.niveles
    print("✓ sin IAs en la nube, responde la local\n\nTodas las pruebas pasaron.")


if __name__ == "__main__":
    assert time.time()
    main()
