"""Pruebas de la conexión con Claude Code usando una línea de comandos simulada (no gasta tu plan).
Ejecutar: .venv\\Scripts\\python -m tests.test_claude
"""
import asyncio
import json
import tempfile
from pathlib import Path

from jarvis import claude_code as c
from jarvis.herramientas import Contexto


def main():
    c.CARPETA = Path(tempfile.mkdtemp())
    c.ejecutable = lambda: "claude"
    llamadas, avisos = [], []

    class Proc:
        async def communicate(self):
            return json.dumps({"is_error": False, "session_id": "ses-1", "permission_denials": [],
                               "result": "Listo.\nRESUMEN: Encontré dos errores y te dejé el plan."}).encode(), b""

    async def lanzar(*args, **kw):
        llamadas.append((args, kw["cwd"]))
        return Proc()

    async def avisar(texto):
        avisos.append(texto)

    c.asyncio.create_subprocess_exec, c.AVISAR = lanzar, avisar
    ctx = Contexto.__new__(Contexto)

    async def flujo():
        await c.preguntar_a_claude(ctx, "revisa el router", str(Path.home()))
        await asyncio.gather(*c.TAREAS.values())
        args = llamadas[-1][0]
        assert args[args.index("--permission-mode") + 1] == "plan", "preguntar debe ser de solo lectura"
        assert "dos errores" in avisos[-1]
        await c.seguir_con_claude(ctx, "¿y el segundo?")
        await asyncio.gather(*c.TAREAS.values())
        args = llamadas[-1][0]
        assert args[args.index("--resume") + 1] == "ses-1" and args[args.index("--permission-mode") + 1] == "plan"
        await c.encargar_a_claude(ctx, "arréglalo", continuar=c._todas()[0]["id"])
        await asyncio.gather(*c.TAREAS.values())
        args = llamadas[-1][0]
        assert args[args.index("--permission-mode") + 1] == "acceptEdits" and "--resume" in args

    asyncio.run(flujo())
    print("✓ preguntar solo lee; seguir mantiene la sesión y solo lee; encargar edita (con confirmación de JARVIS)")
    assert c._resumen("bla bla\nRESUMEN: corto y claro.") == "corto y claro."
    print("✓ resumen para leer en voz alta")
    c.ejecutable = lambda: None
    assert "no está instalado" in asyncio.run(c.preguntar_a_claude(ctx, "hola"))
    print("✓ sin Claude Code instalado explica cómo instalarlo")
    print("\nTodas las pruebas pasaron.")


if __name__ == "__main__":
    main()
