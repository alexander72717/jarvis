"""Pruebas de la parte de impresión 3D que no necesita Bambu Studio: colores y vista previa.
Ejecutar: .venv\\Scripts\\python -m tests.test_impresion3d
"""
import struct
import tempfile
from pathlib import Path

from jarvis import impresion3d as i


def cubo_stl(binario: bool) -> bytes:
    v = [(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0), (0, 0, 10), (20, 0, 10), (20, 20, 10), (0, 20, 10)]
    caras = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    if binario:
        cuerpo = b"".join(struct.pack("<12fH", 0, 0, 0, *v[a], *v[b], *v[c], 0) for a, b, c in caras)
        return b"\0" * 80 + struct.pack("<I", len(caras)) + cuerpo
    t = "solid cubo\n" + "".join("facet normal 0 0 0\nouter loop\n" + "".join(f"vertex {x} {y} {z}\n" for x, y, z in (v[a], v[b], v[c]))
                                 + "endloop\nendfacet\n" for a, b, c in caras)
    return (t + "endsolid cubo\n").encode()


def main():
    assert i.color_pedido("rojo")[0] == "rojo"
    assert i.color_pedido("Azul Claro")[0] == "azul claro"
    assert i.color_pedido("marrón")[0] == "café"
    assert i.color_pedido("#ff0000")[0] == "rojo"
    assert i.color_pedido("transparente") is None
    assert i.nombre_color((250, 250, 250)) == "blanco" and i.nombre_color((15, 15, 15)) == "negro"
    assert i.nombre_color((255, 100, 20)) == "naranja"
    print("✓ colores por nombre, sinónimo y #hex")

    with tempfile.TemporaryDirectory() as d:
        i.PREVIAS = Path(d)
        for binario in (True, False):
            ruta = Path(d) / f"cubo_{binario}.stl"
            ruta.write_bytes(cubo_stl(binario))
            png, medidas = i.previsualizar(ruta)
            assert png and png.stat().st_size > 1000, medidas
            assert medidas.startswith("20 × 20 × 10 mm, 12 triángulos"), medidas
    print("✓ vista previa de STL binario y de texto, con medidas")
    print("\nTodas las pruebas pasaron.")


if __name__ == "__main__":
    main()
