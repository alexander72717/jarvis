import logging
import os
import secrets
import sys

import uvicorn

from . import config


def main():
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

    uvicorn.run("jarvis.servidor:app", host=config.HOST, port=config.PUERTO, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
