"""JARVIS en tu teléfono vía Telegram (gratis, sin abrir puertos: usa long polling).

Mandas texto o notas de voz; responde con texto y, si hablaste, con voz.
Solo atiende a los IDs en TELEGRAM_USUARIOS. Las acciones peligrosas piden confirmación con botones.
"""
import asyncio
import logging
import uuid

import httpx

from . import config, voz

log = logging.getLogger("jarvis.telegram")


class Telegram:
    def __init__(self, agente, router):
        self.agente, self.router = agente, router
        self.token = config.env("TELEGRAM_TOKEN")
        self.permitidos = set(config.env_lista("TELEGRAM_USUARIOS"))
        self.api = f"https://api.telegram.org/bot{self.token}"
        self.http = httpx.AsyncClient(timeout=45)
        self.pendientes: dict[str, asyncio.Future] = {}

    @property
    def activo(self) -> bool:
        return bool(self.token)

    async def llamar(self, metodo, **kw):
        try:
            r = await self.http.post(f"{self.api}/{metodo}", **kw)
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("Telegram %s: %s", metodo, e)
            return {}

    async def notificar(self, texto: str) -> bool:
        if not self.activo or not self.permitidos:
            return False
        ok = False
        for uid in self.permitidos:
            r = await self.llamar("sendMessage", json={"chat_id": uid, "text": texto[:4000]})
            ok = ok or r.get("ok", False)
        return ok

    async def bucle(self):
        if not self.activo:
            return
        yo = await self.llamar("getMe")
        log.info("Telegram conectado como @%s", (yo.get("result") or {}).get("username"))
        offset = 0
        while True:
            try:
                r = await self.http.get(f"{self.api}/getUpdates", params={"timeout": 30, "offset": offset}, timeout=40)
                actualizaciones = r.json().get("result", [])
            except (httpx.HTTPError, ValueError):
                await asyncio.sleep(5)
                continue
            for u in actualizaciones:
                offset = u["update_id"] + 1
                if "callback_query" in u:
                    await self._boton(u["callback_query"])
                elif "message" in u:
                    asyncio.create_task(self._mensaje(u["message"]))

    async def _boton(self, q):
        clave, _, valor = (q.get("data") or "").partition(":")
        fut = self.pendientes.pop(clave, None)
        if fut and not fut.done() and str(q["from"]["id"]) in self.permitidos:
            fut.set_result(valor == "si")
        await self.llamar("answerCallbackQuery", json={"callback_query_id": q["id"], "text": "Autorizado" if valor == "si" else "Cancelado"})

    async def _mensaje(self, msg):
        chat, uid = msg["chat"]["id"], str(msg["from"]["id"])
        if uid not in self.permitidos:
            await self.llamar("sendMessage", json={"chat_id": chat, "text": f"Acceso denegado. Tu ID de Telegram es {uid}. Agrégalo a TELEGRAM_USUARIOS en el .env de JARVIS."})
            return
        hablado = False
        texto = msg.get("text") or msg.get("caption") or ""
        audio = msg.get("voice") or msg.get("audio")
        if audio:
            info = await self.llamar("getFile", json={"file_id": audio["file_id"]})
            ruta = (info.get("result") or {}).get("file_path")
            if ruta:
                datos = (await self.http.get(f"https://api.telegram.org/file/bot{self.token}/{ruta}")).content
                try:
                    texto = await self.router.transcribir(datos, "nota.ogg")
                    hablado = True
                except Exception as e:  # noqa: BLE001
                    await self.llamar("sendMessage", json={"chat_id": chat, "text": f"No pude transcribir la nota de voz: {e}"})
                    return
        if not texto:
            return
        if texto.strip() == "/start":
            await self.llamar("sendMessage", json={"chat_id": chat, "text": "JARVIS en línea. Escríbeme o mándame una nota de voz."})
            return

        await self.llamar("sendChatAction", json={"chat_id": chat, "action": "typing"})

        async def emit(ev):
            if ev["type"] == "herramienta" and ev["estado"] == "inicio":
                await self.llamar("sendChatAction", json={"chat_id": chat, "action": "typing"})

        async def confirmar(nombre, args):
            clave = uuid.uuid4().hex[:10]
            fut = asyncio.get_running_loop().create_future()
            self.pendientes[clave] = fut
            detalle = "\n".join(f"{k}: {str(v)[:300]}" for k, v in args.items())
            await self.llamar("sendMessage", json={"chat_id": chat, "text": f"⚠️ Quiero ejecutar «{nombre}»\n{detalle}\n¿Autorizas?",
                                                   "reply_markup": {"inline_keyboard": [[{"text": "✅ Sí", "callback_data": f"{clave}:si"}, {"text": "❌ No", "callback_data": f"{clave}:no"}]]}})
            try:
                return await asyncio.wait_for(fut, 120)
            except asyncio.TimeoutError:
                return False

        respuesta = await self.agente.responder(f"tg:{chat}", texto, emit, confirmar, canal="telegram")
        if hablado:
            await self.llamar("sendMessage", json={"chat_id": chat, "text": f"🎙 «{texto}»"})
        await self.llamar("sendMessage", json={"chat_id": chat, "text": respuesta[:4000] or "…"})
        if hablado and respuesta:
            try:
                mp3 = await voz.sintetizar(respuesta[:1500])
                await self.llamar("sendVoice", data={"chat_id": chat}, files={"voice": ("jarvis.mp3", mp3, "audio/mpeg")})
            except Exception as e:  # noqa: BLE001
                log.warning("voz telegram: %s", e)
