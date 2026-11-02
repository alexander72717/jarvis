"""Dónde muestra JARVIS un tablero de análisis: en el panel de todas las pantallas o, si este equipo es Windows
y piensa solo, en una ventana propia. Con el cerebro en el servidor siempre va al panel (el servidor no abre ventanas)."""
import re
import subprocess
import unicodedata

from . import config

URL = f"http://127.0.0.1:{config.PUERTO}"
_VACIAS = set(("para pero como este esta esto esos esas sobre entre desde hasta cuando donde porque tiene tienen puede pueden "
               "seria hacer queda mucho poco menos senor usted ustedes nuestro nuestra todo toda todos todas cada mismo misma "
               "algo bien ahora entonces tambien unos unas aqui alli listo bueno claro dale vale gracias quiero quisiera").split())


def _claves(texto: str) -> set[str]:
    """Las palabras que dicen de qué se habla (raíces de 5 letras, sin tildes ni relleno) y las cifras."""
    t = unicodedata.normalize("NFD", re.sub(r"<[^>]+>", " ", texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    palabras = {w[:5] for w in re.findall(r"[a-zñ]{4,}", t) if w not in _VACIAS}
    return palabras | {n.replace(".", "") for n in re.findall(r"\d[\d.]{1,}", t)}


def _abrir_ventana(ruta: str):
    from . import aperturas

    url = URL + ruta
    navegador = aperturas.ruta_navegador("edge") or aperturas.ruta_navegador("chrome")
    if navegador:
        subprocess.Popen([navegador, f"--app={url}", "--window-size=1320,860", "--autoplay-policy=no-user-gesture-required"])
    else:
        aperturas.abrir_web(url, None)


async def mostrar_tablero(ctx, t: dict, donde: str = "auto") -> str:
    if ctx.difundir:
        await ctx.difundir({"type": "tablero", "tablero": t})
    if donde == "ventana" and config.ES_WINDOWS:
        _abrir_ventana(f"/tablero?id={t['id']}")
        return "en una ventana grande en el PC"
    if ctx.difundir:
        await ctx.difundir({"type": "tablero_mostrar", "id": t["id"], "titulo": t["titulo"]})
        return "en tu panel"
    return f"en /tablero?id={t['id']}"
