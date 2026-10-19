"""Modelos 3D: leer STL/3MF/OBJ/STEP, separarlos en piezas (como el árbol de Fusion) y exportarlos.

- 3MF: cada objeto es un grupo y sus componentes son las piezas (Fusion y Bambu guardan los nombres).
- STL/OBJ: cada cuerpo suelto (malla conectada) es una pieza, como «Dividir en objetos» de Bambu Studio.
- STEP: se convierte con la línea de comandos de Bambu Studio.
Las mallas son listas planas de floats (9 por triángulo) en milímetros y en coordenadas del modelo.
"""
import json
import re
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from array import array
from dataclasses import dataclass, field
from pathlib import Path

MAX_PIEZAS = 200  # más cuerpos que esto suele ser texto o ruido: el resto se junta en una pieza


@dataclass
class Pieza:
    id: str
    nombre: str
    grupo: str
    tri: array  # 9 floats por triángulo

    @property
    def triangulos(self) -> int:
        return len(self.tri) // 9

    def caja(self):
        xs, ys, zs = self.tri[0::3], self.tri[1::3], self.tri[2::3]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def medidas(self) -> tuple[float, float, float]:
        a, b = self.caja()
        return b[0] - a[0], b[1] - a[1], b[2] - a[2]


@dataclass
class Modelo:
    ruta: Path
    grupos: dict[str, str] = field(default_factory=dict)  # id -> nombre
    piezas: list[Pieza] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def de_grupo(self, g: str) -> list[Pieza]:
        return [p for p in self.piezas if p.grupo == g]

    def resumen(self, maximo=40) -> str:
        lineas = []
        for g, nombre in self.grupos.items():
            ps = self.de_grupo(g)
            if len(ps) == 1:
                lineas.append(f"- {ps[0].id} «{ps[0].nombre}» {_mm(ps[0].medidas())}")
            else:
                lineas.append(f"- {g} «{nombre}» ({len(ps)} piezas): " + ", ".join(f"{p.id} «{p.nombre}» {_mm(p.medidas())}" for p in ps[:12])
                              + (" …" if len(ps) > 12 else ""))
            if len(lineas) >= maximo:
                lineas.append(f"(… {len(self.grupos) - maximo} grupos más)")
                break
        return "\n".join(lineas)


def _mm(m) -> str:
    return f"{m[0]:.0f}×{m[1]:.0f}×{m[2]:.0f} mm"


# ───────────────────────── lectura ─────────────────────────
def _stl(datos: bytes) -> array:
    a = array("f")
    if len(datos) >= 84 and 84 + 50 * struct.unpack_from("<I", datos, 80)[0] == len(datos):
        for t in struct.iter_unpack("<12fH", datos[84:]):
            a.extend(t[3:12])
        return a
    for x, y, z in re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", datos):
        a.extend((float(x), float(y), float(z)))
    del a[len(a) - len(a) % 9:]
    return a


def _cuerpos(tri: array) -> list[array]:
    """Separa una malla en sus cuerpos sueltos (unión-búsqueda sobre vértices compartidos)."""
    n = len(tri) // 9
    ids: dict = {}
    padre = []

    def raiz(i):
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i

    vert = []
    for k in range(0, len(tri), 3):
        clave = (round(tri[k], 3), round(tri[k + 1], 3), round(tri[k + 2], 3))
        v = ids.get(clave)
        if v is None:
            v = ids[clave] = len(padre)
            padre.append(v)
        vert.append(v)
    for t in range(n):
        a, b, c = raiz(vert[3 * t]), raiz(vert[3 * t + 1]), raiz(vert[3 * t + 2])
        padre[b] = a
        padre[raiz(c)] = a
    por_raiz: dict[int, array] = {}
    for t in range(n):
        por_raiz.setdefault(raiz(vert[3 * t]), array("f")).extend(tri[9 * t:9 * t + 9])
    return sorted(por_raiz.values(), key=len, reverse=True)


def _sin_ns(xml: bytes):
    raiz = ET.fromstring(xml)
    for e in raiz.iter():
        e.tag = e.tag.split("}", 1)[-1]
        e.attrib = {k.split("}", 1)[-1]: v for k, v in e.attrib.items()}
    return raiz


def _matriz(texto: str | None):
    if not texto:
        return None
    m = [float(x) for x in texto.split()]
    return m if len(m) == 12 else None


def _componer(a, b):
    """Primero a, luego b (matrices 3MF 3x4: p' = p·M)."""
    if a is None:
        return b
    if b is None:
        return a
    out = []
    for r in range(4):
        fa = a[3 * r:3 * r + 3]
        for c in range(3):
            v = sum(fa[k] * b[3 * k + c] for k in range(3))
            if r == 3:
                v += b[9 + c]
            out.append(v)
    return out


def _aplicar(tri: array, m) -> array:
    if m is None:
        return tri
    out = array("f", tri)
    for k in range(0, len(tri), 3):
        x, y, z = tri[k], tri[k + 1], tri[k + 2]
        out[k] = x * m[0] + y * m[3] + z * m[6] + m[9]
        out[k + 1] = x * m[1] + y * m[4] + z * m[7] + m[10]
        out[k + 2] = x * m[2] + y * m[5] + z * m[8] + m[11]
    return out


def _3mf(ruta: Path, modelo: Modelo):
    with zipfile.ZipFile(ruta) as z:
        modelos = {"/" + n: _sin_ns(z.read(n)) for n in z.namelist() if n.lower().endswith(".model")}
        cfg = z.read("Metadata/model_settings.config") if "Metadata/model_settings.config" in z.namelist() else b""
    principal = next((k for k in modelos if k.lower().endswith("3d/3dmodel.model")), next(iter(modelos)))
    objetos = {}  # (archivo, id) -> elemento object
    for archivo, raiz in modelos.items():
        for o in raiz.iter("object"):
            objetos[(archivo, o.get("id"))] = o
    # nombres y tipos de pieza que guarda Bambu Studio (los modificadores no se imprimen)
    nombres_obj, partes_cfg = {}, {}
    if cfg:
        c = _sin_ns(cfg)
        for o in c.iter("object"):
            meta = {m.get("key"): m.get("value") for m in o.findall("metadata")}
            nombres_obj[o.get("id")] = meta.get("name")
            partes_cfg[o.get("id")] = [({m.get("key"): m.get("value") for m in p.findall("metadata")}.get("name"), p.get("subtype", "normal_part"))
                                       for p in o.findall("part")]

    def mallas(archivo, oid, m, prof=0):
        """[(nombre, tri)] de un objeto, siguiendo componentes."""
        o = objetos.get((archivo, oid))
        if o is None or prof > 8:
            return []
        malla = o.find("mesh")
        if malla is not None:
            vs = [(float(v.get("x")), float(v.get("y")), float(v.get("z"))) for v in malla.iter("vertex")]
            tri = array("f")
            for t in malla.iter("triangle"):
                for i in (t.get("v1"), t.get("v2"), t.get("v3")):
                    tri.extend(vs[int(i)])
            return [(o.get("name"), _aplicar(tri, m))]
        out = []
        for comp in o.iter("component"):
            destino = comp.get("path") or archivo
            out += mallas(destino if destino.startswith("/") else "/" + destino, comp.get("objectid"),
                          _componer(_matriz(comp.get("transform")), m), prof + 1)
        return out

    raiz = modelos[principal]
    n = 0
    for gi, item in enumerate(raiz.iter("item")):
        oid = item.get("objectid")
        o = objetos.get((principal, oid))
        if o is not None and o.get("type") not in (None, "model"):
            continue
        g = f"g{gi}"
        nombre_g = nombres_obj.get(oid) or (o.get("name") if o is not None else None) or f"Objeto {gi + 1}"
        partes = mallas(principal, oid, _matriz(item.get("transform")))
        tipos = partes_cfg.get(oid) or []
        piezas_g = []
        for k, (nombre, tri) in enumerate(partes):
            nombre_cfg, subtipo = tipos[k] if k < len(tipos) else (None, "normal_part")
            if subtipo != "normal_part" or not len(tri):
                continue
            piezas_g.append(Pieza(f"p{n}", nombre_cfg or nombre or f"{nombre_g} · {k + 1}", g, tri))
            n += 1
        if piezas_g:
            modelo.grupos[g] = nombre_g
            modelo.piezas += piezas_g


def _obj(texto: str, modelo: Modelo):
    vs, actual, grupos = [], "Objeto", {}
    for linea in texto.splitlines():
        p = linea.split()
        if not p:
            continue
        if p[0] == "v":
            vs.append(tuple(float(x) for x in p[1:4]))
        elif p[0] in ("o", "g") and len(p) > 1:
            actual = " ".join(p[1:])
        elif p[0] == "f":
            idx = [int(x.split("/")[0]) for x in p[1:]]
            idx = [i - 1 if i > 0 else len(vs) + i for i in idx]
            tri = grupos.setdefault(actual, array("f"))
            for k in range(1, len(idx) - 1):
                for i in (idx[0], idx[k], idx[k + 1]):
                    tri.extend(vs[i])
    for nombre, tri in grupos.items():
        _agregar_cuerpos(modelo, nombre, tri)


def _agregar_cuerpos(modelo: Modelo, nombre: str, tri: array):
    g = f"g{len(modelo.grupos)}"
    cuerpos = _cuerpos(tri)
    if len(cuerpos) > MAX_PIEZAS:
        resto = array("f")
        for c in cuerpos[MAX_PIEZAS - 1:]:
            resto.extend(c)
        cuerpos = cuerpos[:MAX_PIEZAS - 1] + [resto]
        modelo.avisos.append(f"«{nombre}» tiene muchísimos cuerpos sueltos: junté los más pequeños en uno.")
    modelo.grupos[g] = nombre
    for k, c in enumerate(cuerpos):
        modelo.piezas.append(Pieza(f"p{len(modelo.piezas)}", nombre if len(cuerpos) == 1 else f"Cuerpo {k + 1}", g, c))


def _step(ruta: Path, modelo: Modelo, exe: Path):
    with tempfile.TemporaryDirectory() as d:
        r = subprocess.run([str(exe), "--export-stls", d, str(ruta)], capture_output=True, timeout=300)
        stls = sorted(Path(d).rglob("*.stl"))
        if not stls:
            raise RuntimeError("Bambu Studio no pudo convertir el STEP: " + r.stdout.decode(errors="ignore")[-200:])
        for f in stls:
            _agregar_cuerpos(modelo, f.stem, _stl(f.read_bytes()))


def cargar(ruta: Path, exe: Path | None = None) -> Modelo:
    modelo = Modelo(ruta)
    ext = ruta.suffix.lower()
    if ext == ".3mf":
        _3mf(ruta, modelo)
    elif ext == ".stl":
        _agregar_cuerpos(modelo, ruta.stem, _stl(ruta.read_bytes()))
    elif ext == ".obj":
        _obj(ruta.read_text(errors="ignore"), modelo)
    elif ext in (".step", ".stp"):
        _step(ruta, modelo, exe)
    else:
        raise ValueError(f"No sé leer archivos {ext}")
    if not modelo.piezas:
        raise ValueError("El archivo no tiene geometría imprimible.")
    _corregir_unidades(modelo)
    # restos de exportación sin volumen (líneas o láminas de 0 mm): no se pueden imprimir
    sanas = [p for p in modelo.piezas if sorted(p.medidas())[1] >= 0.3]
    if sanas and len(sanas) < len(modelo.piezas):
        modelo.avisos.append(f"Ignoré {len(modelo.piezas) - len(sanas)} cuerpo(s) sin volumen.")
        modelo.piezas = sanas
        modelo.grupos = {g: n for g, n in modelo.grupos.items() if modelo.de_grupo(g)}
    return modelo


def _corregir_unidades(modelo: Modelo):
    """Muchos programas exportan en metros o pulgadas; Bambu trabaja en milímetros."""
    mayor = max(max(p.medidas()) for p in modelo.piezas)
    factor = 1000 if mayor < 1 else (25.4 if mayor < 8 else 1)
    if factor == 1:
        return
    for p in modelo.piezas:
        for k in range(len(p.tri)):
            p.tri[k] *= factor
    modelo.avisos.append(f"Estaba en {'metros' if factor == 1000 else 'pulgadas'}: lo pasé a milímetros (×{factor:g}).")


# ───────────────────────── exportar ─────────────────────────
def girar(tri: array, m: list[float]) -> array:
    """Aplica la parte de giro (3x3, convención 3MF p' = p·M) de una matriz."""
    return _aplicar(tri, list(m[:9]) + [0.0, 0.0, 0.0])


def guardar_stl(tri: array, ruta: Path):
    with open(ruta, "wb") as f:
        f.write(b"JARVIS".ljust(80, b" ") + struct.pack("<I", len(tri) // 9))
        cero = struct.pack("<3f", 0, 0, 0)
        for t in range(0, len(tri), 9):
            f.write(cero + struct.pack("<9f", *tri[t:t + 9]) + b"\0\0")


def guardar_glb(modelo: Modelo, ruta: Path, max_triangulos=600_000):
    """GLB (glTF binario) con un nodo por grupo y por pieza; el visor web lo carga con three.js.
    Las mallas enormes se aligeran solo para verlas (no para imprimir)."""
    total = sum(p.triangulos for p in modelo.piezas)
    paso = max(1, -(-total // max_triangulos))
    if paso > 1:
        modelo.avisos.append(f"El modelo tiene {total:,} triángulos: el visor muestra una versión aligerada.".replace(",", "."))
    binario = bytearray()
    vistas, accesores, mallas, nodos = [], [], [], [{"name": modelo.ruta.stem, "children": []}]
    for g, nombre in modelo.grupos.items():
        ng = {"name": nombre, "extras": {"id": g, "nombre": nombre}, "children": []}
        nodos[0]["children"].append(len(nodos))
        nodos.append(ng)
        for p in modelo.de_grupo(g):
            tri = p.tri if paso == 1 else array("f", (v for t in range(0, len(p.tri), 9 * paso) for v in p.tri[t:t + 9]))
            datos = tri.tobytes()
            (x0, y0, z0), (x1, y1, z1) = Pieza("", "", "", tri).caja()
            vistas.append({"buffer": 0, "byteOffset": len(binario), "byteLength": len(datos), "target": 34962})
            binario += datos
            accesores.append({"bufferView": len(vistas) - 1, "componentType": 5126, "count": len(tri) // 3, "type": "VEC3",
                              "min": [x0, y0, z0], "max": [x1, y1, z1]})
            mallas.append({"name": p.nombre, "primitives": [{"attributes": {"POSITION": len(accesores) - 1}}]})
            ng["children"].append(len(nodos))
            nodos.append({"name": p.nombre, "mesh": len(mallas) - 1, "extras": {"id": p.id, "nombre": p.nombre, "grupo": g, "medidas": [round(x, 1) for x in p.medidas()]}})
    doc = {"asset": {"version": "2.0", "generator": "JARVIS", "extras": {"ruta": str(modelo.ruta)}}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": nodos,
           "meshes": mallas, "accessors": accesores, "bufferViews": vistas, "buffers": [{"byteLength": len(binario)}]}
    js = json.dumps(doc, ensure_ascii=False).encode()
    js += b" " * (-len(js) % 4)
    binario += b"\0" * (-len(binario) % 4)
    with open(ruta, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(binario)))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(binario), 0x004E4942) + bytes(binario))
