"""Pruebas del calendario, sin gastar IAs.
Ejecutar: python -m tests.test_calendario
"""
import tempfile
from datetime import date, timedelta
from pathlib import Path

from jarvis import calendario as c

LUNES = date(2026, 11, 2)


def main():
    c.ARCHIVO = Path(tempfile.mkdtemp()) / "calendario.json"
    assert c.dia("hoy", LUNES) == LUNES and c.dia("Mañana", LUNES) == date(2026, 11, 3) and c.dia("pasado mañana", LUNES) == date(2026, 11, 4)
    assert c.dia("el viernes", LUNES) == date(2026, 11, 6) and c.dia("miércoles", LUNES) == date(2026, 11, 4)
    assert c.dia("lunes", LUNES) == LUNES and c.dia("próximo lunes", LUNES) == date(2026, 11, 9)
    assert c.dia("2026-12-24", LUNES) == date(2026, 12, 24) and c.dia("2026-13-40", LUNES) is None and c.dia("cuando pueda", LUNES) is None
    print("✓ entiende los días")

    assert c.agregar("Comprar filamento", "martes", hoy=LUNES).startswith("Anotado")
    assert "No entendí la hora" in c.agregar("x", hora="tarde", hoy=LUNES) and "No entendí el día" in c.agregar("x", "nunca", hoy=LUNES)
    assert "No entendí cada" in c.agregar("x", repetir="a veces", hoy=LUNES) and "qué anotar" in c.agregar(" ", hoy=LUNES)
    c.agregar("Repasar inglés", "lunes", repetir="entre semana", hoy=LUNES)
    c.agregar("Dentista", "martes", "15:00", hoy=LUNES)
    martes = date(2026, 11, 3)
    assert [i["texto"] for i in c.del_dia(martes)] == ["Dentista", "Comprar filamento", "Repasar inglés"], "lo que tiene hora va primero"
    assert [i["texto"] for i in c.del_dia(LUNES)] == ["Repasar inglés"]
    assert not c.del_dia(date(2026, 11, 7)), "entre semana no cae en sábado"
    assert not c.del_dia(LUNES - timedelta(7)), "lo repetido no aparece antes de empezar"
    print("✓ guarda de una vez y repetidas")

    hoy = date.today().isoformat()
    c.agregar("Sacar la basura", hoy, repetir="todos")
    assert c.marcar("basura", hoy).startswith("Listo")
    manana = (date.today() + timedelta(1)).isoformat()
    assert [i["hecha"] for i in c.del_dia(date.today()) if i["texto"] == "Sacar la basura"] == [True]
    assert [i["hecha"] for i in c.del_dia(date.fromisoformat(manana)) if i["texto"] == "Sacar la basura"] == [False], "hecha solo ese día"
    assert c.marcar("basura", hoy, False).startswith("Listo")
    assert "No encontré" in c.marcar("gimnasio", hoy)
    print("✓ marca hecho por día")

    assert "manana" not in c.quitar("basura", manana).lower() and not any(i["texto"] == "Sacar la basura" for i in c.del_dia(date.fromisoformat(manana)))
    assert any(i["texto"] == "Sacar la basura" for i in c.del_dia(date.today())), "saltar un día no borra la serie"
    assert c.quitar("basura").startswith("Quité")
    c.agregar("Llamar a mamá", "lunes", hoy=LUNES)
    c.agregar("Llamar al banco", "martes", hoy=LUNES)
    assert "¿cuál quito?" in c.quitar("llamar"), "si hay duda no borra"
    assert len([i for i in c._todas() if "Llamar" in i["texto"]]) == 2
    print("✓ quita sin borrar lo que no era")

    s = c.semana("2026-11-05")
    assert s["desde"] == "2026-11-02" and len(s["dias"]) == 7 and s["dias"][1]["nombre"] == "martes" and s["dias"][6]["fecha"] == "2026-11-08"
    assert s["titulo"] == "2 de noviembre al 8 de noviembre"
    assert "Dentista" in c.ver("semana", LUNES) and "15:00" in c.ver("martes", LUNES) and "No tienes nada" in c.ver("sábado", LUNES)
    assert "Dentista" not in c.ver("próxima semana", LUNES) and "No entendí" in c.ver("cuando pueda", LUNES)
    print("✓ muestra el día y la semana")

    c._guardar([{"id": str(n), "texto": "vieja", "fecha": "2020-01-01", "hechas": []} for n in range(c.MAXIMO)])
    assert c.agregar("Nueva", hoy=LUNES).startswith("Anotado") and len(c._todas()) == 1, "lo pasado deja espacio"
    print("✓ lo pasado no llena el calendario")


if __name__ == "__main__":
    main()
