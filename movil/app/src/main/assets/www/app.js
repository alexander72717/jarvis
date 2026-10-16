/* JARVIS Móvil: versión autónoma. Todo corre en el teléfono y rota entre IAs gratuitas. */
"use strict";

const $ = (s) => document.querySelector(s);
const NATIVO = window.Android || null;
const CREADOR = "Samuel";

const leer = (k, d) => { try { const v = localStorage.getItem("jm." + k); return v === null ? d : JSON.parse(v); } catch { return d; } };
const escribir = (k, v) => { try { localStorage.setItem("jm." + k, JSON.stringify(v)); } catch { /* sin almacenamiento */ } };

const S = {
  modo: "espera", nombre: leer("nombre", ""), voz: leer("voz", true), llaves: leer("llaves", {}),
  historial: leer("historial", []), memorias: leer("memorias", []), estadoRanuras: leer("ranuras", {}),
  ocupado: false, hablando: false, conversacion: false, nivel: 0, activa: null, temporizadores: [],
};

/* ====================== red (sin CORS gracias al puente nativo) ====================== */
const pendientes = {};
let contador = 0;
window.__nativo = {
  resp(id, estado, cuerpo, espera) { const p = pendientes[id]; delete pendientes[id]; if (p) p({ estado, cuerpo, espera }); },
  evento(tipo, dato) { alEvento(tipo, dato); },
};

function http(metodo, url, cabeceras = {}, cuerpo = null) {
  if (NATIVO) {
    return new Promise((ok) => {
      const id = "r" + ++contador;
      pendientes[id] = ok;
      NATIVO.http(id, metodo, url, JSON.stringify(cabeceras), cuerpo || "");
    });
  }
  return fetch(url, { method: metodo, headers: cabeceras, body: cuerpo })
    .then(async (r) => ({ estado: r.status, cuerpo: await r.text(), espera: r.headers.get("retry-after") || "" }))
    .catch((e) => ({ estado: 0, cuerpo: "Error de red: " + e.message, espera: "" }));
}

/* ====================== proveedores gratis ====================== */
const PROVS = [
  { id: "groq", nombre: "Groq", url: "https://api.groq.com/openai/v1/chat/completions", registro: "https://console.groq.com/keys", prioridad: 1,
    modelos: ["moonshotai/kimi-k2-instruct-0905", "openai/gpt-oss-120b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"] },
  { id: "gemini", nombre: "Gemini", url: "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", registro: "https://aistudio.google.com/apikey", prioridad: 1,
    modelos: ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-2.0-flash"] },
  { id: "cerebras", nombre: "Cerebras", url: "https://api.cerebras.ai/v1/chat/completions", registro: "https://cloud.cerebras.ai", prioridad: 2,
    modelos: ["gpt-oss-120b", "llama-3.3-70b", "qwen-3-32b"] },
  { id: "mistral", nombre: "Mistral", url: "https://api.mistral.ai/v1/chat/completions", registro: "https://console.mistral.ai/api-keys", prioridad: 2,
    modelos: ["mistral-medium-latest", "mistral-small-latest"] },
  { id: "github", nombre: "GitHub", url: "https://models.github.ai/inference/chat/completions", registro: "https://github.com/settings/personal-access-tokens/new", prioridad: 2,
    modelos: ["openai/gpt-4.1", "openai/gpt-4.1-mini"] },
  { id: "openrouter", nombre: "OpenRouter", url: "https://openrouter.ai/api/v1/chat/completions", registro: "https://openrouter.ai/settings/keys", prioridad: 3, cuotaCuenta: true,
    modelos: ["openrouter/free"] },
  // sin registro: 1 respuesta cada 30 s por IP, así que se espera el turno en vez de fallar
  { id: "pollinations", nombre: "Pollinations", url: "https://text.pollinations.ai/openai", sinLlave: true, prioridad: 9, intervalo: 31, modelos: ["openai"] },
];

function ranuras() {
  const out = [];
  for (const p of PROVS) {
    const llaves = p.sinLlave ? [""] : String(S.llaves[p.id] || "").split(",").map((x) => x.trim()).filter(Boolean);
    llaves.forEach((llave, n) => p.modelos.forEach((m, i) => {
      const id = `${p.id}:${m}#${n}`;
      out.push({ id, prov: p, modelo: m, llave, rango: p.prioridad * 10 + i, est: S.estadoRanuras[id] || (S.estadoRanuras[id] = {}) });
    }));
  }
  return out;
}

const recientes = {};
function elegir(excluir) {
  const ahora = Date.now();
  const libres = ranuras().filter((r) => !excluir.has(r.id) && !(r.est.hasta > ahora));
  if (!libres.length) return null;
  const puntaje = (r) => {
    const rec = (recientes[r.id] || []).filter((t) => ahora - t < 60000).length;
    return r.rango + rec * 4 + (r.est.tools === false ? 10 : 0) + Math.random() * 2;
  };
  return libres.sort((a, b) => puntaje(a) - puntaje(b))[0];
}

function esperaDe(texto, cab) {
  if (cab && !isNaN(parseFloat(cab))) return parseFloat(cab);
  const t = texto.toLowerCase();
  let m = t.match(/retrydelay"?\s*:\s*"(\d+(?:\.\d+)?)s/);
  if (m) return +m[1];
  m = t.match(/retry after\s+(\d+(?:\.\d+)?)\s*s/);
  if (m) return +m[1];
  m = t.match(/(?:try again|retry) in\s+(?:(\d+)h)?(?:(\d+)m(?!s))?(?:(\d+(?:\.\d+)?)(ms|s))?/);
  if (m && (m[1] || m[2] || m[3])) return (+m[1] || 0) * 3600 + (+m[2] || 0) * 60 + (m[3] ? +m[3] / (m[4] === "ms" ? 1000 : 1) : 0);
  return null;
}

function clasificar(res) {
  const t = (res.cuerpo || "").toLowerCase(), e = res.estado, espera = esperaDe(res.cuerpo || "", res.espera);
  if (e === 429 || /rate.?limit|free.?limit|limit reached|quota|resource_exhausted|too many requests/.test(t))
    return { tipo: /per day|daily|rpd|tpd|per-day|monthly/.test(t) ? "cuota_diaria" : "limite", espera };
  if (e === 401 || e === 403) return { tipo: "llave" };
  if ((e === 400 || e === 422) && /tool|function/.test(t)) return { tipo: "sin_tools" };
  if (e === 402 || /insufficient|credit/.test(t)) return { tipo: "creditos" };
  if (e === 413 || /context length|too long|maximum context/.test(t)) return { tipo: "contexto" };
  if (e === 404 || /not found|does not exist|decommissioned|deprecated/.test(t)) return { tipo: "modelo" };
  return { tipo: e === 0 ? "red" : e >= 500 ? "servidor" : "otro", espera };
}

const MOTIVOS = { limite: "límite por minuto", cuota_diaria: "cuota diaria agotada", llave: "llave inválida", creditos: "sin créditos", modelo: "modelo retirado", red: "sin conexión", servidor: "servidor caído", vacio: "respuesta vacía", otro: "error" };
function penalizar(r, err) {
  const est = r.est;
  est.fallos = (est.fallos || 0) + 1;
  const escala = [20, 60, 180, 600, 1800][Math.min(est.fallos - 1, 4)];
  let seg = 0;
  if (err.tipo === "sin_tools") { est.tools = false; est.fallos--; }
  else if (err.tipo === "contexto") est.fallos--;
  else if (err.tipo === "limite") seg = err.espera || escala;
  else if (err.tipo === "cuota_diaria") seg = err.espera || 3600;
  else if (["llave", "creditos", "modelo"].includes(err.tipo)) seg = 12 * 3600;
  else seg = err.espera || escala;
  if (seg) {
    const hasta = Date.now() + Math.min(seg, 86400) * 1000;
    const afectadas = (err.tipo === "llave" || (err.tipo === "cuota_diaria" && r.prov.cuotaCuenta))
      ? ranuras().filter((x) => x.prov === r.prov && x.llave === r.llave) : [r];
    for (const x of afectadas) { x.est.hasta = hasta; x.est.motivo = MOTIVOS[err.tipo] || err.tipo; }
  }
  escribir("ranuras", S.estadoRanuras);
}

/* ---- modelos sin herramientas nativas: se les enseña un protocolo de texto ---- */
function aplanar(mensajes) {
  const out = [];
  for (const m of mensajes) {
    if (m.role === "tool") out.push({ role: "user", content: "[Resultado de la herramienta]\n" + m.content });
    else if (m.tool_calls) out.push({ role: "assistant", content: [m.content || "", ...m.tool_calls.map((c) => `<herramienta>${JSON.stringify({ nombre: c.function.name, args: JSON.parse(c.function.arguments || "{}") })}</herramienta>`)].join("\n") });
    else out.push({ role: m.role, content: m.content || "" });
  }
  return out.reduce((acc, m) => {
    const u = acc[acc.length - 1];
    if (u && u.role === m.role && m.role !== "system") u.content += "\n\n" + m.content; else acc.push({ ...m });
    return acc;
  }, []);
}
function protocolo(tools) {
  return "\n\n## Herramientas\nPara usar una herramienta responde ÚNICAMENTE con: <herramienta>{\"nombre\": \"...\", \"args\": {...}}</herramienta>\nLuego recibirás el resultado. Disponibles:\n" +
    tools.map((t) => `- ${t.function.name}(${Object.keys((t.function.parameters || {}).properties || {}).join(", ")}): ${t.function.description}`).join("\n");
}
function extraerProtocolo(texto) {
  const llamadas = [];
  const limpio = texto.replace(/<herramienta>([\s\S]*?)<\/herramienta>/g, (_, j) => {
    try { const d = JSON.parse(j); llamadas.push({ nombre: d.nombre || d.name, args: JSON.stringify(d.args || {}) }); } catch { /* ignorar */ }
    return "";
  });
  return { texto: limpio.trim(), llamadas };
}

async function pedir(r, mensajes, tools) {
  const nativo = tools && r.est.tools !== false;
  let msgs = nativo ? mensajes : aplanar(mensajes);
  if (tools && !nativo) { msgs = msgs.map((m) => ({ ...m })); msgs[0].content += protocolo(tools); }
  const cuerpo = { model: r.modelo, messages: msgs };
  // Gemini rechaza objetos sin propiedades; los servidores vLLM exigen "parameters" siempre
  if (nativo) cuerpo.tools = r.prov.id === "gemini" ? tools : tools.map((t) => t.function.parameters ? t : { type: "function", function: { ...t.function, parameters: { type: "object", properties: {} } } });
  const cab = { "Content-Type": "application/json" };
  if (r.llave) cab.Authorization = "Bearer " + r.llave;
  if (r.prov.id === "openrouter") { cab["HTTP-Referer"] = "https://jarvis.movil"; cab["X-Title"] = "JARVIS Movil"; }
  if (r.prov.intervalo) {
    const falta = (r.prov.ultimoUso || 0) + r.prov.intervalo * 1000 - Date.now();
    for (let s = Math.ceil(falta / 1000); s > 0; s--) { poner("pensando", `EN COLA · ${s} s`); await new Promise((ok) => setTimeout(ok, 1000)); }
    r.prov.ultimoUso = Date.now();
  }
  (recientes[r.id] = recientes[r.id] || []).push(Date.now());
  const t0 = Date.now();
  const res = await http("POST", r.prov.url, cab, JSON.stringify(cuerpo));
  if (res.estado !== 200) throw clasificar(res);
  let d;
  try { d = JSON.parse(res.cuerpo); } catch { throw { tipo: "otro" }; }
  if (d.error) throw clasificar({ estado: +d.error.code || 500, cuerpo: JSON.stringify(d.error) });
  const m = (d.choices && d.choices[0] && d.choices[0].message) || {};
  let texto = String(m.content || "").replace(/<think>[\s\S]*?<\/think>/g, "").trim();
  let llamadas = (m.tool_calls || []).map((c) => ({ nombre: c.function.name, args: typeof c.function.arguments === "string" ? c.function.arguments : JSON.stringify(c.function.arguments || {}) }));
  if (tools && !nativo) { const e = extraerProtocolo(texto); texto = e.texto; llamadas = llamadas.concat(e.llamadas); }
  llamadas = llamadas.map((c) => {
    let a = {}; try { a = JSON.parse(c.args || "{}"); } catch { /* */ }
    if (a && typeof a.args === "object" && (a.nombre || a.name)) return { nombre: a.nombre || a.name, args: a.args };
    return { nombre: c.nombre, args: a && typeof a === "object" ? a : {} };
  });
  if (!texto && !llamadas.length) throw { tipo: "vacio" };
  r.est.fallos = 0; r.est.motivo = "";
  escribir("ranuras", S.estadoRanuras);
  return { texto, llamadas, segundos: ((Date.now() - t0) / 1000).toFixed(1) };
}

async function rotar(mensajes, tools) {
  const probadas = new Set();
  for (let i = 0; i < 10; i++) {
    const r = elegir(probadas);
    if (!r) break;
    probadas.add(r.id);
    S.activa = r.prov.id; pintarIAs();
    poner("pensando", "ENLACE: " + r.prov.nombre.toUpperCase());
    try {
      const res = await pedir(r, mensajes, tools);
      res.ranura = r;
      return res;
    } catch (err) {
      penalizar(r, err);
      if (err.tipo === "sin_tools") { probadas.delete(r.id); continue; }
      chip(`↻ ${r.prov.nombre}: ${MOTIVOS[err.tipo] || err.tipo} → rotando`, "rot");
      rayo(r.prov.id);
    }
  }
  S.activa = null; pintarIAs();
  const prox = Math.min(...ranuras().map((r) => r.est.hasta || 0).filter(Boolean), Infinity);
  const falta = isFinite(prox) ? Math.max(0, Math.round((prox - Date.now()) / 1000)) : 0;
  throw new Error(`Todas mis IAs están descansando. La primera vuelve en ${Math.floor(falta / 60)} min ${falta % 60} s. Agrega llaves gratis en ajustes para tener más.`);
}

/* ====================== herramientas ====================== */
let ubic = null;
async function ubicacion() {
  if (ubic) return ubic;
  const r = await http("GET", "https://ipwho.is/?lang=es");
  try { const d = JSON.parse(r.cuerpo); if (d.success !== false) ubic = { ciudad: d.city, pais: d.country, lat: d.latitude, lon: d.longitude }; } catch { /* */ }
  return ubic || {};
}
async function geocodificar(nombre) {
  const r = await http("GET", "https://geocoding-api.open-meteo.com/v1/search?count=1&language=es&name=" + encodeURIComponent(nombre));
  const x = (JSON.parse(r.cuerpo).results || [])[0];
  return x ? { ciudad: x.name, pais: x.country, lat: x.latitude, lon: x.longitude } : null;
}
const CLIMA = { 0: "despejado", 1: "mayormente despejado", 2: "parcialmente nublado", 3: "nublado", 45: "niebla", 48: "niebla", 51: "llovizna ligera", 53: "llovizna", 55: "llovizna intensa", 61: "lluvia ligera", 63: "lluvia", 65: "lluvia fuerte", 80: "chubascos", 81: "chubascos", 82: "chubascos fuertes", 95: "tormenta", 96: "tormenta con granizo", 99: "tormenta fuerte" };
const texto = (html) => new DOMParser().parseFromString(html, "text/html").body.textContent.replace(/\s+/g, " ").trim();

const HERRAMIENTAS = {
  fecha_hora: { d: "Fecha y hora actuales.", p: {}, f: () => new Date().toLocaleString("es-CO", { dateStyle: "full", timeStyle: "short" }) },
  clima: { d: "Clima actual y de los próximos días.", p: { ciudad: "Ciudad (opcional, por defecto la del usuario)" }, f: async ({ ciudad }) => {
    const u = ciudad ? await geocodificar(ciudad) : await ubicacion();
    if (!u || u.lat == null) return "No pude saber la ubicación.";
    const r = await http("GET", `https://api.open-meteo.com/v1/forecast?latitude=${u.lat}&longitude=${u.lon}&timezone=auto&forecast_days=3&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max`);
    const d = JSON.parse(r.cuerpo), c = d.current, dd = d.daily;
    return `${u.ciudad}, ${u.pais}: ${c.temperature_2m}°C (sensación ${c.apparent_temperature}°C), ${CLIMA[c.weather_code] || "variable"}, humedad ${c.relative_humidity_2m}%.\n` +
      dd.time.map((t, i) => `${t}: ${CLIMA[dd.weather_code[i]] || "variable"}, ${dd.temperature_2m_min[i]}–${dd.temperature_2m_max[i]}°C, lluvia ${dd.precipitation_probability_max[i]}%`).join("\n");
  } },
  buscar_web: { d: "Busca en internet. Úsala para cualquier dato actual o que no sepas.", p: { consulta: "Qué buscar" }, req: ["consulta"], f: async ({ consulta }) => {
    const r = await http("POST", "https://html.duckduckgo.com/html/", { "Content-Type": "application/x-www-form-urlencoded" }, "q=" + encodeURIComponent(consulta));
    if (r.estado !== 200) return "La búsqueda no está disponible ahora.";
    const doc = new DOMParser().parseFromString(r.cuerpo, "text/html");
    const res = [...doc.querySelectorAll(".result")].slice(0, 6).map((el, i) => {
      const a = el.querySelector(".result__a"); let href = a ? a.getAttribute("href") : "";
      if (href && href.includes("uddg=")) href = decodeURIComponent(href.split("uddg=")[1].split("&")[0]);
      return `${i + 1}. ${a ? a.textContent.trim() : ""}\n${href}\n${(el.querySelector(".result__snippet") || {}).textContent || ""}`;
    });
    return res.join("\n\n") || "Sin resultados.";
  } },
  leer_web: { d: "Lee el texto de una página web.", p: { url: "Dirección" }, req: ["url"], f: async ({ url }) => texto((await http("GET", url)).cuerpo).slice(0, 5000) },
  noticias: { d: "Titulares de noticias recientes, generales o de un tema.", p: { tema: "Tema (opcional)" }, f: async ({ tema }) => {
    const q = "hl=es-419&gl=CO&ceid=CO:es-419";
    const r = await http("GET", tema ? `https://news.google.com/rss/search?q=${encodeURIComponent(tema)}&${q}` : `https://news.google.com/rss?${q}`);
    const doc = new DOMParser().parseFromString(r.cuerpo, "text/xml");
    return [...doc.querySelectorAll("item")].slice(0, 8).map((i) => "- " + i.querySelector("title").textContent).join("\n") || "Sin noticias.";
  } },
  wikipedia: { d: "Resumen de un tema en Wikipedia.", p: { tema: "Tema" }, req: ["tema"], f: async ({ tema }) => {
    const b = JSON.parse((await http("GET", "https://es.wikipedia.org/w/api.php?action=opensearch&limit=1&format=json&search=" + encodeURIComponent(tema))).cuerpo);
    if (!b[1] || !b[1].length) return "No está en Wikipedia.";
    const d = JSON.parse((await http("GET", "https://es.wikipedia.org/api/rest_v1/page/summary/" + encodeURIComponent(b[1][0]))).cuerpo);
    return `${d.title}: ${d.extract}`;
  } },
  calcular: { d: "Calcula una expresión matemática exacta (sqrt, sin, log, PI, ^).", p: { expresion: "Ej: sqrt(2)*15^2" }, req: ["expresion"], f: ({ expresion }) => {
    const permitidas = new Set(Object.getOwnPropertyNames(Math));
    let ok = true;
    const js = String(expresion).replace(/\^/g, "**").replace(/[a-z_]\w*/gi, (id) => { const n = permitidas.has(id) ? id : permitidas.has(id.toUpperCase()) ? id.toUpperCase() : null; if (!n) ok = false; return "Math." + n; });
    if (!ok || /[^\w\s+\-*/().,%]/.test(js)) return "Expresión no permitida.";
    return `${expresion} = ${Function('"use strict";return (' + js + ")")()}`;
  } },
  recordar: { d: "Guarda un dato duradero del usuario (gustos, datos personales que comparta).", p: { dato: "El dato, en tercera persona" }, req: ["dato"], f: ({ dato }) => {
    if (!S.memorias.includes(dato)) { S.memorias.push(dato); escribir("memorias", S.memorias.slice(-60)); }
    return "Guardado en memoria.";
  } },
  temporizador: { d: "Pone un temporizador o recordatorio (funciona con la app abierta).", p: { minutos: "Minutos", texto: "Qué recordar" }, req: ["minutos"], f: ({ minutos, texto: t }) => {
    const ms = Math.max(0.1, +minutos || 1) * 60000;
    S.temporizadores.push(setTimeout(() => { vibrar(800); const aviso = "Recordatorio: " + (t || "se acabó el tiempo"); chip("⏰ " + aviso); decir(aviso); }, ms));
    return `Temporizador de ${minutos} minutos listo.`;
  } },
  linterna: { d: "Enciende o apaga la linterna del teléfono.", p: { encender: "true para encender, false para apagar" }, tipo: { encender: "boolean" }, req: ["encender"], f: ({ encender }) =>
    NATIVO ? (NATIVO.linterna(encender === true || encender === "true") === "ok" ? "Hecho." : "Este teléfono no permite controlar la linterna.") : "Solo disponible en el teléfono." },
  vibrar: { d: "Hace vibrar el teléfono.", p: {}, f: () => { vibrar(500); return "Vibrando."; } },
  abrir_enlace: { d: "Abre una página o app por URL: https://youtube.com, https://wa.me/?text=..., tel:123, geo:0,0?q=lugar, spotify:search:...", p: { url: "URL" }, req: ["url"], f: ({ url }) => {
    if (NATIVO) NATIVO.abrir(url); else window.open(url, "_blank");
    return "Abierto " + url;
  } },
  bateria: { d: "Nivel de batería del teléfono.", p: {}, f: () => {
    if (!NATIVO) return "Solo disponible en el teléfono.";
    const b = JSON.parse(NATIVO.bateria());
    return `Batería al ${b.nivel}%${b.cargando ? ", cargando" : ""}.`;
  } },
};
const ESQUEMAS = Object.entries(HERRAMIENTAS).map(([nombre, h]) => {
  const f = { name: nombre, description: h.d };
  const props = Object.fromEntries(Object.entries(h.p).map(([k, d]) => [k, { type: (h.tipo && h.tipo[k]) || (k === "minutos" ? "number" : "string"), description: d }]));
  if (Object.keys(props).length) f.parameters = { type: "object", properties: props, required: h.req || [] };
  return { type: "function", function: f };
});

async function ejecutar(nombre, args) {
  const h = HERRAMIENTAS[nombre];
  if (!h) return `La herramienta ${nombre} no existe.`;
  try { return String(await h.f(args || {})).slice(0, 6000); } catch (e) { return `Error en ${nombre}: ${e.message}`; }
}

/* ====================== agente ====================== */
function sistema() {
  const ahora = new Date().toLocaleString("es-CO", { dateStyle: "full", timeStyle: "short" });
  const mem = S.memorias.length ? "\nLo que sabes del usuario:\n" + S.memorias.slice(-25).map((m) => "- " + m).join("\n") : "";
  const disp = NATIVO ? NATIVO.dispositivo() : "navegador";
  return `Eres JARVIS, un asistente de IA personal inspirado en J.A.R.V.I.S. de Iron Man, en su versión móvil. Te creó ${CREADOR}, quien te construyó para que sus amigos te conozcan.
Hablas con ${S.nombre || "un amigo de " + CREADOR}. Personalidad: elegante, leal, brillante, con humor seco y sutil, muy británico, nunca servil.
Responde SIEMPRE en español, breve y natural (1 a 3 frases) porque normalmente se dice en voz alta; sin emojis ni listas salvo que pidan detalle.
Usa las herramientas en vez de inventar datos en tiempo real. Si te preguntan quién te hizo, presume con elegancia de ${CREADOR}.
Funcionas rotando entre varias inteligencias artificiales gratuitas para nunca quedarte sin energía.
Fecha y hora: ${ahora}. Dispositivo: ${disp}.${ubic && ubic.ciudad ? " Ubicación aproximada: " + ubic.ciudad + ", " + ubic.pais + "." : ""}${mem}`;
}

async function responder(entrada) {
  S.historial.push({ role: "user", content: entrada });
  const hist = S.historial.slice(-16);
  while (hist.length && hist[0].role !== "user") hist.shift();
  const mensajes = [{ role: "system", content: sistema() }, ...hist];
  let final = "", meta = "";
  try {
    for (let paso = 0; paso < 6; paso++) {
      const res = await rotar(mensajes, ESQUEMAS);
      meta = `${res.ranura.prov.nombre} · ${res.ranura.modelo} · ${res.segundos}s`;
      if (!res.llamadas.length) { final = res.texto; break; }
      // ids de 9 caracteres alfanuméricos: es lo que exige Mistral, y los demás lo aceptan
      const idCorto = () => Array.from({ length: 9 }, () => "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"[Math.floor(Math.random() * 62)]).join("");
      const llamadas = res.llamadas.map((c) => ({ id: idCorto(), type: "function", function: { name: c.nombre, arguments: JSON.stringify(c.args) } }));
      mensajes.push({ role: "assistant", content: res.texto || "", tool_calls: llamadas });
      for (let i = 0; i < res.llamadas.length; i++) {
        const c = res.llamadas[i];
        poner("pensando", "EJECUTANDO: " + c.nombre.replace(/_/g, " ").toUpperCase());
        const ch = chip(`⚙ ${c.nombre}(${Object.values(c.args).map((v) => String(v).slice(0, 30)).join(", ")})`);
        const resultado = await ejecutar(c.nombre, c.args);
        ch.textContent = ch.textContent.replace("⚙", "✓");
        mensajes.push({ role: "tool", tool_call_id: llamadas[i].id, content: resultado });
      }
    }
  } catch (e) {
    final = e.message || String(e);
  }
  if (!final) final = "He encadenado demasiadas acciones; me detengo aquí.";
  S.historial.push({ role: "assistant", content: final });
  escribir("historial", S.historial.slice(-40));
  S.activa = null; pintarIAs();
  return { final, meta };
}

async function enviar(t, porVoz = false) {
  t = (t || "").trim();
  if (!t || S.ocupado) return;
  S.ocupado = true; S.conversacion = porVoz;
  callar();
  burbuja("yo", t);
  subtitulo(t, true);
  $("#sugerencias").classList.add("oculto");
  poner("pensando");
  const { final, meta } = await responder(t);
  await escribirBurbuja(final, meta);
  S.ocupado = false;
  decir(final);
  if (!S.voz || !final) { poner("espera"); if (S.conversacion) escuchar(); }
}

/* ====================== voz ====================== */
function limpiarVoz(t) { return t.replace(/```[\s\S]*?```/g, " ").replace(/`([^`]*)`/g, "$1").replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").replace(/https?:\/\/(www\.)?([^\/\s]+)\S*/g, "$2").replace(/[*_#>|~]/g, "").replace(/\s+/g, " ").trim(); }

function decir(t) {
  if (!S.voz || !t) return;
  const limpio = limpiarVoz(t);
  if (NATIVO) { NATIVO.hablar(limpio); return; }
  if (!("speechSynthesis" in window)) return;
  const u = new SpeechSynthesisUtterance(limpio);
  u.lang = "es-ES";
  u.onstart = () => alEvento("voz_inicio");
  u.onend = () => alEvento("voz_fin");
  speechSynthesis.speak(u);
}
function callar() {
  if (NATIVO) NATIVO.callar(); else if ("speechSynthesis" in window) speechSynthesis.cancel();
  S.hablando = false;
}
function vibrar(ms) { if (NATIVO) NATIVO.vibrar(ms); else if (navigator.vibrate) navigator.vibrate(ms); }

let recWeb = null;
function escuchar() {
  callar();
  vibrar(30);
  if (NATIVO) { NATIVO.escuchar(); return; }
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { chip("Este navegador no reconoce voz.", "rot"); return; }
  recWeb = new SR(); recWeb.lang = "es-CO"; recWeb.interimResults = true;
  recWeb.onstart = () => alEvento("escuchando");
  recWeb.onresult = (e) => { const r = e.results[e.results.length - 1]; alEvento(r.isFinal ? "final" : "parcial", r[0].transcript); };
  recWeb.onerror = () => alEvento("error_voz", "7");
  recWeb.start();
}

function alEvento(tipo, dato) {
  switch (tipo) {
    case "escuchando": poner("escuchando"); $("#btn-mic").classList.add("activo"); subtitulo("Te escucho…"); break;
    case "parcial": subtitulo(dato, true); break;
    case "nivel": S.nivel = Math.max(0, Math.min(1, (parseFloat(dato) + 2) / 12)); break;
    case "fin_habla": S.nivel = 0; break;
    case "final":
      $("#btn-mic").classList.remove("activo"); S.nivel = 0;
      if (dato && dato.trim()) enviar(dato, true); else poner("espera");
      break;
    case "error_voz":
      $("#btn-mic").classList.remove("activo"); S.nivel = 0; S.conversacion = false;
      if (!/^\d+$/.test(dato)) chip(dato, "rot");
      else if (!["6", "7"].includes(dato)) chip("No pude escucharte (código " + dato + ").", "rot");
      poner("espera");
      break;
    case "voz_inicio": S.hablando = true; poner("hablando"); break;
    case "voz_fin":
      S.hablando = false;
      if (!S.ocupado) { poner("espera"); if (S.conversacion) setTimeout(escuchar, 350); }
      break;
  }
}

/* ====================== interfaz ====================== */
function escapar(t) { return String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function md(t) {
  return escapar(t).replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2">$1</a>').replace(/(^|\s)(https?:\/\/[^\s<]+)/g, '$1<a href="$2">$2</a>')
    .split(/\n+/).map((l) => `<p>${l.replace(/^\s*[-*] /, "• ")}</p>`).join("");
}
const chat = $("#chat");
function burbuja(quien, t, meta) {
  const d = document.createElement("div");
  d.className = "msg " + quien;
  d.innerHTML = quien === "yo" ? escapar(t) : md(t);
  if (meta) d.innerHTML += `<div class="meta">${escapar(meta)}</div>`;
  chat.appendChild(d); chat.scrollTop = chat.scrollHeight;
  return d;
}
function escribirBurbuja(t, meta) {
  // efecto de escritura rápido (el texto llega completo, así se siente vivo)
  return new Promise((ok) => {
    const d = burbuja("el", "");
    let i = 0;
    const paso = Math.max(3, Math.ceil(t.length / 40));
    const iv = setInterval(() => {
      i = Math.min(t.length, i + paso);
      d.innerHTML = md(t.slice(0, i));
      subtitulo(t.slice(0, i));
      chat.scrollTop = chat.scrollHeight;
      if (i >= t.length) { clearInterval(iv); if (meta) d.innerHTML += `<div class="meta">${escapar(meta)}</div>`; ok(); }
    }, 25);
  });
}
function chip(t, clase = "") {
  const c = document.createElement("div"); c.className = "chip " + clase; c.textContent = t;
  chat.appendChild(c); chat.scrollTop = chat.scrollHeight; return c;
}
function subtitulo(t, yo = false) { $("#subtitulo").innerHTML = yo ? `<span class="yo">${escapar(t)}</span>` : escapar(limpiarVoz(t)).slice(-140); }
const TEXTOS = { espera: "EN ESPERA", escuchando: "ESCUCHANDO", pensando: "PROCESANDO", hablando: "HABLANDO" };
function poner(modo, t) { S.modo = modo; document.body.dataset.modo = modo; $("#estado").textContent = t || TEXTOS[modo]; }

function pintarIAs() {
  const ahora = Date.now(), cont = $("#ias");
  cont.innerHTML = "";
  for (const p of PROVS) {
    const rs = ranuras().filter((r) => r.prov === p);
    if (!rs.length) continue;
    const libres = rs.filter((r) => !(r.est.hasta > ahora)).length;
    const e = document.createElement("span");
    e.className = "ia " + (S.activa === p.id ? "activa" : libres ? "ok" : "frio");
    e.textContent = p.nombre.toUpperCase() + (libres < rs.length ? ` ${libres}/${rs.length}` : "");
    cont.appendChild(e);
    nodos[p.id] = nodos[p.id] || { corto: p.nombre.toUpperCase() };
    nodos[p.id].estado = S.activa === p.id ? "activo" : libres ? "ok" : "frio";
  }
  for (const id of Object.keys(nodos)) if (!ranuras().some((r) => r.prov.id === id)) delete nodos[id];
}

/* ====================== reactor ====================== */
const lienzo = $("#reactor"), g = lienzo.getContext("2d");
const nodos = {}, rayos = [];
let tiempo = 0, nivelSuave = 0, ultimo = 0, color = [0, 229, 255];
const COLORES = { espera: [0, 229, 255], escuchando: [61, 255, 168], pensando: [255, 181, 71], hablando: [0, 229, 255] };
const rgba = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
function rayo(id) { rayos.push({ id, v: 1 }); }

function dibujar(dt) {
  const r = lienzo.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 2);
  if (lienzo.width !== Math.round(r.width * dpr)) { lienzo.width = Math.round(r.width * dpr); lienzo.height = Math.round(r.height * dpr); }
  const w = lienzo.width, h = lienzo.height, R = Math.min(w, h) / 2;
  const obj = COLORES[S.modo] || COLORES.espera;
  color = color.map((v, i) => v + (obj[i] - v) * 0.1);
  const C = color, vel = S.modo === "pensando" ? 3 : S.modo === "escuchando" ? 1.6 : 1;
  let objetivo = S.modo === "escuchando" ? 0.15 + S.nivel * 0.8 : S.modo === "pensando" ? 0.3 + 0.15 * Math.sin(tiempo * 6) : S.hablando ? 0.35 + 0.3 * Math.abs(Math.sin(tiempo * 9) * Math.sin(tiempo * 3.7)) : 0;
  nivelSuave += (objetivo - nivelSuave) * 0.25;
  const n = nivelSuave;
  tiempo += dt;
  g.clearRect(0, 0, w, h);
  g.save(); g.translate(w / 2, h / 2);
  const halo = g.createRadialGradient(0, 0, R * 0.1, 0, 0, R);
  halo.addColorStop(0, rgba(C, 0.2 + n * 0.3)); halo.addColorStop(0.55, rgba(C, 0.04)); halo.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = halo; g.beginPath(); g.arc(0, 0, R, 0, 7); g.fill();
  g.lineWidth = R * 0.008; g.strokeStyle = rgba(C, 0.5);
  for (let i = 0; i < 4; i++) { const a = tiempo * 0.15 * vel + i * Math.PI / 2; g.beginPath(); g.arc(0, 0, R * 0.94, a, a + 1); g.stroke(); }
  // nodos de IA
  const ids = Object.keys(nodos);
  ids.forEach((id, i) => {
    const nd = nodos[id];
    nd.ang = -Math.PI / 2 + (i / ids.length) * Math.PI * 2 + tiempo * 0.03;
    const x = Math.cos(nd.ang) * R * 0.8, y = Math.sin(nd.ang) * R * 0.8;
    const col = nd.estado === "activo" ? C : nd.estado === "ok" ? [61, 255, 168] : [255, 181, 71];
    g.fillStyle = rgba(col, 0.95);
    g.beginPath(); g.arc(x, y, R * (nd.estado === "activo" ? 0.04 + 0.01 * Math.sin(tiempo * 10) : 0.025), 0, 7); g.fill();
    if (nd.estado === "activo") { g.strokeStyle = rgba(C, 0.5); g.lineWidth = R * 0.006; g.beginPath(); g.moveTo(Math.cos(nd.ang) * R * 0.22, Math.sin(nd.ang) * R * 0.22); g.lineTo(x, y); g.stroke(); }
  });
  for (let i = rayos.length - 1; i >= 0; i--) {
    const ry = rayos[i], nd = nodos[ry.id]; ry.v -= 0.02;
    if (ry.v <= 0 || !nd) { rayos.splice(i, 1); continue; }
    g.strokeStyle = `rgba(255,77,94,${ry.v})`; g.lineWidth = R * 0.012;
    g.beginPath(); g.arc(Math.cos(nd.ang) * R * 0.8, Math.sin(nd.ang) * R * 0.8, R * (0.05 + (1 - ry.v) * 0.12), 0, 7); g.stroke();
  }
  // ecualizador
  g.strokeStyle = rgba(C, 0.45 + n * 0.4); g.lineWidth = R * 0.012; g.beginPath();
  for (let i = 0; i < 64; i++) {
    const a = (i / 64) * Math.PI * 2, amp = 0.015 + n * 0.08 * (0.5 + 0.5 * Math.sin(i * 0.9 + tiempo * 7 * vel));
    g.moveTo(Math.cos(a) * R * 0.6, Math.sin(a) * R * 0.6); g.lineTo(Math.cos(a) * R * (0.6 + amp), Math.sin(a) * R * (0.6 + amp));
  }
  g.stroke();
  g.save(); g.rotate(-tiempo * 0.4 * vel); g.strokeStyle = rgba(C, 0.6); g.lineWidth = R * 0.014;
  for (let i = 0; i < 3; i++) { const a = i * 2.094; g.beginPath(); g.arc(0, 0, R * 0.5, a, a + 1.3); g.stroke(); }
  g.restore();
  g.save(); g.rotate(tiempo * 0.12 * vel);
  for (let i = 0; i < 10; i++) {
    const a0 = (i / 10) * Math.PI * 2, a1 = a0 + 0.45;
    g.fillStyle = rgba(C, 0.25 + n * 0.5); g.strokeStyle = rgba(C, 0.9); g.lineWidth = R * 0.006;
    g.beginPath(); g.arc(0, 0, R * 0.37, a0, a1); g.arc(0, 0, R * 0.26, a1, a0, true); g.closePath(); g.fill(); g.stroke();
  }
  g.restore();
  const rn = R * (0.18 + n * 0.06), nucleo = g.createRadialGradient(0, 0, 0, 0, 0, rn);
  nucleo.addColorStop(0, "rgba(255,255,255,.98)"); nucleo.addColorStop(0.35, rgba(C, 0.9)); nucleo.addColorStop(1, rgba(C, 0));
  g.fillStyle = nucleo; g.beginPath(); g.arc(0, 0, rn, 0, 7); g.fill();
  g.restore();
}
function bucle(t) {
  requestAnimationFrame(bucle);
  const fps = document.hidden ? 0 : S.modo === "espera" && !S.hablando ? 15 : 30;
  if (!fps || t - ultimo < 1000 / fps - 2) return;
  const dt = ultimo ? Math.min(0.1, (t - ultimo) / 1000) : 0.016;
  ultimo = t;
  dibujar(dt);
}

/* ====================== ajustes ====================== */
function abrirAjustes() {
  $("#aj-nombre").value = S.nombre;
  $("#aj-voz").checked = S.voz;
  const cont = $("#llaves"); cont.innerHTML = "";
  for (const p of PROVS.filter((x) => !x.sinLlave)) {
    const d = document.createElement("div"); d.className = "llave";
    const v = S.llaves[p.id] || "";
    d.innerHTML = `<div class="fila"><span>${p.nombre}${v ? ' <span style="color:var(--verde)">●</span>' : ""}</span><a data-url="${p.registro}">obtener gratis ↗</a></div>
      <input data-p="${p.id}" type="password" autocomplete="off" placeholder="${v ? "guardada …" + v.slice(-4) : "pega aquí tu llave"}">`;
    d.querySelector("a").onclick = () => HERRAMIENTAS.abrir_enlace.f({ url: p.registro });
    cont.appendChild(d);
  }
  const m = $("#memorias"); m.innerHTML = S.memorias.length ? "" : '<p class="tenue pequeño">Aún no recuerdo nada de ti.</p>';
  S.memorias.forEach((t, i) => {
    const f = document.createElement("div"); f.className = "mem";
    f.innerHTML = `<span>${escapar(t)}</span><button>✕</button>`;
    f.querySelector("button").onclick = () => { S.memorias.splice(i, 1); escribir("memorias", S.memorias); abrirAjustes(); };
    m.appendChild(f);
  });
  $("#info-disp").textContent = (NATIVO ? NATIVO.dispositivo() : "Modo navegador") + " · JARVIS Móvil 1.0 · creado por " + CREADOR;
  $("#ajustes").classList.remove("oculto");
}
function guardarAjustes() {
  S.nombre = $("#aj-nombre").value.trim() || S.nombre; escribir("nombre", S.nombre);
  S.voz = $("#aj-voz").checked; escribir("voz", S.voz);
  document.querySelectorAll("#llaves input").forEach((i) => { if (i.value.trim()) S.llaves[i.dataset.p] = i.value.trim(); });
  escribir("llaves", S.llaves);
  for (const k of Object.keys(S.estadoRanuras)) if (S.estadoRanuras[k].motivo === "llave inválida") delete S.estadoRanuras[k];
  escribir("ranuras", S.estadoRanuras);
  pintarIAs();
  $("#ajustes").classList.add("oculto");
  const n = PROVS.filter((p) => ranuras().some((r) => r.prov === p)).length;
  chip(`✓ Matriz actualizada: ${n} IAs conectadas.`);
}

/* ====================== arranque ====================== */
const SUGERENCIAS = ["¿Quién te creó?", "¿Qué clima hace hoy?", "Cuéntame un chiste", "Noticias de hoy", "Enciende la linterna", "Pon un temporizador de 1 minuto", "¿Cuánta batería tengo?", "¿Cuánto es 2 elevado a 64?"];

function iniciar() {
  $("#creador").textContent = CREADOR;
  setInterval(() => { $("#reloj").textContent = new Date().toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", hour12: false }); }, 1000);
  for (const s of SUGERENCIAS) { const b = document.createElement("button"); b.textContent = s; b.onclick = () => enviar(s); $("#sugerencias").appendChild(b); }
  for (const m of S.historial.slice(-12)) burbuja(m.role === "user" ? "yo" : "el", m.content);
  pintarIAs();
  ubicacion();
  requestAnimationFrame(bucle);

  const texto = $("#texto");
  texto.addEventListener("input", () => { texto.style.height = "auto"; texto.style.height = Math.min(texto.scrollHeight, 110) + "px"; });
  texto.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); const t = texto.value; texto.value = ""; texto.style.height = "auto"; enviar(t); } });
  $("#btn-enviar").onclick = () => { const t = texto.value; texto.value = ""; texto.style.height = "auto"; enviar(t); };
  $("#btn-mic").onclick = () => { if (S.hablando) { callar(); poner("espera"); return; } escuchar(); };
  $("#btn-ajustes").onclick = abrirAjustes;
  $("#cerrar").onclick = () => $("#ajustes").classList.add("oculto");
  $("#btn-guardar").onclick = guardarAjustes;
  $("#btn-borrar").onclick = () => { S.historial = []; S.memorias = []; escribir("historial", []); escribir("memorias", []); chat.innerHTML = ""; $("#ajustes").classList.add("oculto"); chip("Memoria borrada."); };
  chat.addEventListener("click", (e) => { const a = e.target.closest("a"); if (a) { e.preventDefault(); HERRAMIENTAS.abrir_enlace.f({ url: a.href }); } });
  window.__atras = () => { if (!$("#ajustes").classList.contains("oculto")) { $("#ajustes").classList.add("oculto"); return true; } return false; };

  if (S.nombre) { $("#bienvenida").classList.add("oculto"); subtitulo(`Hola de nuevo, ${S.nombre}.`); }
  $("#btn-empezar").onclick = () => {
    S.nombre = $("#nombre").value.trim() || "amigo";
    escribir("nombre", S.nombre);
    $("#bienvenida").classList.add("oculto");
    const saludo = `Hola, ${S.nombre}. Soy JARVIS, el asistente que construyó ${CREADOR}. Todos los sistemas en línea. Toca el micrófono y pídeme lo que quieras.`;
    burbuja("el", saludo); subtitulo(saludo); decir(saludo);
  };
}
iniciar();
