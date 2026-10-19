# J.A.R.V.I.S. — tu asistente local que nunca se queda sin tokens

Un JARVIS que corre en tu PC, habla, escucha, ve tu pantalla, controla Windows, vigila tu servidor CasaOS
y te responde en el teléfono. **Todo es gratis**: en vez de depender de una sola IA, rota entre **13 proveedores
de IA con capa gratuita**. Cuando uno se agota, salta al siguiente sin que lo notes.

```
            ┌──────────── tú ────────────┐
       voz / texto / Telegram / imagen
                     │
              ┌──────▼──────┐   memoria (SQLite) · recordatorios · protocolos
              │   AGENTE    │── 31 herramientas: web, clima, noticias, PC, pantalla, servidor…
              └──────┬──────┘
              ┌──────▼──────┐   elige la mejor ranura libre (proveedor × modelo × llave)
              │   ROUTER    │   429 → enfría lo que pide el proveedor → siguiente
              └──────┬──────┘
   Gemini · Groq · Cerebras · Mistral · GitHub · OpenRouter · NVIDIA · SambaNova
   Cohere · Cloudflare · HuggingFace · Ollama (offline) · Pollinations (sin registro)
```

## Arranque en 1 clic

Doble clic en **JARVIS** en el escritorio (o en `JARVIS.bat`). Se abre como app.
La primera vez instala todo solo. Funciona **sin ninguna llave** con el proveedor de emergencia
(Pollinations), pero es lento: pon al menos Groq y Gemini (2 minutos, sin tarjeta) y responde en ~1 s.

### Poner las llaves (todas gratis)
En la interfaz: **⚙ Ajustes → Llaves de IA gratis** → pega y "Guardar y conectar". O edita `.env`.

| Proveedor | Dónde | Por qué vale la pena |
|---|---|---|
| Groq | console.groq.com/keys | Rapidísimo. Kimi K2, GPT-OSS 120B, Llama. **También da Whisper (voz → texto)** |
| Google Gemini | aistudio.google.com/apikey | Cuota **por modelo** (6 bolsas), con visión: "mira mi pantalla" |
| Cerebras | cloud.cerebras.ai | Miles de tokens/seg, GPT-OSS 120B, Qwen 235B |
| Mistral | console.mistral.ai | Plan Experiment: muchísimos tokens al mes |
| GitHub Models | github.com/settings/personal-access-tokens | GPT-4.1 y GPT-4o gratis (permiso *Models: read*) |
| OpenRouter | openrouter.ai/settings/keys | Descubre solo **todos** los modelos `:free` del momento |
| NVIDIA NIM | build.nvidia.com | DeepSeek, Kimi, Qwen… |
| SambaNova, Cohere, Cloudflare, Hugging Face | ver `.env.example` | Más reservas |
| Ollama | ollama.com | 100% offline, en el PC o **en tu CasaOS** |

> Puedes poner varias llaves del mismo proveedor separadas por coma; cada una suma su propia cuota.

## Por qué es mejor que Jarvis

- **Nunca se queda sin cerebro.** La unidad de rotación es *proveedor × modelo × llave*: con 6 proveedores
  configurados tienes ~35 bolsas de cuota independientes. Lee los `retry-after` reales de cada API
  ("try again in 1m30s", `retryDelay`, cuotas diarias) y enfría exactamente lo necesario. Si una cuenta
  agota su cuota diaria, se enfrían todos sus modelos de una vez. Si un proveedor se cae **a mitad de
  respuesta**, la reinicia con otro. Los catálogos se auto-descubren al arrancar: si un modelo gratis
  desaparece o sale uno nuevo, se adapta solo.
- **Tres estrategias**: *Equilibrio* (reparte la carga para que ninguna cuota se agote antes de tiempo),
  *Calidad* (siempre el mejor libre) y *Velocidad* (el de menor latencia medida).
- **Modelos sin herramientas también sirven**: si un modelo no soporta *function calling*, JARVIS le
  enseña un protocolo de texto y sigue usando herramientas igual.
- **Ahorra el cerebro grande**: tareas de fondo (aprender datos tuyos) van a modelos rápidos.
- **Matriz neuronal en vivo**: el reactor muestra cada IA como un nodo; verás el rayo cuando rota.
- **Aprende de ti**: cada pocos mensajes extrae datos duraderos y los recuerda (los ves/borras en Ajustes).
- **Te ve**: botón 👁 o "Jarvis, mira mi pantalla" → captura + IA con visión. También puedes pegar imágenes.
- **En tu bolsillo**: bot de Telegram con notas de voz (te responde con voz) y confirmaciones con botones.
- **Protocolos**: "Jarvis, protocolo trabajo" abre tus apps. Pídele que cree protocolos nuevos por voz.
- **Pide permiso** antes de comandos, archivos, apagar el PC o tocar el servidor (modal o "sí"/"no" por voz).

## Voz

- **Palabra clave**: activa el interruptor «JARVIS» y di *"Jarvis, ¿qué clima hace?"*. Si solo dices
  "Jarvis", te responde con un tono y espera la orden. Decir "Jarvis" mientras habla lo interrumpe.
- **Ctrl + Espacio** o el micrófono: hablar una vez. **Esc**: callarlo.
- Voces neuronales gratis de Microsoft (edge-tts). Cambia la voz y velocidad en Ajustes.
- Reconocimiento: el del navegador (Edge/Chrome). En otros navegadores usa Whisper de Groq.

## Herramientas (31)

Web y conocimiento: `buscar_web`, `leer_web`, `noticias`, `wikipedia`, `clima`, `calcular`, `fecha_hora`.
Memoria: `recordar`, `buscar_memoria`, `olvidar`, recordatorios (`crear/listar/cancelar`).
PC: `abrir` (cualquier app del menú Inicio/escritorio por nombre aproximado), `multimedia` (volumen, pausa,
siguiente), `ver_pantalla`, `portapapeles`, `estado_sistema`, archivos, `ejecutar_comando`⚠, `energia`⚠.
Servidor: `servidor_estado`, `servidor_comando`⚠, `servidor_encender` (Wake-on-LAN).
Otros: protocolos, `estado_ia` ("¿cuántos tokens me quedan?"), `enviar_al_telefono`.  ⚠ = pide confirmación.

## Telegram (JARVIS desde cualquier lugar, gratis)

1. En Telegram habla con **@BotFather** → `/newbot` → copia el token a `TELEGRAM_TOKEN`.
2. Reinicia JARVIS y escríbele a tu bot: te responde con tu ID. Ponlo en `TELEGRAM_USUARIOS`.
3. Los recordatorios también te llegan al teléfono. No abre puertos (usa long polling).

## Servidor CasaOS (24/7)

Con el PC apagado, JARVIS puede vivir en tu servidor (sin las herramientas de escritorio):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\desplegar_casaos.ps1 -Servidor 192.168.1.50 -Usuario casaos
```

O en CasaOS: *App Store → + → Instalar app personalizada → importar* `docker-compose.yml`.
Abre `http://IP-del-servidor:8765`; la primera vez pide la clave que aparece en los logs
(o pon la tuya en `JARVIS_CLAVE`). Instala **Ollama** desde la App Store de CasaOS y tendrás IA offline
como último respaldo para todos tus dispositivos. Para que el PC consulte el servidor, llena
`SERVIDOR_HOST`/`SERVIDOR_USUARIO` y configura una llave SSH (`ssh-keygen` + `ssh-copy-id`).

## IA local en el servidor (ilimitada)

`scripts/ollama_servidor.sh` instala Ollama en el CasaOS con límites (máx. 4 hilos con prioridad baja,
5 GB de RAM, un modelo, se descarga tras 10 min sin uso) y usa la GPU NVIDIA si existe.
Modo por defecto `IA_LOCAL_MODO=auto`: la conversación va a la nube (1–3 s) y la IA local hace el
trabajo de fondo (aprender de ti) cuando nadie está hablando; si las cuotas gratis bajan del 30 %,
JARVIS pasa solo a «local primero». Otros modos: `siempre`, `fondo`, `no` (también en Ajustes).
En modo local, lo difícil (programar, explicar, analizar, pantalla, Notion…) sigue yendo a una IA
potente, y si la local intenta «fingir» una acción sin usar la herramienta, se descarta y escala.

## Un solo JARVIS en el PC y en el celular

```
 📱 celular ──(Tailscale, HTTPS)──► 🧠 cerebro en el servidor (Docker): memoria, historial, recordatorios, IAs
 💻 PC ───────── «nodo» ──────────┘   el PC le presta: abrir apps, Spotify, volumen, pantalla, Notion…
```

1. En el servidor: `docker compose up -d --build` con su propio `.env` (`JARVIS_HOST=0.0.0.0`,
   una `JARVIS_CLAVE` larga, tus llaves y `OLLAMA_URL=http://host.docker.internal:11434/v1`).
2. HTTPS solo dentro de Tailscale (para el micrófono del celular):
   `sudo tailscale serve --bg --https=8443 http://127.0.0.1:8765`
3. En el `.env` del PC: `JARVIS_HUB=http://<ip-tailscale>:8765`, `JARVIS_HUB_CLAVE=<la misma clave>`,
   `JARVIS_CELULAR_URL=https://<servidor>.<tailnet>.ts.net:8443`.
4. En el PC: ⚙ → **📱 Conectar el celular** muestra un QR de un solo uso (15 min). Con Tailscale
   encendido en el celular, escanéalo y queda conectado.

**Archivos entre dispositivos** (pasan por tu servidor, dentro de Tailscale, y se borran a los 7 días):
- PC → celular: «mándame al celular la factura que tengo en el escritorio» → JARVIS la busca en el PC
  y aparece una tarjeta para descargarla en todas tus pantallas.
- Celular → PC: en Android, **Compartir → JARVIS** (app instalada) o el 📎 de la app; luego
  «guárdalo en el escritorio del PC». Nunca sobrescribe y nunca envía archivos con llaves (.env, SSH, .pem).
- Por seguridad del navegador, la app no puede explorar sola los archivos del celular: tú eliges qué compartir.

Lo que dices en un dispositivo aparece en vivo en el otro. Desde el celular puedes pedir cosas al PC
(«baja el volumen de mi PC», «abre Spotify») si está encendido; si el servidor cae, el PC sigue solo.

## Impresión 3D con Bambu Lab

«Jarvis, abre el soporte de la cámara» → busca el STL/3MF/OBJ/STEP en el PC, te manda una **vista previa con
sus medidas** a todas tus pantallas y lo carga en Bambu Studio. «Imprímelo en naranja» → lamina, revisa **todas
las impresoras de tu cuenta** (estén en tu casa o no: va por la nube de Bambu), descarta las ocupadas, lee los
colores de cada AMS y lo envía a una libre que tenga ese color. Siempre te pide confirmación antes de enviar.
También: «¿qué impresoras están libres?» o «imprímelo en negro en la del taller».

- Requisitos: **Bambu Studio** instalado con tu cuenta iniciada. No hace falta el «Modo desarrollador»: conservas
  la nube y Bambu Handy, porque JARVIS maneja Bambu Studio como lo harías tú (mueve el ratón en esa ventana).
- Usa el perfil de impresora con el que se laminó (el último elegido en Bambu Studio). Una impresora de otro
  modelo (p. ej. X1 Carbon con un archivo para A1) aparece como «no compatible» y no se usa.
- Un solo color por impresión. Las casillas vacías o con un material incompatible se ignoran.

## JARVIS para tus amigos

Versión autónoma: no usa tus llaves ni se conecta a tu PC. Cada amigo puede poner sus llaves gratis
(se guardan solo en su equipo); sin llaves funciona con Pollinations (1 respuesta cada 30 s).

- **PC:** `python movil/empaquetar_pc.py` genera `dist/JARVIS-para-amigos.html`, un solo archivo de
  ~90 KB. Se comparte por WhatsApp/Drive y se abre con doble clic en Chrome o Edge (en Edge suena mejor:
  trae voces neuronales). Incluye clima, búsqueda, Wikipedia, noticias (GDELT), cálculo exacto,
  temporizadores y memoria.
- **Android:** el proyecto está en `movil/` (WebView + puente nativo para voz, linterna y vibración).
  Falta compilar la APK, para lo cual se necesita JDK 17 y el SDK de Android.

## Seguridad

- Por defecto solo escucha en `127.0.0.1`. Si lo expones en la red, exige clave.
- Las llaves viven solo en `.env` (excluido de git). La API nunca las devuelve completas.
- Telegram solo atiende a los IDs autorizados.

## Estructura

```
jarvis/
  proveedores.py   catálogo de IAs gratis (añadir una = añadir un diccionario)
  router.py        rotación, enfriamientos, streaming, protocolo de herramientas
  agente.py        personalidad, contexto, bucle de herramientas, aprendizaje
  herramientas.py  las 31 herramientas
  memoria.py       SQLite: conversaciones, hechos (FTS5), recordatorios
  voz.py           edge-tts      telegram.py   bot      servidor.py   FastAPI + WebSocket
web/               interfaz HUD (reactor, matriz neuronal, voz)
tests/             pruebas del router con proveedores simulados
```

Pruebas: `.venv\Scripts\python -m tests.test_rotacion` y `.venv\Scripts\python -m tests.test_impresion3d`
Logs: `data\jarvis.log` · Detener: `scripts\detener.bat` · Arrancar con Windows: `scripts\instalar.ps1 -ConWindows`
