"""Copias de seguridad diarias de lo que no se puede recuperar: memoria, tableros, modos, tareas, habilidades.

Cada equipo guarda las suyas y el PC enlazado se trae además la del cerebro: si se daña el disco del servidor,
la copia sigue en el PC. Quedan fuera las claves (se regeneran) y lo que pesa y se puede volver a crear.
Para restaurar: con JARVIS detenido, descomprime el .zip dentro de la carpeta data/.
"""
import asyncio
import logging
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import config
from .herramientas import herramienta

log = logging.getLogger("jarvis.respaldo")
CARPETA = config.DATOS / "respaldos"
CONSERVAR = 7
HORA = 3  # de madrugada, cuando nadie está usando JARVIS
SIN_COPIA = {"respaldos", "archivos", "edge_lateral", "impresion3d", "previas3d"}
SECRETOS = {"clave.txt", "pases.json", "vapid_privada.pem", "push.json"}


def _entra(relativa: Path) -> bool:
    return (relativa.parts[0] not in SIN_COPIA and relativa.name not in SECRETOS
            and relativa.suffix not in (".log", ".tmp", ".db-wal", ".db-shm", ".db-journal"))


def crear(datos: Path | None = None, destino: Path | None = None, prefijo: str = "respaldo") -> Path:
    datos, destino = datos or config.DATOS, destino or CARPETA
    destino.mkdir(parents=True, exist_ok=True)
    final = destino / f"{prefijo}-{datetime.now():%Y-%m-%d}.zip"
    temporal = final.with_suffix(".parcial")
    with tempfile.TemporaryDirectory() as t, zipfile.ZipFile(temporal, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(p for p in datos.rglob("*") if p.is_file()):
            rel = f.relative_to(datos)
            if not _entra(rel):
                continue
            if f.suffix == ".db":  # copiar el archivo a secas puede pillar la base a mitad de una escritura
                copia = Path(t) / f.name
                origen, salida = sqlite3.connect(f), sqlite3.connect(copia)
                try:
                    origen.backup(salida)
                finally:
                    origen.close()
                    salida.close()
                z.write(copia, rel)
            else:
                z.write(f, rel)
    temporal.replace(final)
    limpiar(destino, prefijo)
    return final


def limpiar(destino: Path, prefijo: str):
    for viejo in sorted(destino.glob(f"{prefijo}-*.zip"))[:-CONSERVAR]:
        viejo.unlink()


def ultimo() -> Path | None:
    return max(CARPETA.glob("respaldo-*.zip"), default=None)


def pendiente(prefijo: str = "respaldo", ahora: datetime | None = None) -> bool:
    ahora = ahora or datetime.now()
    return ahora.hour >= HORA and not (CARPETA / f"{prefijo}-{ahora:%Y-%m-%d}.zip").exists()


@herramienta("hacer_respaldo", "Hace ya mismo una copia de seguridad de la memoria y los datos de JARVIS. Se hace sola cada noche.")
async def hacer_respaldo(ctx):
    r = await asyncio.to_thread(crear)
    return f"Copia lista: {r.name}, {r.stat().st_size / 1024:.0f} KB. Guardo las últimas {CONSERVAR}."


async def vigilar(enlace=None):
    """Una vez al día; el PC enlazado también se trae la copia del cerebro."""
    await asyncio.sleep(120)
    while True:
        try:
            if pendiente():
                r = await asyncio.to_thread(crear)
                log.info("copia de seguridad: %s", r.name)
            if enlace and enlace.conectado and pendiente("cerebro"):
                from .enlace import cabeceras, hub

                resp = await enlace.http.get(hub() + "/api/respaldo", headers=cabeceras(), timeout=120)
                resp.raise_for_status()
                CARPETA.mkdir(parents=True, exist_ok=True)
                (CARPETA / f"cerebro-{datetime.now():%Y-%m-%d}.zip").write_bytes(resp.content)
                limpiar(CARPETA, "cerebro")
                log.info("copia del cerebro guardada en este PC")
        except Exception as e:  # noqa: BLE001
            log.warning("copia de seguridad: %s: %s", type(e).__name__, e)
        await asyncio.sleep(1800)
