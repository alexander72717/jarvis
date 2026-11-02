"""Piezas desde una idea: limpieza del código y conversión real con OpenSCAD si está instalado.
Ejecutar: python -m tests.test_piezas
"""
import tempfile
from pathlib import Path

from jarvis import herramientas as h
from jarvis import piezas as p


def main():
    assert p.limpiar("```openscad\ncube(10);\n```") == "cube(10);"
    assert p.limpiar("cube(10);") == "cube(10);"
    assert p.limpiar("// no uses import aquí\ncube(10);").endswith("cube(10);")
    for malo in ('import("C:/secreto.stl");', "include <otro.scad>", "use <x.scad>", 'surface("a.dat");', ""):
        try:
            p.limpiar(malo)
        except ValueError:
            continue
        raise AssertionError(malo)
    assert not h.HERRAMIENTAS["crear_pieza"]["peligrosa"] and "No imprime" in h.HERRAMIENTAS["crear_pieza"]["desc"]
    print("✓ acepta el código y rechaza lo que lee archivos")

    if p.ejecutable():
        destino = Path(tempfile.mkdtemp()) / "cubo.stl"
        assert p.compilar("cube([20, 20, 5]);", destino) is None and destino.stat().st_size > 100
        assert p.compilar("esto no compila(", destino)
        print("✓ OpenSCAD genera el STL y devuelve sus errores")
    print("\nTodas las pruebas pasaron.")


if __name__ == "__main__":
    main()
