/* ---------- control dock ----------
 * Up to four microphones and a camera, driven from the screen.
 *
 * Mic capture happens in the browser purely so the room can SEE which mic is
 * live and which is muted — the level meters are real getUserMedia audio.
 * Routing that audio into Gemini stays a backend concern; every action is
 * mirrored to the backend over the presenter channel so it can follow.
 */
const MAX_MICS = 4;
const mics = new Map();          // slot -> {slot, stream, track, ctx, el, muted}
let camStream = null;

const dock = {
  root:   document.getElementById("dock"),
  row:    document.getElementById("mic-row"),
  add:    document.getElementById("add-mic"),
  cam:    document.getElementById("cam-btn"),
  clear:  document.getElementById("clear-btn"),
  wrap:   document.getElementById("cam-wrap"),
  video:  document.getElementById("cam"),
  picker: document.getElementById("picker"),
  list:   document.getElementById("picker-list"),
  cancel: document.getElementById("picker-cancel"),
};

/* --- auto-hide: the presenter faces the room, so chrome shouldn't linger --- */
let hideTimer;
function poke() {
  dock.root.classList.remove("hidden");
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => {
    if (!dock.picker.classList.contains("on")) dock.root.classList.add("hidden");
  }, 3000);
}
["mousemove", "keydown", "touchstart"].forEach((e) => addEventListener(e, poke));
poke();

/* --- device picker --------------------------------------------------- */
async function pickDevice() {
  /* labels are blank until permission is granted once */
  try { (await navigator.mediaDevices.getUserMedia({ audio: true }))
          .getTracks().forEach((t) => t.stop()); } catch (e) { /* denied */ }

  let devs = [];
  try {
    devs = (await navigator.mediaDevices.enumerateDevices())
             .filter((d) => d.kind === "audioinput");
  } catch (e) { /* leave empty */ }

  const used = new Set([...mics.values()].map((m) => m.deviceId));
  dock.list.innerHTML = devs.length
    ? devs.map((d, i) => `
        <button class="picker-item" data-id="${d.deviceId}"
                ${used.has(d.deviceId) ? "disabled" : ""}>
          <span class="msym">${used.has(d.deviceId) ? "check_circle" : "mic"}</span>
          <span>${(d.label || `Microphone ${i + 1}`).replace(/</g, "")}</span>
        </button>`).join("")
    : `<div style="color:#5F6368;font-size:14px;padding:8px 4px">
         No microphones found. Check permissions.</div>`;

  dock.picker.classList.add("on");
  poke();

  return new Promise((resolve) => {
    const done = (v) => {
      dock.picker.classList.remove("on");
      dock.list.onclick = null; dock.cancel.onclick = null;
      resolve(v);
    };
    dock.list.onclick = (e) => {
      const b = e.target.closest(".picker-item");
      if (b && !b.disabled) done(b.dataset.id);
    };
    dock.cancel.onclick = () => done(null);
  });
}

/* --- add / remove / mute --------------------------------------------- */
async function addMic() {
  if (mics.size >= MAX_MICS) return;
  const deviceId = await pickDevice();
  if (!deviceId) return;

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { deviceId: { exact: deviceId },
               echoCancellation: true, noiseSuppression: true },
    });
  } catch (e) { return; }               // denied or busy: fail quietly

  /* lowest free slot keeps colours stable as mics come and go */
  let slot = 1;
  while ([...mics.values()].some((m) => m.slot === slot)) slot++;

  const el = document.createElement("button");
  el.className = `mic-chip mic-${slot}`;
  el.innerHTML = `
    <span class="swatch"></span>
    <span class="mic-name">Mic ${slot}</span>
    <span class="level"><i></i></span>
    <span class="msym">mic</span>`;

  const m = { slot, deviceId, stream, el, muted: false,
              track: stream.getAudioTracks()[0] };
  mics.set(slot, m);
  dock.row.appendChild(el);
  meter(m);

  el.onclick = () => toggleMute(slot);
  el.oncontextmenu = (e) => { e.preventDefault(); removeMic(slot); };

  dock.add.disabled = mics.size >= MAX_MICS;
  window.sendPresenter(`mic_add:${slot}`);
  poke();
}

function toggleMute(slot) {
  const m = mics.get(slot);
  if (!m) return;
  m.muted = !m.muted;
  if (m.track) m.track.enabled = !m.muted;
  m.el.classList.toggle("muted", m.muted);
  m.el.querySelector(".msym").textContent = m.muted ? "mic_off" : "mic";
  if (m.muted) m.el.classList.remove("speaking");
  window.sendPresenter(`${m.muted ? "mic_mute" : "mic_unmute"}:${slot}`);
  poke();
}

function removeMic(slot) {
  const m = mics.get(slot);
  if (!m) return;
  try { m.stream.getTracks().forEach((t) => t.stop()); } catch (e) { /* gone */ }
  try { m.ctx && m.ctx.close(); } catch (e) { /* gone */ }
  m.el.remove();
  mics.delete(slot);
  dock.add.disabled = mics.size >= MAX_MICS;
  window.sendPresenter(`mic_remove:${slot}`);
}

/* live level meter: real audio, so a dead mic is visibly dead before you
   find out on stage */
function meter(m) {
  let ctx;
  try { ctx = new (window.AudioContext || window.webkitAudioContext)(); }
  catch (e) { return; }
  m.ctx = ctx;
  const an = ctx.createAnalyser();
  an.fftSize = 256;
  ctx.createMediaStreamSource(m.stream).connect(an);
  const buf = new Uint8Array(an.frequencyBinCount);
  const bar = m.el.querySelector(".level i");

  (function tick() {
    if (!mics.has(m.slot)) return;
    an.getByteFrequencyData(buf);
    let sum = 0;
    for (const v of buf) sum += v;
    const lvl = m.muted ? 0 : Math.min(100, (sum / buf.length) * 2.6);
    bar.style.width = `${lvl}%`;
    m.el.classList.toggle("speaking", lvl > 14);
    requestAnimationFrame(tick);
  })();
}

/* --- camera ----------------------------------------------------------- */
async function toggleCam() {
  if (camStream) {
    camStream.getTracks().forEach((t) => t.stop());
    camStream = null;
    dock.video.srcObject = null;
    dock.wrap.classList.remove("on");
    dock.cam.classList.remove("on");
    dock.cam.querySelector(".msym").textContent = "videocam_off";
    window.sendPresenter("camera_off");
  } else {
    try {
      camStream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 } }, audio: false });
    } catch (e) { return; }
    dock.video.srcObject = camStream;
    dock.wrap.classList.add("on");
    dock.cam.classList.add("on");
    dock.cam.querySelector(".msym").textContent = "videocam";
    window.sendPresenter("camera_on");
  }
  poke();
}

dock.add.onclick   = addMic;
dock.cam.onclick   = toggleCam;
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

/* hide-everything button: strips the dock, hud and preview. Just the scenes. */
dock.stageBtn = document.getElementById("stage-btn");
if (dock.stageBtn) dock.stageBtn.onclick = () => {
  const on = !document.body.classList.contains("clean");
  document.body.classList.toggle("clean", on);
  dock.stageBtn.querySelector(".msym").textContent =
    on ? "fullscreen_exit" : "fullscreen";
  if (on && document.documentElement.requestFullscreen)
    document.documentElement.requestFullscreen().catch(() => {});
  else if (!on && document.fullscreenElement) document.exitFullscreen().catch(() => {});
};
