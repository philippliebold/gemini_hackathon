/* ---------- control dock ----------
 * Up to four microphones, driven from the screen.
 *
 * The mic chips are NOT local capture. They mirror the backend's floor: who is
 * connected, who currently owns the audio stream, and how loud they are. That is
 * the only honest source, because the backend is what actually feeds Gemini — a
 * browser-side meter can look alive while nothing reaches the model.
 *
 * The Mac's own input is chosen here too; the backend opens the device and can
 * switch it mid-session. Phones join over the QR panel.
 */
let MAX_MICS = 4;
let micState = { roster: [], devices: [], mac: { active: false, device: null } };

const dock = {
  root:   document.getElementById("dock"),
  row:    document.getElementById("mic-row"),
  add:    document.getElementById("add-mic"),
  clear:  document.getElementById("clear-btn"),
  picker: document.getElementById("picker"),
  list:   document.getElementById("picker-list"),
  cancel: document.getElementById("picker-cancel"),
  phone:  document.getElementById("phone-btn"),
  panel:  document.getElementById("phone"),
  qr:     document.getElementById("phone-qr"),
  url:    document.getElementById("phone-url"),
  plive:  document.getElementById("phone-live"),
  pclose: document.getElementById("phone-close"),
  brain:  document.getElementById("brain-btn"),
  gate:   document.getElementById("gate"),
  gateV:  document.getElementById("gate-val"),
  gateL:  document.getElementById("gate-level"),
  gateM:  document.getElementById("gate-mark"),
  notesBtn: document.getElementById("notes-btn"),
  notes:  document.getElementById("notes"),
  nThread: document.getElementById("notes-thread"),
  nBody:  document.getElementById("notes-body"),
  nAge:   document.getElementById("notes-age"),
  nClose: document.getElementById("notes-close"),
  reset:  document.getElementById("reset-btn"),
  listen: document.getElementById("listen-btn"),
  listenL: document.getElementById("listen-label"),
  spk:    document.getElementById("spk-btn"),
  spkL:   document.getElementById("spk-label"),
  help:   document.getElementById("help"),
  helpBtn: document.getElementById("help-btn"),
  helpClose: document.getElementById("help-close"),
};

let spkState = { active: false, device: null, devices: [] };

let listening = true;

let notes = null;

/* --- auto-hide: the presenter faces the room, so chrome shouldn't linger --- */
let hideTimer, stirTimer;
function poke() {
  dock.root.classList.remove("hidden");
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => {
    if (!dock.picker.classList.contains("on")) dock.root.classList.add("hidden");
  }, 3000);

  /* `stirring` means someone is at the machine. It is what surfaces the
     reveal button while the controls are hidden. */
  document.body.classList.add("stirring");
  clearTimeout(stirTimer);
  stirTimer = setTimeout(() => document.body.classList.remove("stirring"), 2600);
}
["mousemove", "keydown", "touchstart", "wheel"].forEach((e) =>
  addEventListener(e, poke, { passive: true }));
poke();

/* --- backend state ------------------------------------------------------
 * One frame from the backend replaces all local guessing about mics. */
function onMics(p) {
  micState = {
    roster: p.roster || [],
    devices: p.devices || [],
    mac: p.mac || { active: false, device: null },
    brain: p.brain || { enabled: false, model: null },
  };
  if (dock.brain) {
    const on = micState.brain.enabled;
    const locked = p.ear === "local";     /* nothing else can draw */
    dock.brain.classList.toggle("on", on);
    dock.brain.classList.toggle("locked", locked);
    dock.brain.title = locked
      ? `Local ears: ${micState.brain.model || "3.7-flash"} is the only thing that can draw, so this stays on`
      : on
        ? `Drawing decided by ${micState.brain.model || "3.7-flash"} — click to hand it back to the live ear`
        : "Let gemini-3.7-flash decide what to draw, instead of the live ear";
  }
  MAX_MICS = p.max_mics || 4;

  if (p.join_url) {
    dock.url.textContent = p.join_url;
    dock.url.href = p.join_url;
  }
  if (p.qr_svg && dock.qr.dataset.done !== "1") {
    dock.qr.innerHTML = p.qr_svg;          /* rendered by the backend, no JS lib */
    dock.qr.dataset.done = "1";
  }
  if (p.gate !== undefined) renderGate(p.gate);
  if (p.notes) onNotes(p.notes);
  if (p.listening !== undefined) renderListen(p.listening);
  if (p.speaker) renderSpeaker(p.speaker);
  renderRoster();
}

/* Unambiguous from across a room: green pulse when the mic is live, plainly dead
   when it is not. Nobody should have to wonder whether a hot mic is still billing. */
function renderListen(on) {
  listening = !!on;
  if (!dock.listen) return;
  dock.listen.classList.toggle("on", listening);
  dock.listen.classList.toggle("off", !listening);
  /* The glyph is the ACTION, the way a media control works: running shows
     pause, stopped shows play. The label says the same thing in a word, so it
     reads as a kill switch from across the room and not as a status light. */
  dock.listen.querySelector(".msym").textContent = listening ? "pause" : "play_arrow";
  if (dock.listenL) dock.listenL.textContent = listening ? "Stop" : "Start";
  dock.listen.title = listening
    ? "Stop — halts transcription and every API call (L)"
    : "Start listening again (L)";
  document.body.classList.toggle("stopped", !listening);
}

/* The PA. The label carries the live buffer depth because that IS the latency you
   are hearing — if it climbs, the network is behind, not the audio path. */
function renderSpeaker(st) {
  spkState = st;
  if (!dock.spk) return;
  dock.spk.classList.toggle("on", st.active);
  dock.spk.querySelector(".msym").textContent = st.active ? "volume_up" : "volume_off";
  if (dock.spkL) {
    const name = (st.devices.find((d) => d.index === st.device) || {}).name || "Output";
    dock.spkL.textContent = st.active
      ? `${name.split(" ").slice(0, 2).join(" ")} · ${st.buffered_ms}ms`
      : "Speaker";
  }
  dock.spk.title = st.active
    ? "Playing out loud — click to stop, right-click to pick an output"
    : "Play the phones through this Mac's output — right-click to pick an output (S)";
}

function pickOutput() {
  const devs = spkState.devices || [];
  dock.list.innerHTML = devs.length
    ? devs.map((d) => `
        <button class="picker-item" data-i="${d.index}">
          <span class="msym">${d.index === spkState.device && spkState.active
            ? "check_circle" : "speaker"}</span>
          <span>${String(d.name).replace(/[<>&]/g, "")}${d.default ? " · default" : ""}</span>
        </button>`).join("")
    : `<div style="color:#5F6368;font-size:14px;padding:8px 4px">No outputs found.</div>`;
  dock.picker.classList.add("on");
  poke();
  dock.list.onclick = (e) => {
    const b = e.target.closest(".picker-item");
    if (!b) return;
    window.sendPresenter(`speaker_device:${b.dataset.i}`);
    closeP();
  };
  dock.cancel.onclick = closeP;
  function closeP() {
    dock.picker.classList.remove("on");
    dock.list.onclick = dock.cancel.onclick = null;
  }
}

/* Named `strip`, not `esc`: stage.js already declares a top-level `esc`, and
   both files load as classic scripts into ONE global lexical scope — a second
   `const esc` is a SyntaxError that stops this whole file from parsing, which
   silently kills every dock button. */
const strip = (t) => String(t == null ? "" : t).replace(/[<>&]/g, "");

/* The record is the artifact that survives the talk. Sections rather than a blob so
   a glance finds the thing you are looking for. */
function onNotes(sm) {
  notes = sm || {};
  if (!dock.nBody) return;
  const has = (k) => Array.isArray(notes[k]) && notes[k].length;

  dock.nThread.textContent = notes.thread || "Nothing captured yet — start talking.";
  dock.nThread.classList.toggle("empty", !notes.thread);

  const section = (icon, label, items) => items.length ? `
    <div class="notes-sec">
      <div class="notes-lbl"><span class="msym">${icon}</span>${label}</div>
      <ul>${items.map((i) => `<li>${i}</li>`).join("")}</ul>
    </div>` : "";

  dock.nBody.innerHTML =
    section("lightbulb", "Topics", has("topics")
      ? notes.topics.map((t) => `<b>${strip(t.key)}</b> ${strip(t.gist)}`) : []) +
    section("numbers", "Numbers", has("numbers")
      ? notes.numbers.map((n) => `<b>${strip(n.value)}</b> ${strip(n.of)}`) : []) +
    section("task_alt", "Decided", has("decisions") ? notes.decisions.map(strip) : []) +
    section("help", "Open questions", has("questions") ? notes.questions.map(strip) : []);

  const n = ["topics", "numbers", "decisions", "questions"]
    .reduce((a, k) => a + (has(k) ? notes[k].length : 0), 0);
  dock.nAge.textContent = n ? `${n} captured` : "";
  if (dock.notesBtn) dock.notesBtn.classList.toggle("has", n > 0);
}

/* Track is scaled so 0.08 fills it: quiet speech lands near 20%, a loud room near
   25%, so the useful range is the left third and needs the room. */
let gateDragging = false;
function renderGate(gate) {
  if (!dock.gate) return;
  if (!gateDragging) dock.gate.value = gate;
  dock.gateV.textContent = gate.toFixed(3);
  dock.gateM.style.left = `${Math.min(100, (gate / 0.08) * 100)}%`;
  const peak = Math.max(0, ...micState.roster.map((m) => m.rms || 0));
  dock.gateL.style.width = `${Math.min(100, (peak / 0.08) * 100)}%`;
  dock.gateL.classList.toggle("over", peak >= gate);
}

/* A chip per live mic. `holding` is the one whose audio is actually reaching the
   model right now — the single most useful thing to see before you speak. */
function renderRoster() {
  const r = micState.roster;
  /* Numbered, not named. "MacBook Pro Microphone" is long, changes per
     machine, and tells the room nothing; the colour and the number are what
     tie a chip to the speaker on the canvas. Real device name is the tooltip. */
  dock.row.innerHTML = r.map((m, i) => `
    <div class="mic-chip mic-${(i % 4) + 1}${m.holding ? " speaking" : ""}${m.muted ? " muted" : ""}"
         title="${String(m.label).replace(/[<>&"]/g, "")}${
           m.muted ? " — muted on the phone"
           : m.holding ? " — live, audio is reaching the model" : " — connected"}">
      <span class="swatch"></span>
      <span class="mic-name">Microphone ${i + 1}</span>
      <span class="level"><i style="width:${Math.min(100, Math.round((m.rms || 0) * 900))}%"></i></span>
      <input class="mic-vol" type="range" min="0" max="2" step="0.05"
             value="${m.gain == null ? 1 : m.gain}" data-mic="${m.id}"
             title="How loud this phone is in the room — does not affect transcription">
      <span class="msym">${m.muted ? "mic_off" : m.holding ? "graphic_eq" : "mic"}</span>
    </div>`).join("");

  /* Room volume per phone. Deliberately does NOT touch the transcription path:
     turning someone down so the PA behaves must not make the model deaf to them. */
  dock.row.querySelectorAll(".mic-vol").forEach((el) => {
    el.onchange = () => window.sendPresenter(`mic_gain:${el.dataset.mic}:${el.value}`);
  });

  const n = r.length;
  dock.add.disabled = n >= MAX_MICS;
  dock.add.querySelector("span:last-child").textContent =
    micState.mac.active ? "Mac mic on" : "Mac mic";
  dock.add.classList.toggle("on", micState.mac.active);

  const live = r.filter((m) => m.holding).length;
  dock.plive.textContent = n
    ? `${n} of ${MAX_MICS} connected${live ? " · one is live" : ""}`
    : "No microphones yet";
}

/* --- Mac input picker: the backend's real devices, not the browser's ---- */
function pickMacDevice() {
  const devs = micState.devices;
  const cur = micState.mac.device;
  dock.list.innerHTML = (devs.length
    ? devs.map((d) => `
        <button class="picker-item" data-i="${d.index}">
          <span class="msym">${d.index === cur && micState.mac.active
            ? "check_circle" : "mic"}</span>
          <span>${String(d.name).replace(/[<>&]/g, "")}${d.default ? " · default" : ""}</span>
        </button>`).join("")
    : `<div style="color:#5F6368;font-size:14px;padding:8px 4px">
         No inputs reported by the backend.</div>`)
    + (micState.mac.active
        ? `<button class="picker-item" data-i="off">
             <span class="msym">mic_off</span><span>Turn the Mac mic off</span></button>`
        : "");

  dock.picker.classList.add("on");
  poke();
  dock.list.onclick = (e) => {
    const b = e.target.closest(".picker-item");
    if (!b) return;
    window.sendPresenter(b.dataset.i === "off"
      ? "mic_off" : `mic_device:${b.dataset.i}`);
    close();
  };
  dock.cancel.onclick = close;
  function close() {
    dock.picker.classList.remove("on");
    dock.list.onclick = dock.cancel.onclick = null;
  }
}

/* --- phone panel -------------------------------------------------------- */
function togglePhone(on) {
  dock.panel.classList.toggle("on", on);
  if (on) window.sendPresenter("mics_refresh");
  poke();
}

dock.add.onclick   = pickMacDevice;
dock.phone.onclick = () => togglePhone(!dock.panel.classList.contains("on"));
dock.pclose.onclick = () => togglePhone(false);
addEventListener("keydown", (e) => {
  if (e.key === "Escape") togglePhone(false);
  if (e.key.toLowerCase() === "m" && !e.metaKey && !e.ctrlKey) pickMacDevice();
});
if (dock.brain) dock.brain.onclick = () => {
  if (dock.brain.classList.contains("locked")) return;
  /* optimistic: the backend echoes the real state back on mics.state */
  const next = !micState.brain.enabled;
  dock.brain.classList.toggle("on", next);
  window.sendPresenter(next ? "brain_on" : "brain_off");
  poke();
};
if (dock.gate) {
  dock.gate.oninput = () => {
    gateDragging = true;
    dock.gateV.textContent = (+dock.gate.value).toFixed(3);
    dock.gateM.style.left = `${(dock.gate.value / 0.08) * 100}%`;
  };
  dock.gate.onchange = () => {
    gateDragging = false;
    window.sendPresenter(`mic_gate:${dock.gate.value}`);
  };
}
if (dock.notesBtn) dock.notesBtn.onclick = () => {
  const on = !dock.notes.classList.contains("on");
  dock.notes.classList.toggle("on", on);
  if (on) window.sendPresenter("mics_refresh");   /* pull the latest record */
  poke();
};
if (dock.nClose) dock.nClose.onclick = () => dock.notes.classList.remove("on");
if (dock.reset) dock.reset.onclick = () => {
  /* deliberately unconfirmed: a rehearsal control, and speed matters more than
     protecting a board you are about to rebuild anyway */
  window.sendPresenter("context_reset");
  onNotes({});
  poke();
};
addEventListener("keydown", (e) => {
  if (e.key === "Escape" && dock.notes) dock.notes.classList.remove("on");
  if (e.key.toLowerCase() === "n" && !e.metaKey && !e.ctrlKey && dock.notesBtn)
    dock.notesBtn.click();
});
if (dock.listen) dock.listen.onclick = () => {
  const next = !listening;
  renderListen(next);                       /* optimistic; the backend confirms */
  window.sendPresenter(next ? "listen_on" : "listen_off");
  poke();
};
addEventListener("keydown", (e) => {
  if (e.key.toLowerCase() === "l" && !e.metaKey && !e.ctrlKey && dock.listen)
    dock.listen.click();
});
if (dock.spk) {
  dock.spk.onclick = () => {
    window.sendPresenter(spkState.active ? "speaker_off" : "speaker_on");
    poke();
  };
  dock.spk.oncontextmenu = (e) => { e.preventDefault(); pickOutput(); };
}
addEventListener("keydown", (e) => {
  if (e.key.toLowerCase() === "s" && !e.metaKey && !e.ctrlKey && dock.spk)
    dock.spk.click();
});
/* Controls panel. Every shortcut here has a real button too — the keys are a
   shortcut, never the only way in. */
function toggleHelp(on) {
  if (!dock.help) return;
  dock.help.classList.toggle("on", on === undefined
    ? !dock.help.classList.contains("on") : on);
  poke();
}
if (dock.helpBtn) dock.helpBtn.onclick = () => toggleHelp();
if (dock.helpClose) dock.helpClose.onclick = () => toggleHelp(false);
addEventListener("keydown", (e) => {
  if (e.key === "?" || (e.key === "/" && e.shiftKey)) toggleHelp();
  if (e.key === "Escape") toggleHelp(false);
});
window.CoDock = { onMics, onNotes, renderListen, renderSpeaker, toggleHelp };
dock.clear.onclick = () => { window.CoStage.clearAll(); window.sendPresenter("clear"); };


/* ---------- presenter mode ----------
 * Strips every affordance: dock, status, hints, dot grid. What remains is
 * the canvas on white. This is what the room should see for the whole talk;
 * the dock exists for the minute before it starts. */
function setStage(on) {
  document.body.classList.toggle("stage", on);
  const i = dock.stage && dock.stage.querySelector(".msym");
  if (i) i.textContent = on ? "fullscreen_exit" : "fullscreen";
  if (on && document.documentElement.requestFullscreen)
    document.documentElement.requestFullscreen().catch(() => {});
  else if (!on && document.fullscreenElement && document.exitFullscreen)
    document.exitFullscreen().catch(() => {});
  /* charts must re-measure after the chrome disappears */
  setTimeout(() => document.querySelectorAll(".echart-slot").forEach((el) => {
    if (el._chart) el._chart.resize();
  }), 420);
}

dock.stage = document.getElementById("stage-btn");
if (dock.stage) dock.stage.onclick = () => setStage(!document.body.classList.contains("stage"));
addEventListener("keydown", (e) => {
  if (e.key === "Escape" && document.body.classList.contains("stage")) setStage(false);
  if (e.key.toLowerCase() === "p" && !e.metaKey && !e.ctrlKey)
    setStage(!document.body.classList.contains("stage"));
});

/* Fullscreen and hide-controls are separate on purpose: you often want the
   projector filling the screen while you still reach the mic buttons. */
dock.full = document.getElementById("full-btn");
dock.hide = document.getElementById("hide-btn");

function setFullscreen(on) {
  const el = document.documentElement;
  if (on && el.requestFullscreen) el.requestFullscreen().catch(() => {});
  else if (!on && document.fullscreenElement) document.exitFullscreen().catch(() => {});
}

function syncFullIcon() {
  if (!dock.full) return;
  dock.full.querySelector(".msym").textContent =
    document.fullscreenElement ? "fullscreen_exit" : "fullscreen";
  dock.full.classList.toggle("on", !!document.fullscreenElement);
}

/* `clean` hides every affordance. H is the documented way back -- the dock
   cannot show a button to un-hide itself. */
function setClean(on) {
  document.body.classList.toggle("clean", on);
  if (dock.hide) {
    dock.hide.querySelector(".msym").textContent = on ? "visibility" : "visibility_off";
    dock.hide.classList.toggle("on", on);
  }
}

dock.reveal = document.getElementById("reveal");
if (dock.reveal) dock.reveal.onclick = () => { setClean(false); poke(); };

if (dock.full) dock.full.onclick = () => setFullscreen(!document.fullscreenElement);
if (dock.hide) dock.hide.onclick = () => setClean(!document.body.classList.contains("clean"));
addEventListener("fullscreenchange", syncFullIcon);

addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (k === "f") setFullscreen(!document.fullscreenElement);
  if (k === "h") setClean(!document.body.classList.contains("clean"));
  if (e.key === "Escape") setClean(false);
});

