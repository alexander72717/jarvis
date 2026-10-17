"""La tecla rombo (tecla OMEN) de los HP Victus/OMEN como botón de JARVIS.

No es una tecla normal: el BIOS de HP la avisa como evento WMI `hpqBEvnt` (EventId 29).
Escuchamos esos eventos con un PowerShell en segundo plano (no requiere administrador).
"""
import asyncio
import logging
import subprocess
import threading
import time

from . import config

log = logging.getLogger("jarvis.tecla")
EVENTO_OMEN = 29

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Register-WmiEvent -Namespace root\wmi -Class hpqBEvnt -SourceIdentifier jarvis | Out-Null
[Console]::Out.WriteLine('LISTO'); [Console]::Out.Flush()
while ($true) {
  $e = Wait-Event -SourceIdentifier jarvis -Timeout 3600
  if ($e) {
    $n = $e.SourceEventArgs.NewEvent
    [Console]::Out.WriteLine("EVENTO $($n.eventId) $($n.eventData)"); [Console]::Out.Flush()
    Remove-Event -EventIdentifier $e.EventIdentifier
  }
}
"""


def _hilo(loop, al_pulsar, parar):
    espera = 5
    while not parar.is_set():
        try:
            proc = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            log.warning("no pude escuchar la tecla rombo: %s", e)
            return
        parar.proc = proc
        ultima = 0.0
        for linea in proc.stdout:
            if parar.is_set():
                break
            texto = linea.decode(errors="replace").strip()
            if texto == "LISTO":
                log.info("Tecla rombo lista")
                espera = 5
            elif texto.startswith("EVENTO"):
                partes = texto.split()
                if len(partes) >= 2 and partes[1] == str(EVENTO_OMEN) and time.time() - ultima > 0.4:
                    ultima = time.time()
                    asyncio.run_coroutine_threadsafe(al_pulsar(), loop)
        if proc.poll() is None:
            proc.kill()
        if parar.wait(espera):
            break
        espera = min(espera * 2, 300)  # equipos que no son HP: se rinde despacio, sin gastar recursos


async def escuchar(al_pulsar):
    """Llama a `al_pulsar()` cada vez que se presiona la tecla rombo. Se reinicia solo si falla."""
    if not config.ES_WINDOWS or config.env("TECLA_ROMBO", "si").lower() in ("no", "0", "false"):
        return
    parar = threading.Event()
    hilo = threading.Thread(target=_hilo, args=(asyncio.get_running_loop(), al_pulsar, parar), daemon=True, name="tecla-rombo")
    hilo.start()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        parar.set()
        proc = getattr(parar, "proc", None)
        if proc and proc.poll() is None:
            proc.kill()
