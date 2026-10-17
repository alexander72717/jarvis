"""El cerebro: arma el contexto, llama al router, ejecuta herramientas y aprende del usuario."""
import asyncio
import json
import logging
import re

from . import config, herramientas
from .herramientas import Contexto, fecha_legible, ubicacion
from .router import SinProveedores

log = logging.getLogger("jarvis.agente")

PERSONALIDAD = """Eres {nombre}, el asistente personal de IA de {usuario}, inspirado en J.A.R.V.I.S. de Iron Man… pero mejor.
Corres en su propio computador y tienes acceso real a él, a internet y a su servidor casero mediante herramientas.

Personalidad: elegante, leal, brillante y con un humor seco y sutil, muy británico. Eres proactivo: anticipas lo que se necesita
y ofreces el siguiente paso útil. Nunca eres servil ni exagerado. Hablas siempre en español neutro latinoamericano.

Estilo:
- Tus respuestas normalmente se dicen EN VOZ ALTA: sé breve y natural (1 a 3 frases) salvo que pidan detalle.
- No uses markdown, listas ni emojis en respuestas cortas. Para código o información larga sí puedes usar markdown.
- Nunca leas URLs largas en voz alta; menciona el sitio.
{trato}
Reglas:
- Usa las herramientas en vez de inventar. Para cualquier dato en tiempo real (clima, noticias, precios, resultados) consulta una herramienta.
- Nunca inventes datos, citas, cifras ni "datos curiosos": si no estás seguro, búscalo con 'buscar_web' o dilo con honestidad.
- Puedes encadenar varias herramientas antes de responder.
- Si te piden recordar algo o descubres un dato duradero importante del usuario, usa 'recordar'.
- Si una herramienta falla, dilo con honestidad y propone alternativa.
- Las acciones peligrosas piden confirmación al usuario automáticamente: no pidas permiso tú mismo, solo llámalas.

Contexto actual:
- Fecha y hora: {fecha}
- Ubicación aproximada: {lugar}
- Canal: {canal}
- Sistema: {sistema}
{memorias}"""

CANALES = {"web": "interfaz HUD en el PC (con voz)", "voz": "conversación por voz en el PC", "telegram": "Telegram desde el teléfono (lejos del PC)"}


class Agente:
    def __init__(self, router, memoria):
        self.router = router
        self.memoria = memoria
        self.notificar = None
        self.difundir = None

    async def sistema(self, canal: str, consulta: str) -> str:
        u = await ubicacion()
        lugar = ", ".join(x for x in (u.get("ciudad"), u.get("region"), u.get("pais")) if x) or "desconocida"
        relevantes = self.memoria.buscar(consulta, 8)
        recientes = self.memoria.hechos(12)
        vistos, lista = set(), []
        for h in relevantes + recientes:
            if h["id"] not in vistos:
                vistos.add(h["id"])
                lista.append(f"- (#{h['id']}) {h['texto']}")
        memorias = ("\nLo que sabes del usuario (memoria a largo plazo):\n" + "\n".join(lista[:18])) if lista else ""
        trato = config.tratamiento()
        nombre_u = config.usuario() or "tu usuario"
        sistema = "Windows (control total del PC)" if config.ES_WINDOWS else "servidor Linux/Docker (sin control del escritorio)"
        return PERSONALIDAD.format(
            nombre=config.nombre_asistente(), usuario=nombre_u, fecha=fecha_legible(), lugar=lugar,
            canal=CANALES.get(canal, canal), sistema=sistema, memorias=memorias,
            trato=f"- Dirígete al usuario como «{trato}» de vez en cuando, con naturalidad.\n" if trato else f"- Llámalo por su nombre ({nombre_u}) de vez en cuando.\n")

    async def responder(self, sesion: str, texto: str, emit, confirmar, canal="web", imagenes=None) -> str:
        """Procesa un mensaje. `emit(evento)` recibe el streaming; `confirmar(desc)` -> bool para acciones peligrosas."""
        contenido = texto
        if imagenes:
            contenido = [{"type": "text", "text": texto}] + [{"type": "image_url", "image_url": {"url": i}} for i in imagenes]
        msg_usuario = {"role": "user", "content": contenido}
        self.memoria.guardar_mensaje(sesion, msg_usuario)

        historial = self.memoria.historial(sesion, 20)
        # resultados de herramientas de turnos pasados: solo un resumen (ahorra miles de tokens por petición)
        for m in historial[:-1]:
            if m["role"] == "tool" and len(m["content"]) > 300:
                m["content"] = m["content"][:300] + "…"
        if imagenes and historial:
            historial[-1] = msg_usuario  # la imagen solo viaja en este turno
        mensajes = [{"role": "system", "content": await self.sistema(canal, texto)}] + historial
        ctx = Contexto(router=self.router, memoria=self.memoria, canal=canal, sesion=sesion, difundir=self.difundir, notificar=self.notificar)
        # herramientas relevantes para lo que se habla (incluye las usadas en los últimos turnos)
        usadas = {c["function"]["name"] for m in historial[-8:] for c in (m.get("tool_calls") or [])}
        previo = next((m["content"] for m in reversed(historial[:-1]) if m["role"] == "assistant" and isinstance(m.get("content"), str)), "")
        esquemas = herramientas.esquemas(f"{texto} {previo[-300:]}", usadas)
        final, meta = "", {}

        for paso in range(10):
            fin = None
            try:
                async for ev in self.router.stream(mensajes, esquemas, "inteligente", vision=bool(imagenes)):
                    t = ev["type"]
                    if t == "delta":
                        await emit({"type": "token", "text": ev["text"]})
                    elif t == "fin":
                        fin = ev
                    elif t in ("proveedor", "reset", "rotacion", "pensando"):
                        await emit(ev)
            except SinProveedores as e:
                final = str(e)
                await emit({"type": "token", "text": final})
                break
            imagenes = None
            meta = {"proveedor": fin["proveedor"], "modelo": fin["modelo"], "segundos": fin["segundos"], "tokens": fin["tokens"]}
            if not fin["llamadas"]:
                final = fin["texto"]
                self.memoria.guardar_mensaje(sesion, {"role": "assistant", "content": final}, meta)
                break

            for c in fin["llamadas"]:
                try:
                    a = json.loads(c["args"] or "{}") if isinstance(c["args"], str) else (c["args"] or {})
                except json.JSONDecodeError:
                    a = {}
                c["args"] = json.dumps(a if isinstance(a, dict) else {}, ensure_ascii=False)
            llamadas = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args"]},
                         **({"extra_content": c["extra"]} if c.get("extra") else {})} for c in fin["llamadas"]]
            m_asist = {"role": "assistant", "content": fin["texto"] or "", "tool_calls": llamadas}
            mensajes.append(m_asist)
            self.memoria.guardar_mensaje(sesion, m_asist)
            if fin["texto"]:
                await emit({"type": "token", "text": "\n"})

            for c in fin["llamadas"]:
                try:
                    args = json.loads(c["args"] or "{}") if isinstance(c["args"], str) else (c["args"] or {})
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                await emit({"type": "herramienta", "nombre": c["name"], "args": args, "estado": "inicio"})
                if herramientas.es_peligrosa(c["name"]):
                    ok = await confirmar(c["name"], args)
                    resultado = await herramientas.ejecutar(c["name"], args, ctx) if ok else "El usuario NO autorizó esta acción. No la ejecutes ni insistas."
                else:
                    resultado = await herramientas.ejecutar(c["name"], args, ctx)
                await emit({"type": "herramienta", "nombre": c["name"], "args": args, "estado": "fin", "resultado": resultado[:400]})
                m_tool = {"role": "tool", "tool_call_id": c["id"], "content": resultado}
                mensajes.append(m_tool)
                self.memoria.guardar_mensaje(sesion, m_tool)
        else:
            final = "He encadenado demasiadas acciones seguidas; me detengo aquí por seguridad."
            await emit({"type": "token", "text": final})

        await emit({"type": "fin", "meta": meta, "texto": final})
        if self.memoria.contar_usuario(sesion) % 4 == 0:
            asyncio.create_task(self.aprender(sesion))
        return final

    async def aprender(self, sesion: str):
        """Cada pocos mensajes, un modelo rápido extrae datos duraderos del usuario (en segundo plano)."""
        try:
            hist = [m for m in self.memoria.historial(sesion, 12) if m["role"] in ("user", "assistant") and m.get("content")]
            if not hist:
                return
            charla = "\n".join(f"{m['role']}: {m['content'][:600]}" for m in hist)
            conocidos = "\n".join(h["texto"] for h in self.memoria.hechos(40))
            prompt = [{"role": "system", "content": "Extraes datos personales DURADEROS y útiles sobre el usuario (gustos, proyectos, personas, rutinas, preferencias, datos técnicos de sus equipos). Ignora lo trivial o pasajero. Responde SOLO un arreglo JSON de strings en español, cada uno autocontenido en tercera persona. Si no hay nada nuevo responde []."},
                      {"role": "user", "content": f"Ya conocidos:\n{conocidos or '(ninguno)'}\n\nConversación:\n{charla}"}]
            salida = await self.router.completar(prompt, "rapido")
            m = re.search(r"\[.*\]", salida, re.S)
            for dato in json.loads(m.group(0)) if m else []:
                if isinstance(dato, str) and 8 < len(dato) < 300:
                    self.memoria.recordar(dato, "aprendido")
        except Exception as e:  # noqa: BLE001
            log.debug("aprender: %s", e)
