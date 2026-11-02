# JARVIS arranca al iniciar sesión, escondido en su pestaña del borde.
#   powershell -ExecutionPolicy Bypass -File scripts\instalar_inicio.ps1           instalar
#   powershell -ExecutionPolicy Bypass -File scripts\instalar_inicio.ps1 -Quitar   quitar
param([switch]$Quitar)

$raiz = Split-Path $PSScriptRoot -Parent
$acceso = Join-Path ([Environment]::GetFolderPath("Startup")) "JARVIS.lnk"
if ($Quitar) {
    Remove-Item $acceso -ErrorAction SilentlyContinue
    Write-Output "Listo: JARVIS ya no arranca con Windows."
    exit
}
$a = (New-Object -ComObject WScript.Shell).CreateShortcut($acceso)
$a.TargetPath = Join-Path $raiz ".venv\Scripts\pythonw.exe"
$a.Arguments = '"' + (Join-Path $raiz "scripts\jarvis_lateral.pyw") + '" --cerrado'
$a.WorkingDirectory = $raiz
$a.Save()
Write-Output "Listo: JARVIS arranca con Windows."
