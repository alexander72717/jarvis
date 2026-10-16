"""Servidor web local: interfaz HUD, WebSocket de conversación y API."""
import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Body, FastAPI, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, herramientas, voz
from .agente import Agente
from .memoria import Memoria
from .proveedores import CATALOGO
from .router import Router, SinProveedores
from .telegram import Telegram

log = logging.getLogger("jarvis")

router = Router()
memoria = Memoria()
agente = Agente(router, memoria)
telegram = Telegram(agente, router)
clientes: set[WebSocket] = set()
SESION_WEB = "web"
# cambia cuando se actualiza la interfaz: las ventanas abiertas se recargan solas al reconectar
VERSION_WEB = str(int(max(f.stat().st_mtime for f in config.WEB.iterdir() if f.is_file())))


async def difundir(evento: dict):
    datos = json.dumps(evento, ensure_ascii=False, default=str)
    for ws in list(clientes):
        try:
            await ws.send_text(datos)
        except Exception:  # noqa: BLE001
            clientes.discard(ws)


agente.difundir = difundir
agente.notificar = telegram.notificar if telegram.activo else None

_cambio_router = asyncio.Event()
router.oyentes.append(lambda: _cambio_router.set())


async def tarea_metricas():
    previo, t_previo = None, 0.0
    while True:
        if clientes:  # sin pantallas abiertas no se mide nada
            m = herramientas.metricas()
            ahora = time.time()
            if previo:
                dt = max(ahora - t_previo, 0.5)
                m["subida"] = (m["red_tx"] - previo["red_tx"]) / dt
                m["bajada"] = (m["red_rx"] - previo["red_rx"]) / dt
            previo, t_previo = m, ahora
            await difundir({"type": "sistema", **m})
        else:
            previo = None
        await asyncio.sleep(3)


async def tarea_router():
    while True:
        try:
            await asyncio.wait_for(_cambio_router.wait(), 5)
        except asyncio.TimeoutError:
            pass
        _cambio_router.clear()
        if clientes:
            await difundir({"type": "ia", **router.estado()})


async def tarea_recordatorios():
    while True:
        for r in memoria.vencidos():
            memoria.completar_recordatorio(r["id"])
            texto = f"Recordatorio: {r['texto']}"
            await difundir({"type": "recordatorio", "texto": texto})
            if telegram.activo:
                await telegram.notificar("⏰ " + texto)
        await asyncio.sleep(3)


@asynccontextmanager
async def vida(app):
    await router.iniciar()
    tareas = [asyncio.create_task(t()) for t in (tarea_metricas, tarea_router, tarea_recordatorios)]
    if telegram.activo:
        tareas.append(asyncio.create_task(telegram.bucle()))
    log.info("JARVIS listo en http://%s:%s", config.HOST, config.PUERTO)
    yield
    for t in tareas:
        t.cancel()
    router.guardar(True)


app = FastAPI(title="JARVIS", lifespan=vida)
app.mount("/static", StaticFiles(directory=config.WEB), name="static")

LOCALES = {"127.0.0.1", "::1", "localhost"}


@app.middleware("http")
async def proteger(request: Request, siguiente):
    """Si JARVIS_CLAVE está definida, quien entre desde otra máquina (LAN, CasaOS) debe conocerla."""
    clave = config.env("JARVIS_CLAVE")
    origen = request.client.host if request.client else ""
    if not clave or origen in LOCALES or request.url.path.startswith("/static"):
        return await siguiente(request)
    if request.query_params.get("clave") == clave:
        resp = await siguiente(request)
        resp.set_cookie("jarvis_clave", clave, max_age=180 * 86400, httponly=True, samesite="strict")
        return resp
    if request.cookies.get("jarvis_clave") == clave:
        return await siguiente(request)
    return HTMLResponse("<body style='background:#02060d;color:#00e5ff;font-family:sans-serif;display:grid;place-items:center;height:100vh'>"
                        "<form><h2>J.A.R.V.I.S. · acceso restringido</h2><input name=clave type=password autofocus placeholder='clave'> <button>Entrar</button></form></body>", 401)


def _ws_permitido(ws: WebSocket) -> bool:
    clave = config.env("JARVIS_CLAVE")
    origen = ws.client.host if ws.client else ""
    return not clave or origen in LOCALES or ws.cookies.get("jarvis_clave") == clave


@app.get("/")
async def inicio():
    return FileResponse(config.WEB / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/ping")
async def ping():
    return {"ok": True}


@app.get("/api/estado")
async def estado():
    hora = datetime.now().hour
    return {
        "nombre": config.nombre_asistente(), "usuario": config.usuario(), "tratamiento": config.tratamiento(),
        "voz": config.voz(), "saludo": "Buenos días" if 5 <= hora < 12 else "Buenas tardes" if hora < 19 else "Buenas noches",
        "ia": router.estado(), "herramientas": len(herramientas.disponibles()), "telegram": telegram.activo,
        "recordatorios": memoria.recordatorios(), "pc": config.ES_WINDOWS, "version": VERSION_WEB,
    }


@app.get("/api/historial")
async def historial():
    return memoria.historial_visible(SESION_WEB)


@app.get("/api/tts")
async def tts(texto: str, voz_id: str | None = None, velocidad: str = "+0%"):
    try:
        audio = await voz.sintetizar(texto, voz_id, velocidad)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, 503)
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/voces")
async def voces():
    try:
        return await voz.voces()
    except Exception:  # noqa: BLE001
        return []


@app.post("/api/stt")
async def stt(audio: UploadFile = File(...)):
    try:
        return {"texto": await router.transcribir(await audio.read(), audio.filename or "audio.webm")}
    except SinProveedores as e:
        return JSONResponse({"error": str(e)}, 503)


@app.post("/api/estrategia/{nombre}")
async def cambiar_estrategia(nombre: str):
    if nombre in ("equilibrio", "calidad", "velocidad"):
        router.estrategia = nombre
        _cambio_router.set()
    return {"estrategia": router.estrategia}


@app.post("/api/recargar")
async def recargar():
    await router.iniciar()
    return router.estado()


EXTRA_EDITABLES = {"CLOUDFLARE_ACCOUNT_ID", "OLLAMA_URL", "TELEGRAM_TOKEN", "TELEGRAM_USUARIOS", "USUARIO_NOMBRE", "USUARIO_TRATAMIENTO",
                   "CIUDAD", "PAIS", "VOZ", "SERVIDOR_HOST", "SERVIDOR_USUARIO", "SERVIDOR_MAC", "JARVIS_NOMBRE"}


def _editables() -> set:
    return {p["llave"] for p in CATALOGO if p.get("llave")} | EXTRA_EDITABLES


@app.get("/api/llaves")
async def ver_llaves():
    """Qué variables están puestas (nunca devuelve el valor de una llave, solo los últimos 4 caracteres)."""
    out = {}
    for k in sorted(_editables()):
        v = config.env(k)
        secreto = k.endswith(("KEY", "TOKEN"))
        out[k] = ("…" + v[-4:] if secreto and v else v)
    return out


@app.post("/api/llaves")
async def poner_llaves(cambios: dict = Body(...)):
    permitidas = _editables()
    ruta = config.RAIZ / ".env"
    lineas = ruta.read_text(encoding="utf-8").splitlines() if ruta.exists() else []
    for k, v in cambios.items():
        if k not in permitidas:
            continue
        v = str(v).strip().replace("\n", "").replace("\r", "")
        nueva = f"{k}={v}"
        for i, l in enumerate(lineas):
            if l.split("=", 1)[0].strip() == k:
                lineas[i] = nueva
                break
        else:
            lineas.append(nueva)
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    await router.iniciar()
    return router.estado()


@app.post("/api/reiniciar_enfriamientos")
async def reiniciar_enfriamientos():
    router.reiniciar_enfriamientos()
    return router.estado()


@app.get("/api/memorias")
async def memorias():
    return memoria.hechos()


@app.delete("/api/memorias/{id_}")
async def borrar_memoria(id_: int):
    return {"ok": memoria.olvidar(id_)}


@app.post("/api/nueva_conversacion")
async def nueva_conversacion():
    memoria.borrar_sesion(SESION_WEB)
    return {"ok": True}


@app.websocket("/ws")
async def ws_conversacion(ws: WebSocket):
    if not _ws_permitido(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    clientes.add(ws)
    pendientes: dict[str, asyncio.Future] = {}
    tarea: asyncio.Task | None = None

    async def emit(ev):
        try:
            await ws.send_text(json.dumps(ev, ensure_ascii=False, default=str))
        except Exception:  # noqa: BLE001
            pass

    async def confirmar(nombre, args):
        clave = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        pendientes[clave] = fut
        await emit({"type": "confirmar", "id": clave, "nombre": nombre, "args": args})
        try:
            return await asyncio.wait_for(fut, 180)
        except asyncio.TimeoutError:
            return False
        finally:
            pendientes.pop(clave, None)

    async def atender(texto, canal, imagenes):
        try:
            await agente.responder(SESION_WEB, texto, emit, confirmar, canal=canal, imagenes=imagenes)
        except asyncio.CancelledError:
            await emit({"type": "fin", "meta": {}, "texto": "", "cancelado": True})
        except Exception as e:  # noqa: BLE001
            log.exception("error respondiendo")
            await emit({"type": "error", "texto": f"Error interno: {e}"})

    await emit({"type": "ia", **router.estado()})
    try:
        while True:
            m = json.loads(await ws.receive_text())
            t = m.get("type")
            if t == "mensaje" and m.get("texto", "").strip():
                if tarea and not tarea.done():
                    tarea.cancel()
                tarea = asyncio.create_task(atender(m["texto"].strip(), "voz" if m.get("voz") else "web", m.get("imagenes")))
            elif t == "confirmar":
                fut = pendientes.get(m.get("id"))
                if fut and not fut.done():
                    fut.set_result(bool(m.get("ok")))
            elif t == "detener" and tarea and not tarea.done():
                tarea.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        clientes.discard(ws)
        if tarea and not tarea.done():
            tarea.cancel()
