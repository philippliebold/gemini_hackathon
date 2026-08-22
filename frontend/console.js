/* The operator console.
 *
 * Read-only about the canvas, authoritative about the machine. It renders the two
 * diagnostic ops (`trace`, `health.state`) and sends the same presenter commands
 * the dock does, so the stage can stay a clean surface for the room.
 *
 * Deliberately not a canvas client: it never renders a block. If you want to see
 * what the room sees, look at the room.
 */

const Q = new URLSearchParams(location.search);
const WS_URL = Q.get("ws") || "ws://127.0.0.1:8765";
const MAX_ROWS = 400;          // the backend keeps 400; matching it keeps scroll sane
const MAX_HEARD = 40;

const el = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const ui = {
  conn: el("conn"), listen: el("listen"), log: el("log"), counts: el("counts"),
  caption: el("caption"), heard: el("heard"), mics: el("mics"),
  gate: el("gate"), gateVal: el("gate-val"), inputs: el("inputs"),
  filters: el("filters"),
};

let ws = null;
let filter = "all";
let listening = true;
let gateDragging = false;

/* ---------- sending ---------- */

function send(action) {
  if (ws && ws.readyState === 1)
    ws.send(JSON.stringify({ v: 1, cmd: "presenter", action }));
}

/* ---------- vitals ---------- */

function vital(id, level, value, sub) {
  const node = el(id);
  if (!node) return;
  node.className = `vital is-${level}`;
  node.querySelector(".val").textContent = value;
  node.querySelector(".sub").textContent = sub || "";
}

const EAR_LEVEL = { ready: "ok", "live-api": "ok", warming: "warn",
                    off: "warn", dead: "bad" };

function onHealth(p) {
  const ear = p.ear || {};
  vital("v-ear", EAR_LEVEL[ear.state] || "warn", ear.state || "?",
        ear.error || ear.model || "");

  const brain = p.brain || {};
  /* A fallback model is not a failure, but it IS the reason the visuals feel
     different from the rehearsal, so it gets a colour of its own. */
  const brainLevel = brain.error ? "bad" : brain.fallback ? "warn"
                   : brain.model ? "ok" : "bad";
  vital("v-brain", brainLevel, brain.model || "none",
        brain.error ? brain.error
        : `${brain.fallback ? "on a fallback · " : ""}`
          + `${brain.inflight || 0} in flight · ${brain.calls || 0} calls`);

  const a = p.audio || {};
  vital("v-audio", a.dropped ? "warn" : "ok",
        `${a.queued ?? 0} / ${a.capacity ?? 0}`,
        a.dropped ? `${a.dropped} frame(s) dropped` : "nothing dropped");

  const lag = p.loop_lag_ms || 0;
  vital("v-loop", lag > 250 ? "bad" : lag > 60 ? "warn" : "ok",
        `${lag.toFixed(0)} ms`,
        lag > 250 ? "a stalled loop looks like a bad network" : "healthy");

  const st = p.stage || {};
  vital("v-stage", "ok", `${p.blocks ?? 0} / ${st.max_live ?? "?"}`,
        `${(st.lifetime_s ?? 0).toFixed(0)}s each · ${p.clients ?? 0} client(s)`);

  setListening(p.listening);
  renderCounts(p.counts || {});
}

/* The counters worth a glance mid-talk, in the order the pipeline visits them.
   Everything else stays in `counts` and is not shown — a wall of numbers is the
   same problem as a wall of log lines. */
const SHOWN = [
  ["heard", "heard"], ["drawn", "drawn"], ["silent", "chose silence"],
  ["skipped", "skipped"], ["blocked", "blocked"], ["dropped", "dropped"],
  ["ear.error", "ear errors"], ["brain.error", "brain errors"],
  ["tool.error", "tool errors"], ["audio.dropped", "audio dropped"],
];

function renderCounts(c) {
  ui.counts.innerHTML = SHOWN
    .filter(([k]) => c[k])
    .map(([k, label]) => `<span>${esc(label)} <b>${c[k]}</b></span>`)
    .join("") || "<span>no decisions yet</span>";
}

/* ---------- the trace log ---------- */

function clockOf(ts) {
  const d = new Date((ts || Date.now() / 1000) * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:`
       + `${String(d.getMinutes()).padStart(2, "0")}:`
       + `${String(d.getSeconds()).padStart(2, "0")}`;
}

/* Whisper answers in tens of milliseconds and the brain in seconds, and the same
   column has to carry both without anyone doing arithmetic mid-talk. */
function ms(v) {
  return v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`;
}

function shown(p) {
  if (filter === "all") return true;
  if (filter === "problems")
    return ["drop", "block", "error"].includes(p.verdict);
  return p.stage === filter;
}

function addTrace(p, ts) {
  const empty = ui.log.querySelector(".empty");
  if (empty) empty.remove();

  const row = document.createElement("div");
  row.className = `row v-${p.verdict}`;
  row.dataset.stage = p.stage;
  row.dataset.verdict = p.verdict;
  row.hidden = !shown(p);
  row.innerHTML = `
    <span class="t">${clockOf(ts)}</span>
    <span class="stage">${esc(p.stage)}</span>
    <span class="verdict">${esc(p.verdict)}</span>
    <span class="what">
      <span class="reason">${esc(p.reason)}</span>
      ${p.ms !== undefined ? `<span class="ms">${ms(p.ms)}</span>` : ""}
      ${p.n > 1 ? `<span class="n">×${p.n}</span>` : ""}
      ${p.text ? `<span class="said">“${esc(p.text)}”</span>` : ""}
      ${p.detail ? `<span class="detail">${esc(p.detail)}</span>` : ""}
    </span>`;

  /* Newest first, so the thing that just happened is never below the fold and
     there is no scroll position to fight over. */
  ui.log.prepend(row);
  while (ui.log.children.length > MAX_ROWS) ui.log.lastElementChild.remove();

  if (p.stage === "ear" && p.verdict === "ok" && p.text) addHeard(p.text);
}

function applyFilter() {
  [...ui.log.children].forEach((row) => {
    if (!row.dataset.stage) return;
    row.hidden = !shown({ stage: row.dataset.stage,
                          verdict: row.dataset.verdict });
  });
}

ui.filters.onclick = (e) => {
  const b = e.target.closest("button[data-f]");
  if (!b) return;
  filter = b.dataset.f;
  [...ui.filters.children].forEach((x) => x.classList.toggle("on", x === b));
  applyFilter();
};

/* ---------- what it heard ---------- */

function addHeard(text) {
  const d = document.createElement("div");
  d.textContent = text;
  ui.heard.prepend(d);
  while (ui.heard.children.length > MAX_HEARD) ui.heard.lastElementChild.remove();
}

function onStatus(p) {
  if (p.transcript !== undefined) ui.caption.textContent = p.transcript || "—";
}

/* ---------- mics ---------- */

function onMics(p) {
  const roster = p.roster || [];
  const gate = p.gate ?? 0.014;
  ui.mics.innerHTML = roster.length
    ? roster.map((m) => {
        const pct = Math.min(100, ((m.rms || 0) / 0.08) * 100);
        const over = (m.rms || 0) >= gate;
        return `<div class="mic ${m.holding ? "holding" : ""}">
          <span class="name">${esc(m.label)}${m.muted ? " · muted" : ""}</span>
          <span class="bar"><i class="${over ? "over" : ""}"
                               style="width:${pct.toFixed(0)}%"></i></span>
        </div>`;
      }).join("")
    : `<div class="none">No microphones connected.</div>`;

  if (!gateDragging) ui.gate.value = gate;
  ui.gateVal.textContent = Number(gate).toFixed(3);
  if (p.listening !== undefined) setListening(p.listening);
  renderInputs(p);
}

/* The backend's real input devices, not the browser's — the backend is what
   actually opens the microphone. */
function renderInputs(p) {
  const devices = p.devices || [];
  const mac = p.mac || {};
  ui.inputs.innerHTML = `<div class="lbl">This Mac's input</div>`
    + devices.map((d) => `
        <button class="btn ${mac.active && d.index === mac.device ? "on" : ""}"
                data-act="mic_device:${d.index}">
          ${esc(d.name)}${d.default ? " · default" : ""}
        </button>`).join("")
    + (mac.active
        ? `<button class="btn warn" data-act="mic_off">Turn the Mac mic off</button>`
        : "");
}

/* ---------- listening ---------- */

function setListening(on) {
  if (on === undefined) return;
  listening = !!on;
  ui.listen.textContent = listening ? "Stop" : "Start";
  ui.listen.classList.toggle("stopped", !listening);
  ui.listen.title = listening
    ? "Halt transcription and every API call, and cancel any in flight"
    : "Start listening again";
}

ui.listen.onclick = () => {
  setListening(!listening);                  /* optimistic; health confirms */
  send(listening ? "listen_on" : "listen_off");
};

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]");
  if (b) send(b.dataset.act);
});

ui.gate.oninput = () => {
  gateDragging = true;
  ui.gateVal.textContent = Number(ui.gate.value).toFixed(3);
};
ui.gate.onchange = () => {
  gateDragging = false;
  send(`mic_gate:${ui.gate.value}`);
};

/* ---------- socket ---------- */

const OPS = {
  trace: addTrace,
  "health.state": onHealth,
  status: onStatus,
  "mics.state": onMics,
};

function connect() {
  ws = new WebSocket(WS_URL);
  ws.onopen = () => {
    ui.conn.textContent = "connected";
    ui.conn.className = "pill good";
    /* `role: console` is what asks for the trace backlog — the stage does not get
       it, because a talk produces far more traces than blocks. */
    ws.send(JSON.stringify({ v: 1, cmd: "hello", role: "console" }));
    send("mics_refresh");
  };
  ws.onmessage = (e) => {
    let frame;
    try { frame = JSON.parse(e.data); } catch (err) { return; }
    const fn = OPS[frame.op];
    if (fn) { try { fn(frame.payload || {}, frame.ts); } catch (err) {
      console.warn(frame.op, err); } }
  };
  ws.onclose = () => {
    ui.conn.textContent = "backend down";
    ui.conn.className = "pill bad";
    setTimeout(connect, 1200);
  };
  ws.onerror = () => ws && ws.close();
}

connect();
