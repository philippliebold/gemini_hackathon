/* Canvas shell: WebSocket client, camera (pan/zoom), block lifecycle, links.
 * Reads CONTRACT.md. Never reaches into backend code. */

const WS_URL = new URLSearchParams(location.search).get("ws")
  || "ws://127.0.0.1:8765";

const els = {
  world: document.getElementById("world"),
  blocks: document.getElementById("blocks"),
  links: document.getElementById("links"),
  empty: document.getElementById("empty"),
  dot: document.getElementById("dot"),
  state: document.getElementById("state"),
  transcript: document.getElementById("transcript"),
};

const state = {
  blocks: new Map(),   // id -> { payload, el }
  links: new Map(),    // id -> payload
  cam: { x: 0, y: 0, z: 1 },
  lastSeq: 0,
  ws: null,
};

/* ---------- camera ---------- */
function applyCam(animate = true) {
  const { x, y, z } = state.cam;
  els.world.classList.toggle("dragging", !animate);
  els.world.style.transform = `translate(${x}px, ${y}px) scale(${z})`;
}

function fit(ids = [], padding = 80) {
  const list = ids.length
    ? ids.map((i) => state.blocks.get(i)).filter(Boolean)
    : [...state.blocks.values()];
  if (!list.length) return;
  const b = list.map((o) => o.payload);
  const minX = Math.min(...b.map((p) => p.x));
  const minY = Math.min(...b.map((p) => p.y));
  const maxX = Math.max(...b.map((p) => p.x + (p.w || 440)));
  const maxY = Math.max(...b.map((p) => p.y + (p.h || 280)));
  const w = maxX - minX + padding * 2;
  const h = maxY - minY + padding * 2;
  const z = Math.min(innerWidth / w, innerHeight / h, 1.15);
  state.cam = {
    z,
    x: -((minX + maxX) / 2) * z,
    y: -((minY + maxY) / 2) * z,
  };
  applyCam();
}

/* ---------- blocks ---------- */
function addBlock(p) {
  removeBlock(p.id);
  const el = document.createElement("div");
  el.className = `block enter-${p.enter || "pop"} accent-${(p.data && p.data.accent) || "slate"}`;
  el.style.left = `${p.x}px`;
  el.style.top = `${p.y}px`;
  el.style.width = `${p.w || 440}px`;
  el.dataset.id = p.id;
  el.innerHTML = window.CoPresenterBlocks.renderBlock(p.type, p.data);
  els.blocks.appendChild(el);
  state.blocks.set(p.id, { payload: p, el });
  if (p.type === "diagram") window.CoPresenterBlocks.hydrateMermaid(el);
  if (p.type === "chart") window.CoPresenterBlocks.hydrateCharts(el);
  els.empty.style.opacity = "0";
  requestAnimationFrame(drawLinks);
}

function updateBlock(p) {
  const b = state.blocks.get(p.id);
  if (!b) return;
  if (p.data) b.payload.data = { ...b.payload.data, ...p.data };
  for (const k of ["x", "y", "w", "h"]) if (p[k] != null) b.payload[k] = p[k];
  b.el.style.left = `${b.payload.x}px`;
  b.el.style.top = `${b.payload.y}px`;
  b.el.style.width = `${b.payload.w || 440}px`;
  if (p.data) {
    b.el.innerHTML = window.CoPresenterBlocks.renderBlock(b.payload.type, b.payload.data);
    if (b.payload.type === "diagram") window.CoPresenterBlocks.hydrateMermaid(b.el);
    if (b.payload.type === "chart") window.CoPresenterBlocks.hydrateCharts(b.el);
  }
  drawLinks();
}

/* Move blocks to new positions as one choreographed motion instead of a jump.
   The audience is watching the screen with nobody narrating it, so a reflow
   has to read as intentional. GSAP Flip measures before/after and tweens the
   delta; without it, re-layout looks like the page broke. */
function reflow(moves) {
  const els = moves.map((m) => state.blocks.get(m.id)).filter(Boolean).map((b) => b.el);
  const useFlip = window.Flip && els.length;
  const st = useFlip ? Flip.getState(els) : null;

  for (const m of moves) {
    const b = state.blocks.get(m.id);
    if (!b) continue;
    if (m.x != null) b.payload.x = m.x;
    if (m.y != null) b.payload.y = m.y;
    b.el.style.left = `${b.payload.x}px`;
    b.el.style.top = `${b.payload.y}px`;
  }

  if (useFlip) {
    Flip.from(st, {
      duration: 0.6, ease: "power2.inOut", stagger: 0.03,
      onUpdate: drawLinks, onComplete: drawLinks,
    });
  } else {
    drawLinks();
  }
}

/* One block becomes the subject. Elevation does the pointing, because the
   presenter is facing the room and cannot point at the screen themselves. */
function focusBlock(id) {
  for (const [, b] of state.blocks) b.el.classList.remove("is-focus");
  const b = state.blocks.get(id);
  if (b) b.el.classList.add("is-focus");
}

function removeBlock(id) {
  const b = state.blocks.get(id);
  if (!b) return;
  b.el.style.opacity = "0";
  b.el.style.transform = "scale(.9)";
  setTimeout(() => b.el.remove(), 300);
  state.blocks.delete(id);
  drawLinks();
}

function clearCanvas() {
  state.blocks.clear();
  state.links.clear();
  els.blocks.innerHTML = "";
  els.links.innerHTML = "";
  els.empty.style.opacity = "1";
  state.cam = { x: 0, y: 0, z: 1 };
  applyCam();
}

/* ---------- links ---------- */
function centre(id) {
  const b = state.blocks.get(id);
  if (!b) return null;
  const p = b.payload;
  return { x: p.x + (p.w || 440) / 2, y: p.y + (b.el.offsetHeight || p.h || 280) / 2 };
}

function drawLinks() {
  const parts = [];
  for (const l of state.links.values()) {
    const a = centre(l.from), z = centre(l.to);
    if (!a || !z) continue;
    const mx = (a.x + z.x) / 2;
    parts.push(`<path d="M ${a.x} ${a.y} C ${mx} ${a.y}, ${mx} ${z.y}, ${z.x} ${z.y}"
      ${l.style === "dashed" ? 'stroke-dasharray="6 6"' : ""}/>`);
    if (l.label) parts.push(`<text x="${mx}" y="${(a.y + z.y) / 2 - 8}" text-anchor="middle">${l.label}</text>`);
  }
  els.links.innerHTML = parts.join("");
}

/* ---------- status ---------- */
/* The four Core Dots ARE the status indicator: Google's brand exists as
   motion in voice-first interfaces. State is carried by how they move,
   never by recoloring them — the four colors are fixed. */
const DOT = { idle: "muted", listening: "", thinking: "thinking",
              drawing: "thinking", error: "muted" };

function setStatus(p) {
  els.state.textContent = p.state;
  els.dot.className = `core-dots ${DOT[p.state] ?? ""}`;
  if (p.transcript !== undefined) els.transcript.textContent = p.transcript || "";
}

/* ---------- dispatch ---------- */
const OPS = {
  "block.add": addBlock,
  "block.update": updateBlock,
  "block.remove": (p) => removeBlock(p.id),
  "link.add": (p) => { state.links.set(p.id, p); drawLinks(); },
  "link.remove": (p) => { state.links.delete(p.id); drawLinks(); },
  "canvas.focus": (p) => fit(p.ids || [], p.padding ?? 80),
  "canvas.clear": clearCanvas,
  "canvas.reflow": (p) => reflow(p.moves || []),
  "block.focus": (p) => focusBlock(p.id),
  "status": setStatus,
};

function handle(frame) {
  if (frame.v !== 1) return console.warn("schema version mismatch", frame.v);
  if (frame.seq <= state.lastSeq) return;          // drop replays/out-of-order
  state.lastSeq = frame.seq;
  const fn = OPS[frame.op];
  if (!fn) return console.warn("unknown op", frame.op);
  try { fn(frame.payload); } catch (e) { console.error(frame.op, e); }
}

/* ---------- socket ---------- */
function connect() {
  const ws = new WebSocket(WS_URL);
  state.ws = ws;
  ws.onopen = () => setStatus({ state: "idle" });
  ws.onmessage = (e) => handle(JSON.parse(e.data));
  ws.onclose = () => {
    setStatus({ state: "error", transcript: "reconnecting…" });
    state.lastSeq = 0;
    setTimeout(connect, 1000);
  };
  ws.onerror = () => ws.close();
}

function sendPresenter(action) {
  if (state.ws && state.ws.readyState === 1)
    state.ws.send(JSON.stringify({ v: 1, cmd: "presenter", action }));
}

/* ---------- input ---------- */
let drag = null;
addEventListener("mousedown", (e) => {
  drag = { mx: e.clientX, my: e.clientY, cx: state.cam.x, cy: state.cam.y };
});
addEventListener("mousemove", (e) => {
  if (!drag) return;
  state.cam.x = drag.cx + (e.clientX - drag.mx);
  state.cam.y = drag.cy + (e.clientY - drag.my);
  applyCam(false);
});
addEventListener("mouseup", () => { drag = null; els.world.classList.remove("dragging"); });
addEventListener("wheel", (e) => {
  e.preventDefault();
  state.cam.z = Math.min(2, Math.max(0.15, state.cam.z * (e.deltaY > 0 ? 0.92 : 1.08)));
  applyCam(false);
}, { passive: false });
addEventListener("keydown", (e) => {
  if (e.key === "f" || e.key === "F") fit();
  if (e.key === "c" || e.key === "C") sendPresenter("clear");
  if (e.key === "u" || e.key === "U") sendPresenter("undo");
});

addEventListener("mermaid-ready", () => {
  for (const b of state.blocks.values())
    if (b.payload.type === "diagram") window.CoPresenterBlocks.hydrateMermaid(b.el);
});

applyCam();
connect();
