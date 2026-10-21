import logging
import os
import secrets
import sys

import uvicorn

from . import config


_MUTEX = None


def ya_corre() -> bool:
    """Un solo núcleo por PC: si JARVIS.bat y el panel lateral lo arrancan a la vez, el segundo se va."""
    global _MUTEX  # el mutex vive mientras corre el núcleo
    if not config.ES_WINDOWS:
        return False
    import ctypes

    _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, "JARVIS.Nucleo")
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def main():
    if ya_corre():
        return
    archivo = logging.FileHandler(config.DATOS / "jarvis.log", encoding="utf-8")
    manejadores = [archivo]
    if sys.stdout is not None:  # con pythonw no hay consola
        manejadores.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=manejadores)

    # Expuesto en la red sin clave = cualquiera en tu wifi podría usarlo. Generamos una.
    if config.HOST not in ("127.0.0.1", "localhost") and not config.env("JARVIS_CLAVE"):
        ruta = config.DATOS / "clave.txt"
        clave = ruta.read_text().strip() if ruta.exists() else secrets.token_urlsafe(9)
        ruta.write_text(clave)
        os.environ["JARVIS_CLAVE"] = clave
        logging.warning("JARVIS está expuesto en la red. Clave de acceso: %s  (o define JARVIS_CLAVE en .env)", clave)

    # prioridad baja: JARVIS nunca le quita CPU a juegos, Fusion o simulaciones
    try:
        import psutil

        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if config.ES_WINDOWS else 10)
    except Exception:  # noqa: BLE001
        pass

    uvicorn.run("jarvis.servidor:app", host=config.HOST, port=config.PUERTO, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
