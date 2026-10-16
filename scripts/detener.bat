@echo off
rem Detiene el núcleo de JARVIS
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do taskkill /pid %%p /f >nul 2>nul
echo JARVIS detenido.
