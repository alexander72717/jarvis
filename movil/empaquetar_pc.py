"""Genera dist/JARVIS-para-amigos.html: la app completa en UN solo archivo.

Tus amigos solo tienen que abrirlo con doble clic (Chrome o Edge). No instala nada.
    python movil/empaquetar_pc.py
"""
import base64
import re
from pathlib import Path

AQUI = Path(__file__).resolve().parent
WWW = AQUI / "app" / "src" / "main" / "assets" / "www"
SALIDA = AQUI.parent / "dist" / "JARVIS-para-amigos.html"
ICONO = AQUI.parent / "web" / "jarvis.png"


def main():
    html = (WWW / "index.html").read_text(encoding="utf-8")
    css = (WWW / "estilo.css").read_text(encoding="utf-8")
    js = (WWW / "app.js").read_text(encoding="utf-8")
    # evita que un "</script>" dentro del código cierre la etiqueta antes de tiempo
    js = js.replace("</script", "<\\/script")

    html = html.replace('<link rel="stylesheet" href="estilo.css">', f"<style>\n{css}\n</style>")
    html = html.replace('<script src="app.js"></script>', f"<script>\n{js}\n</script>")
    if ICONO.exists():
        icono = base64.b64encode(ICONO.read_bytes()).decode()
        html = html.replace("<title>", f'<link rel="icon" type="image/png" href="data:image/png;base64,{icono}">\n<title>', 1)
    assert not re.search(r'(src|href)="(estilo\.css|app\.js)"', html), "quedó una referencia externa"

    SALIDA.parent.mkdir(exist_ok=True)
    SALIDA.write_text(html, encoding="utf-8")
    print(f"Listo: {SALIDA} ({SALIDA.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
