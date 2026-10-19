// Visor 3D de JARVIS: muestra el modelo, deja elegir piezas, copias y ajustes, y planea o manda a imprimir.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const CAMA = 256; // mm (A1, P1S y X1 Carbon)
const COLORES = ["cualquiera", "negro", "blanco", "gris", "plateado", "rojo", "naranja", "amarillo", "verde", "verde claro", "azul",
  "azul claro", "celeste", "morado", "lila", "rosa", "café", "beige", "dorado"];
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function herramienta(nombre, args) {
  const r = await fetch(`/api/herramienta/${nombre}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args) });
  if (!r.ok) throw new Error(r.status === 401 ? "sin acceso" : `error ${r.status}`);
  return (await r.json()).texto;
}

function montar(caja, url, nombreArchivo) {
  caja.innerHTML = `
    <div class="v3d-titulo">🧊 ${esc(nombreArchivo.replace(/\.glb$/, ""))}</div>
    <div class="v3d-lienzo"><div class="v3d-cargando">Cargando modelo…</div></div>
    <div class="v3d-ayuda">Arrastra para girar · rueda o pellizco para acercar · toca una pieza para elegirla</div>
    <div class="v3d-arbol"></div>
    <div class="v3d-ajustes">
      <label>Copias de todo <input type="number" name="copias" min="1" max="500" value="1"></label>
      <label>Perfil <select name="perfil"><option value="basico">Básico</option><option value="resistente">Resistente</option>
        <option value="rapido">Rápido</option><option value="detalle">Detalle</option></select></label>
      <label>Color <select name="color">${COLORES.map((c) => `<option>${c}</option>`).join("")}</select></label>
      <label>Material <select name="material"><option>PLA</option><option>PETG</option><option>ABS</option><option>ASA</option><option>TPU</option></select></label>
      <details><summary>Ajustes avanzados</summary>
        <label>Paredes <input type="number" name="paredes" min="1" max="20" placeholder="perfil"></label>
        <label>Relleno % <input type="number" name="relleno" min="0" max="100" placeholder="perfil"></label>
        <label>Patrón <select name="patron"><option value="">perfil</option><option>gyroid</option><option value="rejilla">rejilla</option>
          <option value="cubico">cúbico</option><option value="panal">panal</option><option value="lineas">líneas</option><option value="rayo">rayo</option></select></label>
        <label>Soportes <select name="soportes"><option value="">perfil (no)</option><option value="no">no</option><option value="auto">árbol (auto)</option>
          <option value="normal">normal</option><option value="cama">solo desde la cama</option></select></label>
        <label>Capa (mm) <input type="number" name="altura_capa" min="0.06" max="0.32" step="0.02" placeholder="0.20"></label>
        <label>Borde <select name="borde"><option value="">auto</option><option value="si">sí</option><option value="no">no</option></select></label>
        <label>Impresora <input type="text" name="impresora" placeholder="automática"></label>
        <label class="v3d-check"><input type="checkbox" name="orientar" checked> Orientar cada pieza automáticamente</label>
      </details>
    </div>
    <div class="v3d-botones">
      <button data-accion="impresoras_3d">Ver impresoras</button>
      <button data-accion="impresion_3d_planear">Planear</button>
      <button data-accion="imprimir_3d" class="v3d-imprimir">Imprimir</button>
    </div>
    <pre class="v3d-salida" hidden></pre>`;
  const lienzo = caja.querySelector(".v3d-lienzo");
  const salida = caja.querySelector(".v3d-salida");

  const escena = new THREE.Scene();
  escena.background = new THREE.Color(0x070d16);
  const camara = new THREE.PerspectiveCamera(40, 1, 1, 5000);
  const render = new THREE.WebGLRenderer({ antialias: true });
  render.setPixelRatio(Math.min(devicePixelRatio, 2));
  lienzo.appendChild(render.domElement);
  const control = new OrbitControls(camara, render.domElement);
  control.enableDamping = true;
  escena.add(new THREE.HemisphereLight(0xcfe8ff, 0x1a2230, 1.6));
  const sol = new THREE.DirectionalLight(0xffffff, 1.4);
  sol.position.set(200, 400, 300);
  escena.add(sol);
  const rejilla = new THREE.GridHelper(CAMA, 16, 0x1f6f8b, 0x16303f);
  escena.add(rejilla);

  const matSi = new THREE.MeshStandardMaterial({ color: 0x35d0f0, roughness: 0.55, metalness: 0.05, flatShading: true });
  const matNo = new THREE.MeshStandardMaterial({ color: 0x3a4655, roughness: 0.9, transparent: true, opacity: 0.35, flatShading: true });
  const matSobre = new THREE.MeshStandardMaterial({ color: 0xffc53d, roughness: 0.5, flatShading: true });
  const piezas = new Map(); // id -> {mesh, nombre, grupo, medidas, elegida, copias}
  let sobre = null;

  const ajustar = () => {
    const w = lienzo.clientWidth, h = lienzo.clientHeight;
    render.setSize(w, h, false);
    camara.aspect = w / h;
    camara.updateProjectionMatrix();
  };
  new ResizeObserver(ajustar).observe(lienzo);
  let vivo = true;
  (function ciclo() {
    if (!vivo) return;
    if (!caja.isConnected) { vivo = false; render.dispose(); return; }
    control.update();
    render.render(escena, camara);
    requestAnimationFrame(ciclo);
  })();

  const pintar = () => {
    for (const p of piezas.values()) p.mesh.material = p === sobre ? matSobre : (p.elegida ? matSi : matNo);
    for (const fila of caja.querySelectorAll(".v3d-pieza")) {
      const p = piezas.get(fila.dataset.id);
      fila.querySelector("input[type=checkbox]").checked = p.elegida;
      fila.classList.toggle("sobre", p === sobre);
    }
    for (const g of caja.querySelectorAll(".v3d-grupo")) {
      const ps = [...piezas.values()].filter((p) => p.grupo === g.dataset.id);
      const chk = g.querySelector(":scope > summary input");
      chk.checked = ps.every((p) => p.elegida);
      chk.indeterminate = !chk.checked && ps.some((p) => p.elegida);
    }
  };

  let ruta = "";
  new GLTFLoader().load(url, (gltf) => {
    lienzo.querySelector(".v3d-cargando").remove();
    ruta = (gltf.asset && gltf.asset.extras && gltf.asset.extras.ruta) || ""; // así el PC sabe qué archivo es aunque se haya reiniciado
    const raiz = gltf.scene;
    raiz.rotation.x = -Math.PI / 2; // las impresoras usan Z hacia arriba
    escena.add(raiz);
    raiz.updateMatrixWorld(true);
    const grupos = [];
    raiz.traverse((o) => {
      if (o.isMesh) {
        o.geometry.computeVertexNormals();
        const ex = o.userData || {};
        piezas.set(ex.id, { mesh: o, id: ex.id, nombre: ex.nombre || o.name, grupo: ex.grupo, medidas: ex.medidas || [], elegida: true, copias: 1 });
        o.userData.pieza = ex.id;
      } else if (o.userData && o.userData.id && o.userData.id.startsWith("g")) grupos.push({ id: o.userData.id, nombre: o.userData.nombre || o.name });
    });
    // centra el modelo sobre la cama y encuadra la cámara
    const caja3 = new THREE.Box3().setFromObject(raiz);
    const centro = caja3.getCenter(new THREE.Vector3());
    raiz.position.x -= centro.x; raiz.position.z -= centro.z; raiz.position.y -= caja3.min.y;
    const tam = caja3.getSize(new THREE.Vector3()).length();
    camara.position.set(tam * 0.9, tam * 0.75, tam * 0.9);
    control.target.set(0, (caja3.max.y - caja3.min.y) / 2, 0);
    rejilla.scale.setScalar(Math.max(1, tam / CAMA));
    ajustar();

    const arbol = caja.querySelector(".v3d-arbol");
    arbol.innerHTML = grupos.map((g) => {
      const ps = [...piezas.values()].filter((p) => p.grupo === g.id);
      const filas = ps.map((p) => `<div class="v3d-pieza" data-id="${p.id}"><label><input type="checkbox" checked> ${esc(p.nombre)}</label>
        <span class="v3d-mm">${p.medidas.map((x) => Math.round(x)).join("×")} mm</span>
        <input type="number" class="v3d-copias" min="0" max="500" value="1" title="copias de esta pieza"></div>`).join("");
      return `<details class="v3d-grupo" data-id="${g.id}" ${grupos.length < 4 ? "open" : ""}><summary><input type="checkbox" checked> ${esc(g.nombre)}
        <span class="v3d-mm">${ps.length} pieza${ps.length > 1 ? "s" : ""}</span></summary>${filas}</details>`;
    }).join("") + `<div class="v3d-todo"><button data-todo="1">Todas</button><button data-todo="0">Ninguna</button></div>`;
    arbol.addEventListener("change", (e) => {
      const fila = e.target.closest(".v3d-pieza");
      if (fila && e.target.type === "checkbox") piezas.get(fila.dataset.id).elegida = e.target.checked;
      if (fila && e.target.classList.contains("v3d-copias")) { const p = piezas.get(fila.dataset.id); p.copias = Math.max(0, +e.target.value || 0); p.elegida = p.copias > 0; }
      const g = e.target.closest("summary") && e.target.closest(".v3d-grupo");
      if (g) for (const p of piezas.values()) if (p.grupo === g.dataset.id) p.elegida = e.target.checked;
      pintar();
    });
    arbol.addEventListener("click", (e) => {
      if (e.target.closest("summary input")) e.stopPropagation();
      const b = e.target.closest("[data-todo]");
      if (b) { for (const p of piezas.values()) p.elegida = b.dataset.todo === "1"; pintar(); }
    });
    arbol.addEventListener("mouseover", (e) => { const f = e.target.closest(".v3d-pieza"); sobre = f ? piezas.get(f.dataset.id) : null; pintar(); });
    arbol.addEventListener("mouseleave", () => { sobre = null; pintar(); });
    pintar();
  }, undefined, () => { lienzo.querySelector(".v3d-cargando").textContent = "No pude cargar el modelo."; });

  // tocar una pieza en el lienzo la elige o la quita (un arrastre para girar no cuenta)
  const rayo = new THREE.Raycaster();
  let inicio = null;
  render.domElement.addEventListener("pointerdown", (e) => { inicio = [e.clientX, e.clientY]; });
  render.domElement.addEventListener("pointerup", (e) => {
    if (!inicio || Math.hypot(e.clientX - inicio[0], e.clientY - inicio[1]) > 6) return;
    const r = render.domElement.getBoundingClientRect();
    rayo.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camara);
    const golpe = rayo.intersectObjects([...piezas.values()].map((p) => p.mesh))[0];
    if (golpe) { const p = piezas.get(golpe.object.userData.pieza); p.elegida = !p.elegida; pintar(); }
  });

  const argumentos = () => {
    const f = (n) => caja.querySelector(`[name=${n}]`);
    const elegidas = [...piezas.values()].filter((p) => p.elegida && p.copias > 0);
    const a = { piezas: elegidas.length === piezas.size && elegidas.every((p) => p.copias === 1) ? "todas" : elegidas.map((p) => `${p.id}:${p.copias}`).join(", "),
      copias: f("copias").value, perfil: f("perfil").value, material: f("material").value, orientar: f("orientar").checked ? "si" : "no" };
    if (ruta) a.archivo = ruta;
    for (const n of ["paredes", "relleno", "patron", "soportes", "altura_capa", "borde", "impresora"]) if (f(n).value) a[n] = f(n).value;
    if (f("color").value !== "cualquiera") a.color = f("color").value;
    return { a, elegidas };
  };

  caja.querySelector(".v3d-botones").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-accion]");
    if (!b) return;
    const { a, elegidas } = argumentos();
    if (b.dataset.accion !== "impresoras_3d" && !elegidas.length) { salida.hidden = false; salida.textContent = "Elige al menos una pieza."; return; }
    if (b.dataset.accion === "imprimir_3d" && !confirm(`¿Mandar a imprimir ${elegidas.length} pieza(s) × ${a.copias}${a.color ? " en " + a.color : ""}?`)) return;
    const botones = caja.querySelectorAll(".v3d-botones button");
    botones.forEach((x) => (x.disabled = true));
    salida.hidden = false;
    salida.textContent = { impresoras_3d: "Revisando impresoras (unos 10 s por impresora)…", impresion_3d_planear: "Orientando, organizando platos y laminando…",
      imprimir_3d: "Laminando y enviando a las impresoras… (JARVIS mueve el ratón en Bambu Studio)" }[b.dataset.accion];
    try { salida.textContent = await herramienta(b.dataset.accion, b.dataset.accion === "impresoras_3d" ? {} : a); }
    catch (err) { salida.textContent = "No se pudo: " + err.message; }
    botones.forEach((x) => (x.disabled = false));
  });
}

window.Visor3D = { montar };
window.dispatchEvent(new Event("visor3d-listo"));
