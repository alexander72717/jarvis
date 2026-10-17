"""Notion con su API oficial (gratis): buscar, leer páginas, consultar calendarios/bases y crear notas.

Configuración (una sola vez):
  1. https://www.notion.so/profile/integrations -> «Nueva integración» (interna) -> copia el secreto (ntn_…)
  2. Pégalo en Ajustes de JARVIS como NOTION_TOKEN
  3. En Notion, en cada página o base que JARVIS deba ver: «…» -> Conexiones -> añade tu integración
"""
from datetime import date, datetime, timedelta

import httpx

from . import config
from .herramientas import P, herramienta

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
http = httpx.AsyncClient(timeout=30)


def _cab() -> dict | None:
    token = config.env("NOTION_TOKEN")
    if not token:
        return None
    return {"Authorization": f"Bearer {token}", "Notion-Version": VERSION, "Content-Type": "application/json"}


SIN_TOKEN = ("Notion no está conectado. Crea una integración gratis en notion.so/profile/integrations, pega su secreto "
             "en Ajustes como NOTION_TOKEN y comparte tus páginas con ella (… → Conexiones).")


async def _pedir(metodo: str, ruta: str, cuerpo: dict | None = None) -> dict:
    cab = _cab()
    if not cab:
        raise RuntimeError(SIN_TOKEN)
    r = await http.request(metodo, API + ruta, headers=cab, json=cuerpo)
    if r.status_code == 401:
        raise RuntimeError("Notion rechazó el token (NOTION_TOKEN inválido).")
    if r.status_code == 404:
        raise RuntimeError("Notion no encontró eso o no está compartido con la integración (… → Conexiones).")
    r.raise_for_status()
    return r.json()


def _texto_rico(partes) -> str:
    return "".join(p.get("plain_text", "") for p in partes or [])


def _titulo(obj: dict) -> str:
    if obj.get("object") == "database":
        return _texto_rico(obj.get("title")) or "(base sin título)"
    for prop in (obj.get("properties") or {}).values():
        if prop.get("type") == "title":
            return _texto_rico(prop.get("title")) or "(sin título)"
    return "(sin título)"


def _valor(prop: dict) -> str:
    t = prop.get("type")
    v = prop.get(t)
    if v is None:
        return ""
    if t in ("title", "rich_text"):
        return _texto_rico(v)
    if t == "date":
        return v.get("start", "") + (f" → {v['end']}" if v.get("end") else "")
    if t in ("select", "status"):
        return v.get("name", "")
    if t == "multi_select":
        return ", ".join(x.get("name", "") for x in v)
    if t == "checkbox":
        return "sí" if v else "no"
    if t in ("number", "url", "email", "phone_number"):
        return str(v)
    if t == "people":
        return ", ".join(x.get("name", "") for x in v)
    if t == "formula":
        return str(v.get(v.get("type"), ""))
    if t == "relation":
        return f"{len(v)} relacionados"
    return ""


def _fila(pagina: dict) -> str:
    props = pagina.get("properties") or {}
    detalles = [f"{k}: {_valor(p)}" for k, p in props.items() if p.get("type") != "title" and _valor(p)]
    return f"- {_titulo(pagina)}" + (f" ({'; '.join(detalles)})" if detalles else "")


async def _buscar_id(nombre: str, tipo: str) -> tuple[str, dict]:
    datos = await _pedir("POST", "/search", {"query": nombre, "filter": {"property": "object", "value": tipo}, "page_size": 5})
    res = datos.get("results") or []
    if not res:
        raise RuntimeError(f"No encontré {'la base' if tipo == 'database' else 'la página'} «{nombre}» (¿está compartida con la integración?).")
    return res[0]["id"], res[0]


@herramienta("notion_buscar", "Busca páginas y bases de datos en el Notion del usuario por título.",
             {"consulta": P("string", "Texto a buscar (vacío = lo más reciente)")})
async def notion_buscar(ctx, consulta=""):
    try:
        datos = await _pedir("POST", "/search", {"query": consulta or "", "page_size": 15,
                                                 "sort": {"direction": "descending", "timestamp": "last_edited_time"}})
    except RuntimeError as e:
        return str(e)
    filas = [f"- [{'base' if o['object'] == 'database' else 'página'}] {_titulo(o)} (editado {o.get('last_edited_time', '')[:10]})"
             for o in datos.get("results", [])]
    return "\n".join(filas) or "No encontré nada compartido con la integración."


@herramienta("notion_leer", "Lee el contenido de una página de Notion por su título.",
             {"pagina": P("string", "Título de la página")}, ["pagina"])
async def notion_leer(ctx, pagina):
    try:
        pid, obj = await _buscar_id(pagina, "page")
        lineas, cursor = [], None
        while len(lineas) < 300:
            datos = await _pedir("GET", f"/blocks/{pid}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else ""))
            for b in datos.get("results", []):
                t = b.get("type")
                contenido = b.get(t) or {}
                texto = _texto_rico(contenido.get("rich_text"))
                if t == "to_do":
                    texto = f"[{'x' if contenido.get('checked') else ' '}] {texto}"
                elif t.startswith("heading"):
                    texto = f"## {texto}"
                elif t in ("bulleted_list_item", "numbered_list_item"):
                    texto = f"• {texto}"
                elif t == "child_database":
                    texto = f"(base de datos: {contenido.get('title', '')})"
                elif t == "child_page":
                    texto = f"(subpágina: {contenido.get('title', '')})"
                if texto.strip():
                    lineas.append(texto)
            cursor = datos.get("next_cursor")
            if not datos.get("has_more"):
                break
    except RuntimeError as e:
        return str(e)
    return f"# {_titulo(obj)}\n" + ("\n".join(lineas) or "(página vacía)")


@herramienta("notion_consultar", "Consulta una base de datos o calendario de Notion (clases, tareas, horarios), opcionalmente entre dos fechas.",
             {"base": P("string", "Nombre de la base o calendario"), "desde": P("string", "Fecha inicial AAAA-MM-DD (opcional; 'hoy', 'mañana')"),
              "hasta": P("string", "Fecha final AAAA-MM-DD (opcional)")}, ["base"])
async def notion_consultar(ctx, base, desde=None, hasta=None):
    def fecha(txt):
        if not txt:
            return None
        t = txt.lower().strip()
        hoy = date.today()
        return {"hoy": hoy, "mañana": hoy + timedelta(days=1), "manana": hoy + timedelta(days=1), "ayer": hoy - timedelta(days=1)}.get(t) \
            or datetime.fromisoformat(t[:10]).date()

    try:
        bid, obj = await _buscar_id(base, "database")
        cuerpo = {"page_size": 50}
        props_fecha = [k for k, v in (obj.get("properties") or {}).items() if v.get("type") == "date"]
        d, h = fecha(desde), fecha(hasta)
        if (d or h) and props_fecha:
            d = d or h
            h = h or d
            cuerpo["filter"] = {"and": [{"property": props_fecha[0], "date": {"on_or_after": d.isoformat()}},
                                        {"property": props_fecha[0], "date": {"before": (h + timedelta(days=1)).isoformat()}}]}
            cuerpo["sorts"] = [{"property": props_fecha[0], "direction": "ascending"}]
        datos = await _pedir("POST", f"/databases/{bid}/query", cuerpo)
    except (RuntimeError, ValueError) as e:
        return str(e)
    filas = [_fila(p) for p in datos.get("results", [])]
    rango = f" entre {d} y {h}" if (desde or hasta) and props_fecha else ""
    return f"{_titulo(obj)}{rango}:\n" + ("\n".join(filas) or "(sin resultados)")


@herramienta("notion_crear_nota", "Crea una página nueva en Notion dentro de una página existente.",
             {"titulo": P("string", "Título de la nota"), "contenido": P("string", "Texto (cada línea un párrafo)"),
              "dentro_de": P("string", "Título de la página donde crearla")}, ["titulo", "contenido", "dentro_de"], peligrosa=True)
async def notion_crear_nota(ctx, titulo, contenido, dentro_de):
    try:
        padre, _ = await _buscar_id(dentro_de, "page")
        hijos = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": l[:1900]}}]}}
                 for l in contenido.splitlines() if l.strip()][:90]
        datos = await _pedir("POST", "/pages", {"parent": {"page_id": padre},
                                                 "properties": {"title": {"title": [{"type": "text", "text": {"content": titulo}}]}},
                                                 "children": hijos})
    except RuntimeError as e:
        return str(e)
    return f"Creé «{titulo}» en Notion: {datos.get('url', '')}"


# ======================= lectura sin llave (app de Notion + accesibilidad) =======================
# Para espacios de trabajo donde no se pueden crear integraciones: JARVIS abre la página en la app
# de Notion y lee lo que muestra con UI Automation. Solo lectura. Notion (Electron) debe abrirse con
# --force-renderer-accessibility para exponer el contenido; JARVIS ya lo abre así.

import os  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

EXE = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Notion\Notion.exe")
BANDERA = "--force-renderer-accessibility"


def abrir_app() -> bool:
    if not os.path.exists(EXE):
        return False
    subprocess.Popen([EXE, BANDERA])
    return True


def _ventana():
    import uiautomation as auto

    for w in auto.GetRootControl().GetChildren():
        if not w.ClassName.startswith("Chrome_WidgetWin"):
            continue
        try:
            import psutil

            if psutil.Process(w.ProcessId).name().lower() == "notion.exe":
                return w
        except Exception:  # noqa: BLE001
            continue
    return None


def _nodos(c, prof=0, limite=80) -> int:
    n = 1
    if prof < 45:
        for h in c.GetChildren():
            n += _nodos(h, prof + 1)
            if n > limite:
                break
    return n


def _leer_ventana(v, maximo=9000) -> list[str]:
    lineas, vistos = [], set()

    def recorrer(c, prof=0):
        if prof > 70 or sum(len(x) for x in lineas) > maximo:
            return
        for h in c.GetChildren():
            n = re.sub(r"\s+", " ", (h.Name or "")).strip()
            tipo = h.ControlTypeName
            if n and n not in vistos and tipo in ("HyperlinkControl", "TextControl", "TabItemControl", "HeaderItemControl", "DataItemControl"):
                vistos.add(n)
                # los textos sueltos que ya están dentro de un enlace (tarjeta de calendario) no se repiten
                if tipo == "HyperlinkControl" or len(n) > 2 and not any(n in l for l in lineas[-12:]):
                    lineas.append(("• " if tipo == "HyperlinkControl" else "") + n)
            recorrer(h, prof + 1)

    recorrer(v)
    return lineas


def _ver(pagina: str | None, filtro: str | None) -> str:
    import uiautomation as auto

    with auto.UIAutomationInitializerInThread():
        v = _ventana()
        if v is None:
            if not abrir_app():
                return "No encontré la app de Notion instalada."
            for _ in range(20):
                time.sleep(1)
                v = _ventana()
                if v and _nodos(v) > 60:
                    break
        if v is None:
            return "Notion no abrió a tiempo."
        if _nodos(v) <= 60:
            return ("Notion está abierto pero sin accesibilidad activada (se abrió sin JARVIS). "
                    "Ciérralo y pídeme «abre Notion» para que pueda leerlo.")
        ya_abierta = pagina and pagina.lower().strip() in v.Name.lower()
        if pagina and not ya_abierta:
            v.SetActive()
            time.sleep(0.4)
            auto.SendKeys("{Ctrl}p", waitTime=0.6)  # búsqueda rápida de Notion
            auto.SendKeys(pagina.replace("{", "").replace("}", ""), interval=0.01, waitTime=1.8)
            auto.SendKeys("{Enter}", waitTime=3.0)
            v = _ventana() or v
        titulo = v.Name
        lineas = _leer_ventana(v)
    if filtro:
        f = filtro.lower()
        lineas = [l for l in lineas if f in l.lower()]
        if not lineas:
            return f"En «{titulo}» no aparece nada con «{filtro}» en lo que está visible."
    return f"# {titulo} (leído de la app de Notion)\n" + "\n".join(lineas)[:9000]


@herramienta("notion_ver", "Lee lo que muestra la app de Notion (páginas, calendario de clases, tareas). Sin llave: funciona en "
             "cualquier espacio de trabajo. Opcional: abrir otra página por su título y filtrar líneas (ej. una fecha '18 de octubre').",
             {"pagina": P("string", "Título de la página a abrir (vacío = la que está abierta)"),
              "filtro": P("string", "Solo líneas que contengan este texto, ej. '18 de octubre de 2026' o un nombre")}, solo_pc=True)
async def notion_ver(ctx, pagina=None, filtro=None):
    import asyncio

    return await asyncio.to_thread(_ver, pagina, filtro)
