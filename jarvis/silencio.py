"""Silencio mientras JARVIS habla o escucha: pausa la música/videos y silencia los demás sonidos del PC.

- Pausa con los controles multimedia de Windows (Spotify web, YouTube, apps de música…), solo lo que
  estaba sonando, y luego lo reanuda exactamente igual.
- Silencia las demás sesiones de audio activas (juegos, llamadas…), menos la del navegador donde está JARVIS.
- Si JARVIS se cierra a mitad, al volver a arrancar restaura lo que había dejado en silencio.
"""
import asyncio
import json
import logging
import time

from . import config

log = logging.getLogger("jarvis.silencio")
ESTADO = config.DATOS / "silencio.json"
REPRODUCIENDO = 4  # GlobalSystemMediaTransportControlsSessionPlaybackStatus.Playing
PROPIOS = ("python.exe", "pythonw.exe")


def _es_jarvis(app_id: str) -> bool:
    return "127.0.0.1" in app_id or "localhost" in app_id


class Silenciador:
    def __init__(self):
        self.activo = False
        self.pausadas: list[str] = []      # sesiones multimedia que pausamos
        self.silenciadas: list[int] = []   # PIDs a los que les quitamos el sonido
        self.desde = 0.0
        self.lock = asyncio.Lock()
        self.disponible = config.ES_WINDOWS

    # ---------- audio por programa (Core Audio vía pycaw) ----------
    @staticmethod
    def _sesiones():
        import comtypes
        from pycaw.pycaw import AudioUtilities

        comtypes.CoInitialize()
        return AudioUtilities.GetAllSessions()

    def _silenciar_otros(self, excluir: str | None) -> list[int]:
        hechos = []
        try:
            for s in self._sesiones():
                if not s.Process:
                    continue
                nombre = s.Process.name().lower()
                if nombre in PROPIOS or (excluir and nombre == excluir.lower()):
                    continue
                vol = s.SimpleAudioVolume
                if s.State == 1 and not vol.GetMute():  # 1 = sonando ahora mismo
                    vol.SetMute(1, None)
                    hechos.append(s.Process.pid)
        except Exception as e:  # noqa: BLE001
            log.warning("no pude silenciar: %s", e)
        return hechos

    def _devolver_sonido(self, pids: list[int]):
        if not pids:
            return
        try:
            for s in self._sesiones():
                if s.Process and s.Process.pid in pids:
                    s.SimpleAudioVolume.SetMute(0, None)
        except Exception as e:  # noqa: BLE001
            log.warning("no pude devolver el sonido: %s", e)

    # ---------- reproductores (controles multimedia de Windows) ----------
    @staticmethod
    async def _gestor():
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M

        return await M.request_async()

    async def _pausar_reproductores(self) -> list[str]:
        hechos = []
        try:
            for s in (await self._gestor()).get_sessions():
                app = s.source_app_user_model_id
                if _es_jarvis(app) or s.get_playback_info().playback_status != REPRODUCIENDO:
                    continue
                if await s.try_pause_async():
                    hechos.append(app)
        except Exception as e:  # noqa: BLE001
            log.warning("no pude pausar: %s", e)
        return hechos

    async def _reanudar_reproductores(self, apps: list[str]):
        if not apps:
            return
        try:
            for s in (await self._gestor()).get_sessions():
                if s.source_app_user_model_id in apps:
                    await s.try_play_async()
        except Exception as e:  # noqa: BLE001
            log.warning("no pude reanudar: %s", e)

    # ---------- API ----------
    def _guardar(self):
        try:
            ESTADO.write_text(json.dumps({"pausadas": self.pausadas, "silenciadas": self.silenciadas}), encoding="utf-8")
        except OSError:
            pass

    async def silenciar(self, navegador_jarvis: str | None = None) -> dict:
        if not self.disponible:
            return {"activo": False}
        async with self.lock:
            if self.activo:
                self.desde = time.time()
                return self.estado()
            self.pausadas = await self._pausar_reproductores()
            self.silenciadas = await asyncio.to_thread(self._silenciar_otros, navegador_jarvis)
            self.activo, self.desde = True, time.time()
            self._guardar()
            log.info("silencio: pausé %s, silencié %d programas", self.pausadas or "nada", len(self.silenciadas))
            return self.estado()

    async def restaurar(self) -> dict:
        async with self.lock:
            if not self.activo:
                return self.estado()
            await asyncio.to_thread(self._devolver_sonido, self.silenciadas)
            await self._reanudar_reproductores(self.pausadas)
            self.activo, self.pausadas, self.silenciadas = False, [], []
            ESTADO.unlink(missing_ok=True)
            return self.estado()

    async def recuperar_tras_cierre(self):
        """Si el proceso anterior murió con cosas en silencio, se las devolvemos."""
        try:
            datos = json.loads(ESTADO.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.activo, self.pausadas, self.silenciadas = True, datos.get("pausadas", []), datos.get("silenciadas", [])
        await self.restaurar()

    async def vigilante(self):
        """Seguro: nunca deja el PC en silencio más de 90 s si la interfaz deja de avisar."""
        while True:
            await asyncio.sleep(10)
            if self.activo and time.time() - self.desde > 90:
                log.warning("silencio demasiado largo: restaurando")
                await self.restaurar()

    def estado(self) -> dict:
        return {"activo": self.activo, "pausadas": self.pausadas, "silenciadas": len(self.silenciadas)}
