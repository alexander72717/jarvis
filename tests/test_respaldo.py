"""Copias de seguridad y registro de acciones.
Ejecutar: python -m tests.test_respaldo
"""
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from jarvis import registro, respaldo as r


def main():
    base = Path(tempfile.mkdtemp())
    datos, destino = base / "data", base / "copias"
    (datos / "tableros").mkdir(parents=True)
    (datos / "archivos").mkdir()
    (datos / "tableros" / "a.json").write_text("{}")
    (datos / "archivos" / "video.mp4").write_text("pesado")
    for secreto in ("clave.txt", "pases.json", "vapid_privada.pem", "push.json"):
        (datos / secreto).write_text("secreto")
    (datos / "jarvis.log").write_text("ruido")
    db = sqlite3.connect(datos / "jarvis.db")
    db.execute("CREATE TABLE hechos(texto)")
    db.execute("INSERT INTO hechos VALUES ('el café sin azúcar')")
    db.commit()
    db.close()

    z = r.crear(datos, destino)
    with zipfile.ZipFile(z) as f:
        nombres = set(f.namelist())
        assert nombres == {"jarvis.db", "tableros/a.json"}, nombres
        (base / "restaurado.db").write_bytes(f.read("jarvis.db"))
    fila = sqlite3.connect(base / "restaurado.db").execute("SELECT texto FROM hechos").fetchone()
    assert fila == ("el café sin azúcar",)
    print("✓ la copia guarda lo importante, sin claves ni pesos, y la base se restaura")

    for i in range(1, 10):
        (destino / f"respaldo-2026-01-0{i}.zip").write_text("x")
    r.limpiar(destino, "respaldo")
    assert len(list(destino.glob("respaldo-*.zip"))) == r.CONSERVAR
    r.CARPETA = destino
    hoy = datetime.now().replace(hour=4)
    assert not r.pendiente(ahora=hoy.replace(hour=2)), "aún no es la hora"
    assert r.ultimo().name == z.name
    assert not r.pendiente(ahora=hoy), "ya está la de hoy"
    assert r.pendiente("cerebro", ahora=hoy)
    print("✓ conserva las últimas y hace una al día")

    registro.ARCHIVO = base / "acciones.jsonl"
    registro.anotar("cerrar", {"app": "x" * 500}, "celular", "ok", True)
    registro.anotar("clima", {}, "web", "sol")
    registro.anotar("energia", {}, "tarea", "no", False)
    filas = registro.ultimas(5)
    assert len(filas) == 3 and len(filas[0]["datos"]) == 200 and filas[-1]["autorizada"] is False
    assert "NO autorizada" in registro._linea(filas[-1]) and registro.ultimas(5, "1999-01-01") == []
    print("✓ registra las acciones, recortadas y con su permiso\n\nTodas las pruebas pasaron.")


if __name__ == "__main__":
    main()
