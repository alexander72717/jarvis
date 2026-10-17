#!/usr/bin/env bash
# IA local de JARVIS en el servidor CasaOS (Ollama en Docker), con límites para no estorbar a las demás apps.
#   ssh usuario@servidor 'bash -s' < scripts/ollama_servidor.sh
#
# - CPU: máximo 4 de 8 hilos y prioridad baja (cpu-shares 256): cede ante cualquier otra app.
# - RAM: máximo 5 GB. Un modelo cargado a la vez, una petición a la vez, se descarga tras 10 min sin uso.
# - GPU: usa la NVIDIA del servidor pasando los dispositivos y las librerías del driver directamente
#   (no hace falta nvidia-container-toolkit ni sudo). Contexto 4096 + flash attention + caché q8_0
#   para que qwen3:4b quepa entero en los ~4 GB de la GTX 960M.
set -euo pipefail

MODELO="${MODELO:-qwen3:4b-instruct}"
L=/usr/lib/x86_64-linux-gnu
V=$(sed -n 's/.*Kernel Module *\([0-9.]*\).*/\1/p' /proc/driver/nvidia/version 2>/dev/null || true)

GPU=()
if [ -n "$V" ] && [ -e /dev/nvidia0 ]; then
  GPU=(--device /dev/nvidia0 --device /dev/nvidiactl --device /dev/nvidia-uvm --device /dev/nvidia-uvm-tools
       -v "$L/libcuda.so.$V:$L/libcuda.so.1:ro" -v "$L/libnvidia-ml.so.$V:$L/libnvidia-ml.so.1:ro"
       -v "$L/libnvidia-ptxjitcompiler.so.$V:$L/libnvidia-ptxjitcompiler.so.1:ro" -v "$L/libnvidia-nvvm.so.$V:$L/libnvidia-nvvm.so.4:ro")
  echo "GPU NVIDIA detectada (driver $V)"
else
  echo "Sin GPU NVIDIA: solo CPU"
fi

docker rm -f ollama >/dev/null 2>&1 || true
docker run -d --name ollama --restart unless-stopped \
  --cpus=4 --cpu-shares=256 --memory=5g --memory-swap=5g \
  "${GPU[@]}" \
  -e OLLAMA_NUM_PARALLEL=1 -e OLLAMA_MAX_LOADED_MODELS=1 -e OLLAMA_KEEP_ALIVE=10m \
  -e OLLAMA_CONTEXT_LENGTH=4096 -e OLLAMA_NUM_THREAD=4 -e OLLAMA_FLASH_ATTENTION=1 -e OLLAMA_KV_CACHE_TYPE=q8_0 \
  -p 11434:11434 -v /DATA/AppData/ollama:/root/.ollama ollama/ollama:latest

sleep 5
docker exec ollama ollama pull "$MODELO"
echo "Listo. En el .env de JARVIS: OLLAMA_URL=http://<ip-tailscale-del-servidor>:11434/v1  OLLAMA_MODELO=$MODELO"
