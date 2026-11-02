"""Las habilidades se leen del disco y siempre piden confirmación.
Ejecutar: python -m tests.test_habilidades
"""
import tempfile
from pathlib import Path

from jarvis import habilidades, herramientas


def main():
    assert herramientas.HERRAMIENTAS["usar_habilidad"]["peligrosa"]
    with tempfile.TemporaryDirectory() as d:
        habilidades.CARPETA = Path(d)
        (Path(d) / "prender_x.ps1").write_text("# Prende X\nStart-Process x\n", encoding="utf-8")
        (Path(d) / "nota.txt").write_text("no es un script", encoding="utf-8")
        assert list(habilidades.todas()) == ["prender_x"] and habilidades.todas()["prender_x"]["desc"] == "Prende X"
    print("habilidades: OK")


if __name__ == "__main__":
    main()
