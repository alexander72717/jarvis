"""Ninguna web ajena puede usar JARVIS desde el navegador del PC.
Ejecutar: python -m tests.test_seguridad
"""
import asyncio
import json
import os
from types import SimpleNamespace

from jarvis import herramientas, servidor as s
from jarvis.nodos import Nodo, Registro


def conexion(ip="127.0.0.1", **cabeceras):
    return SimpleNamespace(client=SimpleNamespace(host=ip), cookies={},
                           headers={k.replace("_", "-"): v for k, v in cabeceras.items()})


class WsFalso:
    def __init__(self, registro):
        self.registro, self.enviados = registro, []

    async def send_text(self, crudo):
        m = json.loads(crudo)
        self.enviados.append(m)
        self.registro.nodos["PC"].pendientes[m["id"]].set_result(json.dumps({"ok": True}))


async def relevo():
    """El gesto del celular llega al PC por un canal propio; sin nodo con el control prendido, no pasa nada."""
    r = Registro()
    ws = WsFalso(r)
    r.nodos["PC"] = Nodo(ws, "PC", [], control=False)
    assert "error" in await r.control({"t": "clic"})
    r.nodos["PC"].control = True
    assert await r.control({"t": "clic"}) == {"ok": True} and ws.enviados[0]["type"] == "control"


def main():
    asyncio.run(relevo())
    assert {n for n in herramientas.HERRAMIENTAS if "remoto" in n} == {"abrir_control_remoto", "cerrar_control_remoto"}  # la IA solo abre el panel
    from jarvis import control_remoto
    os.environ["CONTROL_REMOTO"] = "0"
    assert not control_remoto.activo() and "apagado" in control_remoto.atender({"t": "cuadro"})["error"]
    propia = dict(host="127.0.0.1:8765", origin="http://127.0.0.1:8765")
    assert s._es_local(conexion(**propia)) and s._origen_valido(conexion(**propia))
    assert s._es_local(conexion(host="[::1]:8765"))
    # otra web abierta en el PC
    assert not s._ws_permitido(conexion(host="127.0.0.1:8765", origin="https://malo.example"))
    assert not s._origen_valido(conexion(host="127.0.0.1:8765", origin="null"))
    # dominio ajeno apuntando a 127.0.0.1 (DNS rebinding): mismo Origin y Host, pero no es local
    os.environ.pop("JARVIS_CLAVE", None)
    assert not s._ws_permitido(conexion(host="malo.example:8765", origin="http://malo.example:8765"))
    # proxy local (tailscale serve) y equipos de la red: necesitan la clave
    os.environ["JARVIS_CLAVE"] = "x"
    assert not s._es_local(conexion(host="127.0.0.1:8765", x_forwarded_for="100.1.1.1"))
    assert not s._ws_permitido(conexion("100.1.1.1", host="pc.ts.net"))
    assert s._ws_permitido(conexion("100.1.1.1", host="pc.ts.net", x_jarvis_clave="x"))  # el nodo no manda Origin
    assert s._ws_permitido(conexion("100.1.1.1", host="pc.ts.net", origin="https://pc.ts.net", x_jarvis_clave="x"))
    print("seguridad: OK")


if __name__ == "__main__":
    main()
