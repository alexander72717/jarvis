"""Nodos: equipos que le prestan herramientas al cerebro (por ejemplo el PC con Windows).

El cerebro (servidor) no puede abrir apps ni tocar el volumen del PC; el PC se conecta por WebSocket
a /nodo, anuncia sus herramientas y ejecuta las que el cerebro le pida. Si el PC se apaga, esas
herramientas simplemente desaparecen de la lista.
"""
import asyncio
import json
import logging
import uuid

log = logging.getLogger("jarvis.nodos")


class Nodo:
    def __init__(self, ws, nombre: str, herramientas: list[dict], control: bool = False):
        self.ws, self.nombre, self.control = ws, nombre, control
        self.herramientas = {h["nombre"]: h for h in herramientas}  # nombre -> {nombre, desc, params, req, peligrosa}
        self.pendientes: dict[str, asyncio.Future] = {}


class Registro:
    def __init__(self):
        self.nodos: dict[str, Nodo] = {}

    # ----- conexión -----
    async def atender(self, ws, nombre: str, herramientas: list[dict], control: bool = False):
        """Se queda escuchando al nodo hasta que se desconecte."""
        nodo = Nodo(ws, nombre, herramientas, control)
        anterior = self.nodos.get(nombre)
        if anterior:
            for f in anterior.pendientes.values():
                if not f.done():
                    f.set_result("El PC se reconectó a mitad de la acción; inténtalo de nuevo.")
        self.nodos[nombre] = nodo
        log.info("nodo conectado: %s (%d herramientas)", nombre, len(herramientas))
        try:
            while True:
                m = json.loads(await ws.receive_text())
                if m.get("type") == "control_estado":
                    nodo.control = bool(m.get("activo"))
                elif m.get("type") == "resultado":
                    f = nodo.pendientes.pop(m.get("id"), None)
                    if f and not f.done():
                        f.set_result(str(m.get("resultado", "")))
        finally:
            if self.nodos.get(nombre) is nodo:
                del self.nodos[nombre]
            for f in nodo.pendientes.values():
                if not f.done():
                    f.set_result(f"Se perdió la conexión con {nombre} antes de terminar.")
            log.info("nodo desconectado: %s", nombre)

    # ----- herramientas -----
    def remotas(self, locales: set[str]) -> dict[str, dict]:
        """nombre_visible -> definición. Si el cerebro ya tiene una herramienta con ese nombre, la del nodo
        se llama nombre_pc (p. ej. estado_sistema_pc = el PC, estado_sistema = el servidor)."""
        out = {}
        for nodo in self.nodos.values():
            for n, h in nodo.herramientas.items():
                visible = f"{n}_pc" if n in locales else n
                out[visible] = {**h, "nodo": nodo.nombre, "original": n,
                                "desc": f"[En el PC «{nodo.nombre}»] {h['desc']}"}
        return out

    async def ejecutar(self, definicion: dict, args: dict, timeout: float = 120) -> str:
        nodo = self.nodos.get(definicion["nodo"])
        if not nodo:
            return f"El PC «{definicion['nodo']}» no está conectado (¿apagado?)."
        clave = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        nodo.pendientes[clave] = fut
        try:
            await nodo.ws.send_text(json.dumps({"type": "ejecutar", "id": clave, "nombre": definicion["original"], "args": args}, ensure_ascii=False))
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return f"El PC tardó más de {int(timeout)} s en responder."
        except Exception as e:  # noqa: BLE001
            return f"No pude hablar con el PC: {e}"
        finally:
            nodo.pendientes.pop(clave, None)

    # ----- control remoto: canal aparte de las herramientas, la IA no puede usarlo -----
    def hay_control(self) -> bool:
        return any(n.control for n in self.nodos.values())

    async def control(self, m: dict) -> dict:
        nodo = next((n for n in self.nodos.values() if n.control), None)
        if not nodo:
            return {"error": "Ningún PC tiene el control remoto prendido."}
        clave = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        nodo.pendientes[clave] = fut
        try:
            await nodo.ws.send_text(json.dumps({"type": "control", "id": clave, "m": m}, ensure_ascii=False))
            return json.loads(await asyncio.wait_for(fut, 8))
        except (asyncio.TimeoutError, ValueError, RuntimeError) as e:
            return {"error": f"El PC no respondió: {type(e).__name__}"}
        finally:
            nodo.pendientes.pop(clave, None)

    def estado(self) -> list[dict]:
        return [{"nombre": n.nombre, "herramientas": len(n.herramientas)} for n in self.nodos.values()]


REGISTRO = Registro()
