"""Pruebas de la impresión 3D que no necesitan Bambu Studio: piezas, unidades, visor, ajustes y colores.
Ejecutar: .venv\\Scripts\\python -m tests.test_impresion3d
"""
import json
import struct
import tempfile
from array import array
from pathlib import Path

from jarvis import impresion3d as i, laminado, modelos3d as m


def cubo(x0, lado, alto) -> list[float]:
    v = [(x0, 0, 0), (x0 + lado, 0, 0), (x0 + lado, lado, 0), (x0, lado, 0),
         (x0, 0, alto), (x0 + lado, 0, alto), (x0 + lado, lado, alto), (x0, lado, alto)]
    caras = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return [c for t in caras for k in t for c in v[k]]


def main():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        # dos cubos sueltos en un mismo STL = dos piezas, como «Dividir en objetos» de Bambu
        stl = d / "dos.stl"
        m.guardar_stl(array("f", cubo(0, 20, 10) + cubo(40, 10, 5)), stl)
        mo = m.cargar(stl)
        assert len(mo.piezas) == 2 and len(mo.grupos) == 1, mo.resumen()
        assert [round(x) for x in mo.piezas[0].medidas()] == [20, 20, 10]
        print("✓ un STL con dos cuerpos se separa en dos piezas")

        # en metros se pasa a milímetros
        metros = d / "metros.stl"
        m.guardar_stl(array("f", [c / 1000 for c in cubo(0, 20, 10)]), metros)
        mo_m = m.cargar(metros)
        assert round(mo_m.piezas[0].medidas()[0]) == 20 and "metros" in mo_m.avisos[0]
        print("✓ los modelos en metros se pasan a milímetros")

        # el GLB del visor es válido y trae los ids y nombres de cada pieza
        glb = d / "visor.glb"
        m.guardar_glb(mo, glb)
        datos = glb.read_bytes()
        magia, version, largo = struct.unpack_from("<III", datos)
        assert magia == 0x46546C67 and version == 2 and largo == len(datos)
        n_json = struct.unpack_from("<I", datos, 12)[0]
        doc = json.loads(datos[20:20 + n_json])
        extras = [n["extras"] for n in doc["nodes"] if "mesh" in n]
        assert [e["id"] for e in extras] == ["p0", "p1"] and all(e["nombre"] for e in extras)
        print("✓ el visor recibe un GLB válido con las piezas")

        # elegir piezas y copias
        assert [(p.id, n) for p, n in i._unidades(mo, "todas", 3)] == [("p0", 3), ("p1", 3)]
        assert [(p.id, n) for p, n in i._unidades(mo, "p1:4", 2)] == [("p1", 8)]
        assert [(p.id, n) for p, n in i._unidades(mo, "g0", 1)] == [("p0", 1), ("p1", 1)]
        try:
            i._unidades(mo, "no existe", 1)
            raise AssertionError("debió fallar")
        except ValueError:
            pass
        print("✓ piezas por id, grupo o nombre, con copias")

        # girar con una matriz 3MF (90° alrededor de Z)
        girado = m.girar(array("f", [10, 0, 0] * 3), [0, 1, 0, -1, 0, 0, 0, 0, 1])
        assert [round(x) for x in girado[:3]] == [0, 10, 0]
        print("✓ el giro de orientación se aplica a la malla")

    # ajustes de impresión
    a = laminado.ajustes("resistente", paredes=3, relleno="20%", soportes="arbol", borde="no")
    assert a["wall_loops"] == "3" and a["sparse_infill_density"] == "20%" and a["support_type"] == "tree(auto)" and a["brim_type"] == "no_brim"
    assert laminado.ajustes("basico") == {}
    assert laminado.ajustes(soportes="no")["enable_support"] == "0"
    for malo in ({"paredes": 0}, {"relleno": 150}, {"altura_capa": 0.5}, {"perfil": "turbo"}, {"soportes": "quizas"}):
        try:
            laminado.ajustes(**malo)
            raise AssertionError(f"debió rechazar {malo}")
        except ValueError:
            pass
    print("✓ ajustes de paredes, relleno y soportes, con rangos validados")

    assert i.color_pedido("rojo")[0] == "rojo" and i.color_pedido("Azul Claro")[0] == "azul claro"
    assert i.color_pedido("marrón")[0] == "café" and i.color_pedido("#ff0000")[0] == "rojo" and i.color_pedido("transparente") is None
    assert i.nombre_color((250, 250, 250)) == "blanco" and i.nombre_color((255, 100, 20)) == "naranja"
    assert i._modelo_de("Taller – X1 Carbon") == "X1 Carbon" and i._modelo_de("Casa – A1") == "A1"
    print("✓ colores y modelo de impresora")
    print("\nTodas las pruebas pasaron.")


if __name__ == "__main__":
    main()
