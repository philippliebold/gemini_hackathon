/* Co-Presenter — scene engine.
 *
 * Same WebSocket contract as before; completely different idea of what to do
 * with it. There is no board and no persistence. A block arriving is a thing
 * appearing on stage; it holds attention, then it leaves. At most MAX_LIVE
 * scenes coexist, and the newest is always the subject.
 *
 * Backend x/y are deliberately IGNORED. Placement is the screen's job, not
 * the model's — a grid of one, two or three slots always composes, whereas
 * model-chosen coordinates only sometimes do.
 */

const Q = new URLSearchParams(location.search);
const WS_URL = Q.get("ws") || "ws://127.0.0.1:8765";
const MAX_LIVE = +(Q.get("max") || 1);   // one subject at a time. Always.
const LIFETIME = +(Q.get("hold") || 26000);  // a scene nobody refreshes retires itself

const el = {
  slots:   document.getElementById("slots"),
  idle:    document.getElementById("idle"),
  dots:    document.getElementById("dots"),
  caption: document.getElementById("caption"),
};

const live = new Map();      // id -> {id, node, payload, timer}

/* ---------- helpers ---------- */

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const ACCENT = { blue: "#4285F4", red: "#EA4335", yellow: "#FBBC04",
                 green: "#34A853", slate: "#4285F4" };

/* Split into words so each lands separately — the line assembles itself the
   way the sentence was said, rather than snapping in whole. */
function words(text, delay = 0, step = 0.055) {
  return String(text ?? "").split(/\s+/).filter(Boolean).map((w, i) =>
    `<span class="w" style="animation-delay:${(delay + i * step).toFixed(2)}s">${esc(w)}</span>`
  ).join(" ");
}

/* ---------- scene renderers ---------- */

const SCENE = {
  hero: (d) => `
    ${d.emoji ? `<div class="t-emoji">${esc(d.emoji)}</div>` : ""}
    <div class="t-hero ${String(d.title || "").length > 22 ? "tight" : ""}">
      ${words(d.title, .12)}
    </div>
    ${d.sub ? `<div class="t-sub">${words(d.sub, .5, .03)}</div>` : ""}`,

  text: (d) => `
    ${d.title ? `<div class="t-hero tight">${words(d.title, .1)}</div>` : ""}
    ${d.body ? `<div class="t-sub">${words(d.body, .4, .028)}</div>` : ""}
    ${(d.bullets || []).length ? `
      <ul class="bullets" style="margin-top:2.4vh">
        ${d.bullets.map((b, i) =>
          `<li style="animation-delay:${(.45 + i * .16).toFixed(2)}s">${esc(b)}</li>`
        ).join("")}
      </ul>` : ""}`,

  stat: (d) => `
    ${d.label ? `<div class="t-kicker">${esc(d.label)}</div>` : ""}
    <div class="t-num">${words(d.value, .1, .07)}</div>
    ${d.delta ? `<div class="t-delta">${esc(d.delta)}</div>` : ""}`,

  /* a defined term, in brackets — the reference deck's signature move */
  term: (d) => `
    <div class="t-bracket">${words(d.term, .12)}</div>
    ${d.sub ? `<div class="t-sub">${words(d.sub, .5, .03)}</div>` : ""}`,

  image: (d) => d.src
    ? `<div class="shot">
         <img src="${esc(d.src)}" alt="${esc(d.alt || "")}"
              onerror="this.dataset.broken=1;this.closest('.shot').classList.add('failed')">
         ${d.caption ? `<div class="cap">${esc(d.caption)}</div>` : ""}
       </div>`
    : `<div class="loading"></div>`,

  /* The fixture ships a YOUR_MAPS_KEY placeholder so no key lands in the
     repo. Pass a real one for a local demo: ?mapskey=... */
  map: (d) => `
    <div class="t-kicker">${esc(d.from || "")} → ${esc(d.to || "")}</div>
    <div class="shot">
      ${d.embed_url
        ? `<iframe src="${esc(String(d.embed_url).replace("YOUR_MAPS_KEY", Q.get("mapskey") || window.MAPS_KEY || "YOUR_MAPS_KEY"))}" style="height:min(48vh,520px)"
             loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>`
        : `<div class="loading" style="aspect-ratio:16/11"></div>`}
    </div>
    ${(d.duration || d.distance) ? `
      <div class="t-sub" style="margin-top:1.6vh">
        <b style="color:#F4F8FF">${esc(d.duration || "")}</b>
        &nbsp;·&nbsp;${esc(d.distance || "")} ${esc(d.mode || "")}
      </div>` : ""}`,

  math: (d) => `
    ${d.title ? `<div class="t-kicker">${esc(d.title)}</div>` : ""}
    <div class="glass"><div class="math-wrap katex-slot"
      data-tex="${esc(d.tex || "")}"><code>${esc(d.tex || "")}</code></div></div>
    ${d.note ? `<div class="t-sub" style="margin-top:1.6vh">${esc(d.note)}</div>` : ""}`,

  chart: (d) => `
    ${d.title ? `<div class="t-kicker">${esc(d.title)}</div>` : ""}
    <div class="glass"><div class="echart-slot"
      style="height:min(42vh,420px)"
      data-spec="${esc(JSON.stringify(d))}"></div></div>`,

  table: (d) => `
    ${d.title ? `<div class="t-kicker">${esc(d.title)}</div>` : ""}
    <div class="glass">
      <table class="rows">
        ${(d.columns || []).length ? `<thead><tr>${
          d.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>` : ""}
        <tbody>${(d.rows || []).map((r) => `<tr>${
          r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody>
      </table>
    </div>`,

  diagram: (d) => `
    ${d.title ? `<div class="t-kicker">${esc(d.title)}</div>` : ""}
    <div class="glass mermaid-slot" data-src="${esc(d.mermaid || "")}"></div>`,

  /* The recap. Apple ends a keynote with every point on one screen at once;
     this is that. Tiles stagger in so the room watches it assemble. */
  summary: (d) => {
    const items = (d.items || []).slice(0, 12);
    const cols = items.length <= 4 ? 2 : items.length <= 9 ? 3 : 4;
    return `
    ${d.title ? `<div class="sum-title">${words(d.title, .05)}</div>` : ""}
    <div class="sum-grid" style="--cols:${cols}">
      ${items.map((it, i) => `
        <div class="sum-tile" style="animation-delay:${(.25 + i * .09).toFixed(2)}s">
          ${it.emoji ? `<div class="sum-emoji">${esc(it.emoji)}</div>` : ""}
          ${it.value ? `<div class="sum-value">${esc(it.value)}</div>` : ""}
          <div class="sum-label">${esc(it.label || "")}</div>
        </div>`).join("")}
    </div>`;
  },

  code: (d) => `
    ${d.title ? `<div class="t-kicker">${esc(d.title)}</div>` : ""}
    <div class="glass"><pre style="margin:0;font-family:var(--font-mono);
      font-size:clamp(11px,.95vw,17px);line-height:1.65;color:#C8DCFB;
      overflow:auto">${esc(d.source || "")}</pre></div>`,
};

function render(type, data) {
  const fn = SCENE[type] || SCENE.text;
  try { return fn(data || {}); }
  catch (e) { return `<div class="t-sub">${esc(type)}</div>`; }
}

/* ---------- lifecycle ---------- */

function retire(id) {
  const s = live.get(id);
  if (!s) return;
  clearTimeout(s.timer);
  live.delete(id);
  s.node.classList.add("out");
  /* Stack it in the SAME grid cell as whatever arrives next, so the outgoing
     and incoming scenes cross-fade in place. (Absolute positioning here
     escaped the stage padding and bled off the left edge.) */
  s.node.style.gridArea = "1 / 1";
  s.node.style.pointerEvents = "none";
  s.node.style.zIndex = "0";
  setTimeout(() => { s.node.remove(); relayout(); }, 780);
  relayout();
}

function relayout() {
  const n = Math.min(live.size, MAX_LIVE);
  el.slots.dataset.n = String(Math.max(n, 1));
  /* newest is the subject; everything else steps back */
  const ids = [...live.keys()];
  ids.forEach((id, i) => {
    const s = live.get(id);
    s.node.classList.toggle("recede", i !== ids.length - 1 && ids.length > 1);
  });
  el.idle.classList.toggle("gone", live.size > 0);
}

function addScene(p) {
  /* Replacing the same id: drop the old node NOW. retire() keeps it in the
     DOM for its exit animation, which would paint both copies on top of
     each other — the doubled-text bug. */
  if (live.has(p.id)) {
    const prev = live.get(p.id);
    clearTimeout(prev.timer);
    prev.node.remove();
    live.delete(p.id);
  }

  /* one subject at a time: whatever is on stage leaves as this arrives */
  while (live.size >= MAX_LIVE) retire(live.keys().next().value);

  const node = document.createElement("div");
  node.className = "scene";
  node.dataset.id = p.id;
  const accent = ACCENT[(p.data && p.data.accent) || "blue"] || ACCENT.blue;
  node.style.setProperty("--accent", accent);
  node.innerHTML = render(p.type, p.data);

  el.slots.appendChild(node);
  live.set(p.id, {
    id: p.id, node, payload: p,
    timer: setTimeout(() => retire(p.id), LIFETIME),
  });

  hydrate(node, p.type);
  relayout();
}

function updateScene(p) {
  const s = live.get(p.id);
  /* A slow tool (image generation runs ~12s) can land long after its scene
     left the stage. Re-adding it here would resurrect a scene the talk has
     moved past AND evict whatever is on screen -- and with no `type` on an
     update payload it would render empty. The moment has passed: drop it. */
  if (!s) {
    if (!p.type) return;
    return addScene(p);
  }
  clearTimeout(s.timer);
  s.timer = setTimeout(() => retire(p.id), LIFETIME);
  if (p.data) {
    s.payload.data = { ...s.payload.data, ...p.data };
    s.node.innerHTML = render(s.payload.type, s.payload.data);
    hydrate(s.node, s.payload.type);
  }
}

function focusScene(id) {
  if (!live.has(id)) return;
  /* promote to subject by moving it last in DOM + map order */
  const s = live.get(id);
  live.delete(id); live.set(id, s);
  el.slots.appendChild(s.node);
  clearTimeout(s.timer);
  s.timer = setTimeout(() => retire(id), LIFETIME);
  relayout();
}

function clearAll() {
  [...live.keys()].forEach(retire);
}

/* ---------- library hydration ---------- */

let themed = false;
function echartsTheme() {
  if (themed || !window.echarts) return themed;
  echarts.registerTheme("stage", {
    color: ["#6FA8FF", "#FF7A6E", "#FFD24D", "#5BD07C"],
    backgroundColor: "transparent",
    textStyle: { fontFamily: "Google Sans, Roboto, sans-serif",
                 fontSize: 15, color: "#B9D0F0" },
  });
  themed = true;
  return true;
}

const AX = {
  axisLine: { lineStyle: { color: "rgba(255,255,255,.22)" } },
  axisTick: { show: false },
  axisLabel: { color: "#B9D0F0", fontSize: 15 },
  splitLine: { lineStyle: { color: "rgba(255,255,255,.08)" } },
};

function chartSpec(d) {
  const s = d.series || [];
  const labels = s.map((x) => String(x.label ?? ""));
  const vals = s.map((x) => Number(x.value) || 0);
  const grid = { left: 6, right: 22, top: 16, bottom: 4, containLabel: true };

  if (d.kind === "pie" || d.kind === "donut") {
    return { series: [{ type: "pie", radius: ["48%", "74%"],
      itemStyle: { borderColor: "rgba(10,26,51,.85)", borderWidth: 3 },
      label: { color: "#E6F0FF", fontSize: 16 },
      data: s.map((x) => ({ name: x.label, value: Number(x.value) || 0 })) }] };
  }
  if (d.kind === "line" || d.kind === "area") {
    return { grid,
      xAxis: { type: "category", boundaryGap: false, data: labels, ...AX },
      yAxis: { type: "value", ...AX },
      series: [{ type: "line", data: vals, smooth: true, symbolSize: 10,
        lineStyle: { width: 5, color: "#6FA8FF" },
        itemStyle: { color: "#6FA8FF" },
        areaStyle: { color: "rgba(111,168,255,.20)" } }] };
  }
  return { grid,
    xAxis: { type: "value", ...AX },
    yAxis: { type: "category", data: labels.slice().reverse(), ...AX },
    series: [{ type: "bar", data: vals.slice().reverse(), barWidth: "56%",
      itemStyle: { borderRadius: [0, 8, 8, 0], color: "#6FA8FF" } }] };
}

function hydrate(root, type) {
  if (type === "chart" && echartsTheme()) {
    root.querySelectorAll(".echart-slot").forEach((slot) => {
      let d = {};
      try { d = JSON.parse(slot.dataset.spec || "{}"); } catch (e) { /* {} */ }
      try {
        const c = echarts.init(slot, "stage", { renderer: "canvas" });
        c.setOption(chartSpec(d));
        slot._chart = c;
        /* The scene is mid-animation (blur + transform) when this runs, so
           ECharts can measure zero. Re-measure once it has settled. */
        setTimeout(() => c.resize(), 120);
        setTimeout(() => c.resize(), 1100);
      } catch (e) { /* leave empty */ }
    });
  }
  if (type === "math" && window.katex) {
    root.querySelectorAll(".katex-slot").forEach((slot) => {
      try { katex.render(slot.dataset.tex || "", slot,
                         { displayMode: true, throwOnError: false }); }
      catch (e) { /* keep raw */ }
    });
  }
  if (type === "diagram" && window.mermaid) {
    root.querySelectorAll(".mermaid-slot").forEach(async (slot) => {
      const src = slot.dataset.src;
      if (!src) return;
      try {
        const id = "m" + Math.random().toString(36).slice(2, 8);
        const { svg } = await mermaid.render(id, src);
        slot.innerHTML = svg;
      } catch (e) { slot.textContent = ""; }
    });
  }
}

addEventListener("mermaid-ready", () => {
  live.forEach((s) => { if (s.payload.type === "diagram") hydrate(s.node, "diagram"); });
});

addEventListener("resize", () => {
  document.querySelectorAll(".echart-slot").forEach((s) => s._chart && s._chart.resize());
});

/* ---------- status ---------- */

const DOTS = { idle: "muted", listening: "", thinking: "thinking",
               drawing: "thinking", error: "muted" };

function setStatus(p) {
  el.dots.className = `core-dots ${DOTS[p.state] ?? ""}`;
  if (p.transcript !== undefined) el.caption.textContent = p.transcript || "";
}

/* ---------- ops ---------- */

const OPS = {
  "block.add": addScene,
  "block.update": updateScene,
  "block.remove": (p) => retire(p.id),
  "block.focus": (p) => focusScene(p.id),
  "canvas.clear": clearAll,
  "canvas.focus": (p) => (p.ids || []).slice(-1).forEach(focusScene),
  "canvas.reflow": () => {},          /* the stage lays itself out */
  "link.add": () => {},               /* no edges in a scene model */
  "link.remove": () => {},
  "status": setStatus,
  /* mic state is chrome, never a scene — the dock owns it */
  "mics.state": (p) => window.CoDock && window.CoDock.onMics(p),
};

function handle(frame) {
  const fn = OPS[frame.op];
  if (fn) { try { fn(frame.payload || {}); } catch (e) { console.warn(e); } }
}

/* ---------- source: LIVE vs DEMO ----------
 * LIVE  = whatever backend is on the websocket.
 * DEMO  = replay the fixture in the browser. Needs no backend at all, so
 *         it always works even if the mic machine is down mid-event.
 */
let ws = null, demoTimer = null, mode = Q.get("mode") || "live";

function stopDemo() { clearTimeout(demoTimer); demoTimer = null; }

function closeWs() {
  if (!ws) return;
  ws.onclose = null; ws.onerror = null;
  try { ws.close(); } catch (e) { /* already gone */ }
  ws = null;
}

function connect() {
  closeWs();
  ws = new WebSocket(WS_URL);
  ws.onopen = () => setStatus({ state: "listening" });
  ws.onmessage = (e) => { try { handle(JSON.parse(e.data)); } catch (err) {} };
  ws.onclose = () => {
    if (mode !== "live") return;
    setStatus({ state: "error", transcript: "reconnecting…" });
    setTimeout(() => mode === "live" && connect(), 1200);
  };
  ws.onerror = () => ws && ws.close();
}

async function runDemo() {
  stopDemo();
  let rows = [];
  try {
    const r = await fetch("./demo.jsonl", { cache: "no-store" });
    rows = (await r.text()).split("\n").filter(Boolean).map(JSON.parse);
  } catch (e) {
    setStatus({ state: "error", transcript: "demo fixture not found" });
    return;
  }
  let i = 0;
  const step = () => {
    if (mode !== "demo") return;
    if (i >= rows.length) { i = 0; demoTimer = setTimeout(step, 2500); return; }
    const row = rows[i++];
    demoTimer = setTimeout(() => { handle(row); step(); },
                           Math.max(0, (row.delay || 0) * 1000));
  };
  step();
}

function setMode(next) {
  if (next === mode && (ws || demoTimer)) return;
  mode = next;
  clearAll();
  stopDemo();
  /* The caption belongs to the source that produced it. Leaving a demo
     transcript on screen after switching to Live is a straight lie about
     what the model just heard. */
  setStatus({ state: mode === "demo" ? "idle" : "listening", transcript: "" });
  if (mode === "demo") { closeWs(); runDemo(); }
  else connect();
  document.querySelectorAll("#src-toggle button").forEach((b) =>
    b.classList.toggle("on", b.dataset.mode === mode));
  try { localStorage.setItem("copresenter-mode", mode); } catch (e) { /* private */ }
}

/* remember the last choice; ?mode= wins */
if (!Q.get("mode")) {
  try { mode = localStorage.getItem("copresenter-mode") || "live"; }
  catch (e) { /* private mode */ }
}

addEventListener("DOMContentLoaded", () => {
  const t = document.getElementById("src-toggle");
  if (t) t.onclick = (e) => {
    const b = e.target.closest("button[data-mode]");
    if (b) setMode(b.dataset.mode);
  };
  setMode(mode);
});
if (document.readyState !== "loading") {
  const t = document.getElementById("src-toggle");
  if (t) t.onclick = (e) => {
    const b = e.target.closest("button[data-mode]");
    if (b) setMode(b.dataset.mode);
  };
  setMode(mode);
}

window.sendPresenter = (action) => {
  if (ws && ws.readyState === 1)
    ws.send(JSON.stringify({ v: 1, cmd: "presenter", action }));
};

/* ---------- keys ---------- */

addEventListener("keydown", (e) => {
  const k = e.key.toLowerCase();
  if (k === "c") { clearAll(); window.sendPresenter("clear"); }
  /* 'h' (hide) and 'f' (fullscreen) live in dock.js -- binding them here too
     would toggle twice and cancel out. */
});

window.CoStage = { addScene, updateScene, retire, clearAll, focusScene, live };
