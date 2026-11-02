"""Memoria por proyecto y cierre del día.
Ejecutar: python -m tests.test_proyectos
"""
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from jarvis import proyectos as p
from jarvis import tareas as t


def main():
    base = Path(tempfile.mkdtemp())
    p.ARCHIVO = base / "proyectos.json"
    t.ARCHIVO = base / "tareas.json"
    p.anotar("Caja negra del cohete", "Usar ESP32-S3", "decision")
    p.anotar("cohete", "Cambiar el BMP280 dañado", "pendiente")
    p.anotar("COHETE", "La microSD sigue sin llegar")
    assert list(p._todos()) == ["caja negra del cohete"], "el mismo proyecto aunque se nombre distinto"
    r = p.resumen("cohete")
    assert "Usar ESP32-S3" in r and "BMP280" in r and "microSD" in r
    assert "No encontré" in p.resolver("cohete", "motor")
    assert "quedó hecho" in p.resolver("cohete", "bmp280")
    assert "BMP280" not in p.resumen("cohete")
    assert "No tengo" in p.resumen("impresora") and "cohete" in p.resumen("impresora")
    print("✓ guarda decisiones y pendientes, encuentra el proyecto y marca lo hecho")

    for i in range(p.MAXIMO + 5):
        p.anotar("cohete", f"nota {i}")
    assert len(p._todos()["caja negra del cohete"]["notas"]) <= p.MAXIMO
    print("✓ no crece sin límite")

    assert "Listo" in asyncio.run(t.cierre_del_dia(SimpleNamespace(canal="web")))
    assert "ya está programado" in asyncio.run(t.cierre_del_dia(SimpleNamespace(canal="web")))
    assert t._todas()[0]["hora"] == "21:00" and "registro_de_acciones" in t._todas()[0]["pedido"]
    print("✓ el cierre del día se programa una sola vez\n\nTodas las pruebas pasaron.")


if __name__ == "__main__":
    main()
