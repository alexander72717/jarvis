# Crea el acceso directo "JARVIS" en el escritorio y, si quieres, que arranque con Windows.
#   powershell -ExecutionPolicy Bypass -File scripts\instalar.ps1 [-ConWindows]
param([switch]$ConWindows)

$raiz = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $raiz "JARVIS.bat"
$icono = Join-Path $raiz "web\jarvis.ico"
$shell = New-Object -ComObject WScript.Shell

function Crear($ruta) {
    $a = $shell.CreateShortcut($ruta)
    $a.TargetPath = $bat
    $a.WorkingDirectory = $raiz
    $a.WindowStyle = 7   # minimizado
    if (Test-Path $icono) { $a.IconLocation = $icono }
    $a.Description = "J.A.R.V.I.S. - asistente local"
    $a.Save()
    Write-Host "Creado: $ruta"
}

Crear (Join-Path ([Environment]::GetFolderPath("Desktop")) "JARVIS.lnk")
if ($ConWindows) { Crear (Join-Path ([Environment]::GetFolderPath("Startup")) "JARVIS.lnk") }
