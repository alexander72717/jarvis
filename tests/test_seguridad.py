"""Ninguna web ajena puede usar JARVIS desde el navegador del PC.
Ejecutar: python -m tests.test_seguridad
"""
import os
from types import SimpleNamespace

from jarvis import servidor as s


def conexion(ip="127.0.0.1", **cabeceras):
    return SimpleNamespace(client=SimpleNamespace(host=ip), cookies={},
                           headers={k.replace("_", "-"): v for k, v in cabeceras.items()})


def main():
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
