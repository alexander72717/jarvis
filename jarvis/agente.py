"""El cerebro: arma el contexto, llama al router, ejecuta herramientas y aprende del usuario."""
import asyncio
import json
import time
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

# Versión corta para la IA local: en CPU cada token del mensaje cuesta tiempo.
# Fija a propósito (sin hora ni memorias): así la IA local reutiliza su caché entre peticiones.
PERSONALIDAD_CORTA = """Eres {nombre}, el asistente personal de {usuario} (estilo J.A.R.V.I.S.: elegante, leal, con humor seco).
Hablas en español latinoamericano. Respuestas MUY breves (1-2 frases), naturales para decir en voz alta, sin markdown ni emojis.
Usa las herramientas para datos reales o acciones en el PC; nunca inventes datos.
Cada mensaje del usuario empieza con [Contexto: …] con la fecha, la hora y lo que sabes de él.
{trato}"""

# Señales de que la petición necesita una IA potente (el resto lo resuelve la IA local del servidor)
DIFICIL = ("programa", "código", "codigo", "script", "función", "funcion", "error", "bug", "depura", "explic", "explíc", "analiza",
           "compara", "resume", "resumen", "traduce", "redacta", "escribe un", "escribe una", "ensayo", "informe", "plan ",
           "planea", "estrategia", "investiga", "por qué", "porque ", "cómo funciona", "como funciona", "diferencia",
           "ecuación", "ecuacion", "demuestra", "piénsalo", "piensalo", "a fondo", "detallad", "paso a paso", "pantalla",
           "imagen", "foto", "notion", "correo", "email", "protocolo", "archivo", "comando", "servidor")


# Acciones cuyo resultado YA es la respuesta: con la IA local no se le pide una segunda redacción (ahorra ~8 s)
VOLUMEN = {"subir_volumen": "Volumen arriba.", "bajar_volumen": "Volumen abajo.", "play_pausa": "Listo.", "siguiente": "Siguiente canción.",
           "anterior": "Canción anterior.", "silenciar": "Silencio.", "detener": "Detenido."}


def respuesta_directa(nombre: str, args: dict, resultado: str) -> str | None:
    if resultado.startswith(("Error", "No ")) or len(resultado) > 320:
        return None
    if nombre == "multimedia":
        return VOLUMEN.get(args.get("accion"), "Hecho.")
    if nombre == "crear_recordatorio" and "para " in resultado:
        cuando, _, que = resultado.split("para ", 1)[1].partition(": ")
        return f"Listo, te recordaré «{que}» el {cuando}."
    if nombre in ("abrir", "poner_musica", "recordar", "cancelar_recordatorio"):
        return resultado
    return None


# Peticiones que SOLO se cumplen usando una herramienta: si la IA local contesta sin usar ninguna, se lo inventó
ACCIONES = re.compile(r"\b(pon|ponme|reproduce|abre|ábreme|cierra|sube|baja|pausa|pausar|siguiente|anterior|silencia|"
                      r"recuérdame|recuerdame|recuerda que|apunta|anota|busca|búscame|clima|lloverá|llovera|temperatura|"
                      r"noticias|apaga|enciende|volumen|temporizador|alarma)\b", re.I)


def modo_local() -> str:
    """auto (por defecto): nube primero, local si escasean las cuotas · siempre: local primero · fondo / no."""
    m = config.env("IA_LOCAL_MODO", "auto").lower()
    return m if m in ("auto", "siempre", "fondo", "no") else "auto"


def nivel_para(texto: str, imagenes, cuotas_bajas: bool = False) -> str:
    t = texto.lower()
    modo = modo_local()
    if modo in ("fondo", "no") or (modo == "auto" and not cuotas_bajas):
        return "inteligente"
    if imagenes or len(texto) > 260 or any(p in t for p in DIFICIL):
        return "inteligente"
    return "local"


CANALES = {"web": "interfaz web de JARVIS", "voz": "conversación por voz en el PC", "telegram": "Telegram desde el teléfono (lejos del PC)",
           "pc": "la pantalla del PC del usuario (con voz)",
           "celular": "el celular del usuario, quizá lejos del PC: las herramientas marcadas [En el PC] actúan en su computador si está encendido"}


class Agente:
    def __init__(self, router, memoria):
        self.router = router
        self.memoria = memoria
        self.notificar = None
        self.difundir = None

    async def sistema(self, canal: str, consulta: str, compacto: bool = False) -> str:
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
        if compacto:
            return self.sistema_local()
        sistema = "Windows (control total del PC)" if config.ES_WINDOWS else "servidor Linux/Docker (sin control del escritorio)"
        return PERSONALIDAD.format(
            nombre=config.nombre_asistente(), usuario=nombre_u, fecha=fecha_legible(), lugar=lugar,
            canal=CANALES.get(canal, canal), sistema=sistema, memorias=memorias,
            trato=f"- Dirígete al usuario como «{trato}» de vez en cuando, con naturalidad.\n" if trato else f"- Llámalo por su nombre ({nombre_u}) de vez en cuando.\n")

    def sistema_local(self) -> str:
        trato = config.tratamiento()
        return PERSONALIDAD_CORTA.format(nombre=config.nombre_asistente(), usuario=config.usuario() or "tu usuario",
                                         trato=f"Llámalo «{trato}» a veces." if trato else "")

    async def contexto_local(self, consulta: str) -> str:
        u = await ubicacion()
        lugar = ", ".join(x for x in (u.get("ciudad"), u.get("pais")) if x) or "desconocida"
        hechos = {h["id"]: h["texto"] for h in self.memoria.buscar(consulta, 4) + self.memoria.hechos(3)}
        sabes = ("; sabes: " + "; ".join(list(hechos.values())[:6])) if hechos else ""
        return f"[Contexto: {fecha_legible()}; {lugar}{sabes}]"

    async def precalentar(self):
        """Carga el modelo local y su caché (instrucciones + herramientas) antes de que el usuario termine de hablar."""
        import time as _t

        usa_local = modo_local() == "siempre" or (modo_local() == "auto" and self.router.cuotas_bajas())
        if not usa_local or not self.router.hay_local() or _t.time() - getattr(self, "_calentado", 0) < 240:
            return
        self._calentado = _t.time()
        try:
            await self.router.completar([{"role": "system", "content": self.sistema_local()}, {"role": "user", "content": "hola"}],
                                        "local", herramientas_fijas=herramientas.esquemas_locales())
        except Exception:  # noqa: BLE001
            self._calentado = 0

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
        self.ultimo_uso = time.time()
        nivel = nivel_para(texto, imagenes, self.router.cuotas_bajas()) if self.router.hay_local() else "inteligente"
        if nivel == "local":
            # en CPU cada token cuenta: solo los últimos 4 mensajes de texto, recortados, sin rastros de herramientas
            previos = [{"role": m["role"], "content": m["content"][:300]} for m in historial[:-1]
                       if m["role"] in ("user", "assistant") and not m.get("tool_calls") and isinstance(m.get("content"), str) and m["content"]]
            historial = previos[-4:] + historial[-1:]
            while historial and historial[0]["role"] != "user":
                historial.pop(0)
            if historial and isinstance(historial[-1].get("content"), str):
                historial[-1] = {**historial[-1], "content": f"{await self.contexto_local(texto)}\n{historial[-1]['content']}"}
        mensajes = [{"role": "system", "content": await self.sistema(canal, texto, compacto=nivel == "local")}] + historial
        ctx = Contexto(router=self.router, memoria=self.memoria, canal=canal, sesion=sesion, difundir=self.difundir, notificar=self.notificar)
        # herramientas relevantes para lo que se habla (incluye las usadas en los últimos turnos)
        usadas = {c["function"]["name"] for m in historial[-8:] for c in (m.get("tool_calls") or [])}
        previo = next((m["content"] for m in reversed(historial[:-1]) if m["role"] == "assistant" and isinstance(m.get("content"), str)), "")
        esquemas_potentes = herramientas.esquemas(f"{texto} {previo[-300:]}", usadas)
        esquemas_local = herramientas.esquemas_locales()
        final, meta = "", {}

        for paso in range(10):
            fin = None
            try:
                esquemas = esquemas_local if nivel == "local" else esquemas_potentes
                async for ev in self.router.stream(mensajes, esquemas, nivel, vision=bool(imagenes)):
                    t = ev["type"]
                    if t == "delta":
                        await emit({"type": "token", "text": ev["text"]})
                    elif t == "fin":
                        fin = ev
                    elif t in ("proveedor", "reset", "rotacion", "pensando"):
                        await emit(ev)
            except SinProveedores as e:
                if nivel == "local":
                    # la IA local no está o falló: la misma petición pasa a las potentes
                    nivel = "inteligente"
                    mensajes[0] = {"role": "system", "content": await self.sistema(canal, texto)}
                    await emit({"type": "reset"})
                    continue
                final = str(e)
                await emit({"type": "token", "text": final})
                break
            imagenes = None
            meta = {"proveedor": fin["proveedor"], "modelo": fin["modelo"], "segundos": fin["segundos"], "tokens": fin["tokens"]}
            if not fin["llamadas"] and nivel == "local" and ACCIONES.search(texto):
                # la local respondió como si hubiera hecho algo, sin hacerlo: lo hace una potente
                nivel = "inteligente"
                mensajes[0] = {"role": "system", "content": await self.sistema(canal, texto)}
                await emit({"type": "reset"})
                continue
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
                if nivel == "local" and (len(resultado) > 2500 or paso >= 2 or c["name"] not in herramientas.todas()):
                    nivel = "inteligente"
                    mensajes[0] = {"role": "system", "content": await self.sistema(canal, texto)}
                directa = respuesta_directa(c["name"], args, resultado) if nivel == "local" and len(fin["llamadas"]) == 1 else None
                m_tool = {"role": "tool", "tool_call_id": c["id"], "content": resultado}
                mensajes.append(m_tool)
                self.memoria.guardar_mensaje(sesion, m_tool)
            if directa:
                final = directa
                await emit({"type": "token", "text": final})
                self.memoria.guardar_mensaje(sesion, {"role": "assistant", "content": final}, meta)
                break
        else:
            final = "He encadenado demasiadas acciones seguidas; me detengo aquí por seguridad."
            await emit({"type": "token", "text": final})

        await emit({"type": "fin", "meta": meta, "texto": final})
        if self.memoria.contar_usuario(sesion) % 4 == 0:
            asyncio.create_task(self.aprender_cuando_libre(sesion))
        return final

    async def aprender_cuando_libre(self, sesion: str):
        """Espera a que JARVIS lleve 60 s sin conversación y entonces aprende (con la IA local si existe)."""
        for _ in range(60):  # como mucho ~1 h de espera
            await asyncio.sleep(60)
            if time.time() - getattr(self, "ultimo_uso", 0) >= 60:
                break
        await self.aprender(sesion)
        self._calentado = 0  # el aprendizaje cambió la caché de la IA local: la próxima vez se vuelve a calentar

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
            try:
                salida = await self.router.completar(prompt, "local")
            except SinProveedores:
                salida = await self.router.completar(prompt, "rapido")
            m = re.search(r"\[.*\]", salida, re.S)
            for dato in json.loads(m.group(0)) if m else []:
                if isinstance(dato, str) and 8 < len(dato) < 300:
                    self.memoria.recordar(dato, "aprendido")
        except Exception as e:  # noqa: BLE001
            log.debug("aprender: %s", e)
