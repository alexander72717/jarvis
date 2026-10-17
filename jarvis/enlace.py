"""Enlace del PC con el cerebro del servidor.

Con JARVIS_HUB en el .env (p. ej. http://100.x.y.z:8765, la IP de Tailscale del servidor), este JARVIS deja de pensar solo:
- se conecta al cerebro como «nodo» y ejecuta las herramientas del PC que el cerebro le pida;
- la interfaz del PC conversa a través del cerebro (misma memoria e historial que el celular).
Si el cerebro no responde, todo vuelve a funcionar en local, como siempre.
"""
import asyncio
import json
import logging
import platform

import httpx
import websockets

from . import config, herramientas
from .herramientas import Contexto

log = logging.getLogger("jarvis.enlace")


def hub() -> str:
    return config.env("JARVIS_HUB").rstrip("/")


def clave() -> str:
    return config.env("JARVIS_HUB_CLAVE")


def url_ws(ruta: str, **extra) -> str:
    base = hub().replace("https://", "wss://").replace("http://", "ws://")
    params = "&".join(f"{k}={v}" for k, v in extra.items() if v)
    return f"{base}{ruta}" + (f"?{params}" if params else "")


def cabeceras() -> dict:
    return {"X-Jarvis-Clave": clave()} if clave() else {}


class Enlace:
    def __init__(self):
        self.conectado = False
        self.http = httpx.AsyncClient(timeout=15)

    @property
    def activo(self) -> bool:
        return bool(hub())

    async def mantener(self, router, memoria):
        """Mantiene viva la conexión de nodo con el cerebro; reintenta solo si se cae."""
        if not self.activo:
            return
        espera = 3
        nombre = config.env("NODO_NOMBRE", platform.node() or "PC")
        while True:
            try:
                async with websockets.connect(url_ws("/nodo"), additional_headers=cabeceras(), max_size=None, ping_interval=20, open_timeout=10) as ws:
                    await ws.send(json.dumps({"type": "registro", "nombre": nombre, "herramientas": herramientas.para_nodo()}, ensure_ascii=False))
                    self.conectado, espera = True, 3
                    log.info("conectado al cerebro %s como nodo «%s»", hub(), nombre)
                    ctx = Contexto(router=router, memoria=memoria, canal="nodo", sesion="nodo")
                    async for crudo in ws:
                        m = json.loads(crudo)
                        if m.get("type") == "ejecutar":
                            asyncio.create_task(self._ejecutar(ws, ctx, m))
            except (OSError, websockets.WebSocketException, asyncio.TimeoutError) as e:
                if self.conectado:
                    log.warning("se perdió el cerebro (%s); funciono en local mientras vuelve", e)
            finally:
                self.conectado = False
            await asyncio.sleep(espera)
            espera = min(espera * 2, 60)

    async def _ejecutar(self, ws, ctx, m):
        log.info("el cerebro pide: %s %s", m.get("nombre"), m.get("args"))
        resultado = await herramientas.ejecutar(m.get("nombre", ""), m.get("args") or {}, ctx)
        try:
            await ws.send(json.dumps({"type": "resultado", "id": m.get("id"), "resultado": resultado}, ensure_ascii=False))
        except websockets.WebSocketException:
            pass

    # ----- la interfaz del PC habla a través del cerebro -----
    async def conversar(self, ws_ui):
        """Une la pantalla del PC con el cerebro: lo que dices va al servidor y lo que responde vuelve aquí."""
        async with websockets.connect(url_ws("/ws", dispositivo="pc"), additional_headers=cabeceras(), max_size=None, ping_interval=20, open_timeout=10) as ws_hub:
            async def hacia_ui():
                async for crudo in ws_hub:
                    try:
                        tipo = json.loads(crudo).get("type")
                    except ValueError:
                        continue
                    if tipo == "sistema":  # las métricas del servidor no; la pantalla del PC muestra las del PC
                        continue
                    await ws_ui.send_text(crudo)

            async def hacia_hub():
                while True:
                    crudo = await ws_ui.receive_text()
                    await ws_hub.send(crudo)

            tareas = [asyncio.create_task(hacia_ui()), asyncio.create_task(hacia_hub())]
            try:
                await asyncio.wait(tareas, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in tareas:
                    t.cancel()

    async def pedir(self, metodo: str, ruta: str, **kw):
        r = await self.http.request(metodo, hub() + ruta, headers=cabeceras(), **kw)
        r.raise_for_status()
        return r.json()


ENLACE = Enlace()
