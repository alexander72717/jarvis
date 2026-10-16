# Despliega JARVIS en tu servidor CasaOS (Docker) para tenerlo 24/7 y en Telegram aunque el PC esté apagado.
#   powershell -ExecutionPolicy Bypass -File scripts\desplegar_casaos.ps1 -Servidor 192.168.1.50 -Usuario casaos
# Requiere acceso SSH al servidor (idealmente con llave: ssh-keygen; ssh-copy-id).
param(
    [Parameter(Mandatory = $true)][string]$Servidor,
    [string]$Usuario = "casaos",
    [string]$Destino = "~/jarvis"
)
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
$destinoSsh = "$Usuario@$Servidor"

Write-Host "> Empaquetando JARVIS..."
$tmp = Join-Path $env:TEMP "jarvis_despliegue.tar"
if (Test-Path $tmp) { Remove-Item $tmp }
tar -cf $tmp -C $raiz jarvis web requirements.txt Dockerfile docker-compose.yml .env.example
if (Test-Path (Join-Path $raiz ".env")) { tar -rf $tmp -C $raiz .env }

Write-Host "> Copiando a $destinoSsh..."
ssh $destinoSsh "mkdir -p $Destino"
scp $tmp "${destinoSsh}:$Destino/jarvis.tar"

Write-Host "> Construyendo y levantando el contenedor..."
$cmd = "cd $Destino && tar -xf jarvis.tar && rm jarvis.tar && [ -f .env ] || cp .env.example .env; " +
       "sed -i 's/^JARVIS_HOST=.*/JARVIS_HOST=0.0.0.0/' .env; " +
       "(docker compose up -d --build || sudo docker compose up -d --build) && sleep 4 && (docker logs --tail 15 jarvis 2>/dev/null || sudo docker logs --tail 15 jarvis)"
ssh -t $destinoSsh $cmd

Write-Host ""
Write-Host "Listo: http://${Servidor}:8765  (la clave de acceso aparece en los logs de arriba o en /DATA/AppData/jarvis/data/clave.txt)"
Write-Host "OJO: si Telegram esta activo en el PC y en el servidor a la vez, solo uno recibira los mensajes. Deja TELEGRAM_TOKEN solo en el servidor."
