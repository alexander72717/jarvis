"""Laminado con la línea de comandos de Bambu Studio: perfiles, ajustes, organizar platos y laminar.

Bambu Studio CLI hace lo difícil: orienta cada pieza (--orient), las acomoda en la cama (--arrange) y, si no caben,
crea más platos solo. Aquí solo se preparan los perfiles (aplanando la herencia de los perfiles del sistema)
y se lee el resultado: qué pieza quedó en cada plato, cuánto tarda y cuántos gramos usa.
"""
import json
import os
import re
import subprocess
import zipfile
from functools import lru_cache
from pathlib import Path

EXE = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Bambu Studio" / "bambu-studio.exe"
BBL = EXE.parent / "resources" / "profiles" / "BBL"

PLACA = "Textured PEI Plate"  # la de tus impresoras; la CLI por defecto usa «Cool Plate»
# Perfiles listos; los ajustes que diga el usuario se aplican encima.
PERFILES = {
    "basico": {},
    "resistente": {"wall_loops": "4", "sparse_infill_density": "40%", "sparse_infill_pattern": "gyroid",
                   "top_shell_layers": "5", "bottom_shell_layers": "4"},
    "rapido": {"layer_height": "0.28", "wall_loops": "2", "sparse_infill_density": "10%", "sparse_infill_pattern": "grid"},
    "detalle": {"layer_height": "0.12", "wall_loops": "3", "sparse_infill_density": "15%"},
}
PATRONES = {"gyroid": "gyroid", "giroide": "gyroid", "rejilla": "grid", "grid": "grid", "cubico": "cubic", "cúbico": "cubic",
            "panal": "honeycomb", "honeycomb": "honeycomb", "lineas": "line", "líneas": "line", "triangulos": "triangles",
            "triángulos": "triangles", "rayo": "lightning", "lightning": "lightning", "concentrico": "concentric"}
SOPORTES = {"no": None, "ninguno": None, "auto": "tree(auto)", "arbol": "tree(auto)", "árbol": "tree(auto)",
            "normal": "normal(auto)", "cama": "tree(auto)"}


def ajustes(perfil="basico", paredes=None, relleno=None, patron=None, soportes=None, altura_capa=None, borde=None) -> dict:
    """Traduce lo que pide el usuario a claves de Bambu Studio (validando rangos)."""
    clave = (perfil or "basico").lower().replace("á", "a").replace("í", "i").replace("é", "e")
    if clave not in PERFILES:
        raise ValueError(f"No conozco el perfil «{perfil}». Opciones: {', '.join(PERFILES)}.")
    d = dict(PERFILES[clave])
    if paredes not in (None, ""):
        n = int(paredes)
        if not 1 <= n <= 20:
            raise ValueError("Las paredes van de 1 a 20.")
        d["wall_loops"] = str(n)
    if relleno not in (None, ""):
        n = float(str(relleno).rstrip("% "))
        if not 0 <= n <= 100:
            raise ValueError("El relleno va de 0 a 100 %.")
        d["sparse_infill_density"] = f"{n:g}%"
    if patron:
        p = PATRONES.get(str(patron).lower())
        if not p:
            raise ValueError(f"Patrón de relleno desconocido. Opciones: {', '.join(sorted(set(PATRONES)))}.")
        d["sparse_infill_pattern"] = p
    if soportes not in (None, ""):
        s = str(soportes).lower()
        if s in ("si", "sí", "true", "1"):
            s = "auto"
        if s not in SOPORTES:
            raise ValueError("Soportes: no, auto (árbol), normal o cama (solo apoyados en la cama).")
        d["enable_support"] = "0" if SOPORTES[s] is None else "1"
        if SOPORTES[s]:
            d["support_type"] = SOPORTES[s]
            d["support_on_build_plate_only"] = "1" if s == "cama" else "0"
    if altura_capa not in (None, ""):
        h = float(altura_capa)
        if not 0.06 <= h <= 0.32:
            raise ValueError("La altura de capa va de 0,06 a 0,32 mm con boquilla de 0,4.")
        d["layer_height"] = f"{h:g}"
    if borde not in (None, ""):
        b = str(borde).lower()
        d["brim_type"] = "no_brim" if b in ("no", "ninguno", "false", "0") else ("outer_only" if b in ("si", "sí", "true", "1", "exterior") else "auto_brim")
    return d


@lru_cache(maxsize=1)
def _indice() -> dict[str, Path]:
    out = {}
    for f in BBL.rglob("*.json"):
        try:
            nombre = json.loads(f.read_text(encoding="utf-8")).get("name")
        except (OSError, ValueError):
            continue
        if nombre:
            out[nombre] = f
    return out


def _plano(nombre: str) -> dict:
    ruta = _indice().get(nombre)
    if not ruta:
        raise RuntimeError(f"No encuentro el perfil «{nombre}» en Bambu Studio.")
    d = json.loads(ruta.read_text(encoding="utf-8"))
    if d.get("inherits"):
        base = _plano(d["inherits"])
        base.update(d)
        d = base
    d.pop("inherits", None)
    return d


def _compatible(prefijos: tuple, maquina: str) -> str:
    for prefijo in prefijos:
        for nombre, ruta in _indice().items():
            if nombre.startswith(prefijo) and "@BBL" in nombre:
                try:
                    d = json.loads(ruta.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                if maquina in d.get("compatible_printers", []):
                    return nombre
    raise RuntimeError(f"No encuentro un perfil «{prefijos[0]}» compatible con {maquina}.")


def perfiles(modelo_impresora: str, material="PLA") -> tuple[str, str, str]:
    """('Bambu Lab A1 0.4 nozzle', '0.20mm Standard @BBL A1', 'Bambu PLA Basic @BBL A1')"""
    maquina = f"Bambu Lab {modelo_impresora} 0.4 nozzle"
    if maquina not in _indice():
        raise RuntimeError(f"No tengo perfiles para la impresora «{modelo_impresora}».")
    m = (material or "PLA").upper()
    proceso = _compatible(("0.20mm Standard",), maquina)
    filamento = _compatible((f"Bambu {m} Basic", f"Bambu {m}", f"Generic {m}"), maquina)
    return maquina, proceso, filamento


def escribir_perfiles(carpeta: Path, modelo_impresora: str, cambios: dict, material="PLA") -> list[Path]:
    maquina, proceso, filamento = perfiles(modelo_impresora, material)
    rutas = []
    for nombre, cambios_k, archivo in ((maquina, {}, "maquina.json"), (proceso, {"curr_bed_type": PLACA, **cambios}, "proceso.json"),
                                       (filamento, {}, "filamento.json")):
        d = _plano(nombre)
        d.update(cambios_k)
        if archivo == "proceso.json":
            # con el nombre del sistema, la ventana de Bambu Studio recarga el perfil original y descarta los cambios
            d["name"] = f"JARVIS {nombre}"
        ruta = carpeta / archivo
        ruta.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        rutas.append(ruta)
    return rutas


def _cli(args: list[str], carpeta: Path, timeout=1800) -> str:
    r = subprocess.run([str(EXE), *args], capture_output=True, cwd=carpeta, timeout=timeout)
    salida = (r.stdout + r.stderr).decode("utf-8", errors="ignore")
    if r.returncode != 0:
        lineas = [x for x in salida.splitlines() if x.strip() and not x.startswith(("orientation", "best:"))]
        raise RuntimeError("Bambu Studio no pudo laminar: " + " ".join(lineas[-3:])[:400])
    return salida


def _base(perfiles_json: list[Path], orientar: bool) -> list[str]:
    maquina, proceso, filamento = perfiles_json
    return ["--arrange", "1", "--orient", "1" if orientar else "0", "--allow-rotations",
            "--load-settings", f"{maquina};{proceso}", "--load-filaments", str(filamento)]


def organizar(unidades: dict[str, tuple[Path, int]], carpeta: Path, perfiles_json, orientar=True):
    """unidades: {'u0': (stl, copias)} -> ([{'u0': 3, 'u1': 1}, ...] una entrada por plato, {'u0': giro 3x3}).
    El giro es la orientación que eligió Bambu: hay que aplicarlo a la malla, porque Bambu Studio (la ventana)
    no puede abrir archivos hechos con --orient."""
    claves = list(unidades)
    args = _base(perfiles_json, orientar) + ["--clone-objects", ",".join(str(unidades[k][1]) for k in claves),
                                             "--export-3mf", "organizado.3mf", "--outputdir", str(carpeta)]
    _cli(args + [str(unidades[k][0]) for k in claves], carpeta)
    with zipfile.ZipFile(carpeta / "organizado.3mf") as z:
        cfg = z.read("Metadata/model_settings.config").decode("utf-8", errors="ignore")
        modelo = z.read("3D/3dmodel.model").decode("utf-8", errors="ignore")
    nombres = {oid: n for oid, n in re.findall(r'<object id="(\d+)">\s*<metadata key="name" value="([^"]*)"', cfg)}
    giros = {}
    for oid, transf in re.findall(r'<object id="(\d+)"[^>]*>\s*<components>\s*<component [^>]*transform="([^"]+)"', modelo):
        m = re.match(r"(u\d+)\.stl", nombres.get(oid, ""))
        if m and m.group(1) not in giros:
            giros[m.group(1)] = [float(x) for x in transf.split()[:9]]
    platos = []
    for bloque in re.findall(r"<plate>(.*?)</plate>", cfg, re.S):
        cuenta: dict[str, int] = {}
        for oid in re.findall(r'<metadata key="object_id" value="(\d+)"', bloque):
            m = re.match(r"(u\d+)\.stl", nombres.get(oid, ""))
            if m:
                cuenta[m.group(1)] = cuenta.get(m.group(1), 0) + 1
        if cuenta:
            platos.append(cuenta)
    return platos, giros


def laminar(unidades: dict[str, tuple[Path, int]], carpeta: Path, perfiles_json, salida: str, orientar=False) -> list[dict]:
    """Lamina las unidades (deben caber en un plato; si no, salen varios). Devuelve info por plato.
    Sin --orient (ya vienen giradas) para que Bambu Studio pueda abrir el resultado y enviarlo."""
    claves = [k for k, (_, n) in unidades.items() if n > 0]
    args = _base(perfiles_json, orientar) + ["--clone-objects", ",".join(str(unidades[k][1]) for k in claves),
                                             "--slice", "0", "--export-3mf", salida, "--outputdir", str(carpeta)]
    _cli(args + [str(unidades[k][0]) for k in claves], carpeta)
    with zipfile.ZipFile(carpeta / salida) as z:
        info = z.read("Metadata/slice_info.config").decode("utf-8", errors="ignore")
    out = []
    for bloque in re.findall(r"<plate>(.*?)</plate>", info, re.S):
        tiempo = re.search(r'key="prediction" value="(\d+)"', bloque)
        peso = re.search(r'key="weight" value="([\d.]+)"', bloque)
        out.append({"segundos": int(tiempo.group(1)) if tiempo else 0, "gramos": float(peso.group(1)) if peso else 0.0})
    return out
