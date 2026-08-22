/* Block renderers. One function per block type in CONTRACT.md.
 *
 * Each renderer takes `data` and returns an HTML string for the block's inner
 * content. Adding a block type: add a renderer here, keyed by the type name.
 * Never crash — a bad payload must degrade, not blank the screen.
 */
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const title = (t) => (t ? `<div class="block-title">${esc(t)}</div>` : "");

const RENDER = {
  text: (d) => `
    ${title(d.title ? null : null)}
    <h2 class="text-2xl font-medium leading-tight mb-3" style="font-family:var(--font-display)">${esc(d.title)}</h2>
    ${d.body ? `<p class="text-[#5F6368] text-sm mb-3">${esc(d.body)}</p>` : ""}
    <ul class="space-y-2">
      ${(d.bullets || []).map((b) => `
        <li class="flex gap-3 text-[#3C4043] text-[15px]">
          <span class="text-[#80868B] mt-[7px] w-1 h-1 rounded-full bg-[#80868B] shrink-0"></span>
          <span>${esc(b)}</span>
        </li>`).join("")}
    </ul>`,

  stat: (d) => `
    <div class="flex flex-col justify-center h-full">
      <div class="text-6xl font-medium tracking-tight tabular-nums" style="font-family:var(--font-display)">${esc(d.value)}</div>
      <div class="text-[#5F6368] text-sm mt-2">${esc(d.label)}</div>
      ${d.delta ? `<div class="text-[#34A853] text-xs mt-3 font-mono">${esc(d.delta)}</div>` : ""}
    </div>`,

  diagram: (d) => `
    ${title(d.title || "diagram")}
    <div class="mermaid-slot" data-src="${esc(d.mermaid)}">
      <pre class="text-[11px] text-[#80868B] font-mono whitespace-pre-wrap">${esc(d.mermaid)}</pre>
    </div>`,

  /* ECharts renders after mount (needs a sized element), so the renderer
     only emits the host. hydrateCharts() below fills it. */
  chart: (d) => `
    ${title(d.title || "chart")}
    <div class="echart-slot" style="height:${d.h_chart || 260}px"
         data-spec="${esc(JSON.stringify(d))}"></div>`,

  table: (d) => `
    ${title(d.title || "table")}
    <table class="w-full text-sm">
      <thead><tr class="text-[#80868B] text-xs uppercase tracking-wider">
        ${(d.columns || []).map((c) => `<th class="text-left font-normal pb-2">${esc(c)}</th>`).join("")}
      </tr></thead>
      <tbody>${(d.rows || []).map((r) => `
        <tr class="border-t border-[#DADCE0]">
          ${r.map((c, i) => `<td class="py-2.5 ${i === 0 ? "text-[#5F6368]" : "text-[#202124]"}">${esc(c)}</td>`).join("")}
        </tr>`).join("")}</tbody>
    </table>`,

  image: (d) => d.src
    ? `<img src="${esc(d.src)}" alt="${esc(d.alt)}" class="w-full rounded-xl">
       ${d.caption ? `<div class="text-[#80868B] text-xs mt-3">${esc(d.caption)}</div>` : ""}`
    : `<div class="shimmer w-full h-40 rounded-xl"></div>
       <div class="text-[#9AA0A6] text-xs mt-3">${esc(d.caption || "imagining…")}</div>`,

  map: (d) => `
    ${title(`${d.from} → ${d.to}`)}
    ${d.embed_url
      ? `<iframe src="${esc(d.embed_url)}" class="w-full h-48 rounded-xl border-0"
           loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>`
      : `<svg viewBox="0 0 200 180" class="w-full h-48 rounded-xl bg-[#F8F9FA]">
           <polyline points="${(d.polyline || []).map((p) => p.join(",")).join(" ")}"
             fill="none" stroke="#4285F4" stroke-width="3" stroke-linecap="round"
             stroke-dasharray="600" stroke-dashoffset="600">
             <animate attributeName="stroke-dashoffset" to="0" dur="1.2s" fill="freeze"/>
           </polyline>
         </svg>`}
    <div class="flex gap-4 mt-3 text-sm">
      <span class="text-[#202124]">${esc(d.duration || "")}</span>
      <span class="text-[#80868B]">${esc(d.distance || "")}</span>
      <span class="text-[#9AA0A6] text-xs self-center">${esc(d.mode || "")}</span>
    </div>`,

  code: (d) => `
    ${title(d.title || d.lang || "code")}
    <pre class="text-[13px] leading-relaxed font-mono text-[#3C4043] overflow-x-auto
                whitespace-pre">${esc(d.source)}</pre>`,
};

function renderBlock(type, data) {
  const fn = RENDER[type];
  if (!fn) return `<div class="text-[#EA4335] text-xs font-mono">unknown block type: ${esc(type)}</div>`;
  try {
    return fn(data || {});
  } catch (e) {
    return `<div class="text-[#EA4335] text-xs font-mono">render error: ${esc(e.message)}</div>`;
  }
}

/* Mermaid is async and can throw on bad syntax — always keep the raw fallback
   visible until a real SVG replaces it. */
async function hydrateMermaid(el) {
  const slot = el.querySelector(".mermaid-slot");
  if (!slot || !window.mermaid || slot.dataset.done) return;
  try {
    const { svg } = await window.mermaid.render(
      "m" + Math.random().toString(36).slice(2), slot.dataset.src);
    slot.innerHTML = svg;
    slot.dataset.done = "1";
  } catch { /* leave the source visible; never blank the screen */ }
}

/* ---------- ECharts ---------- */
/* One theme, registered once. The model never emits colours or fonts —
   it emits {kind, series}. Everything visual is decided here, so a sloppy
   spec still lands on-brand. */
const G4 = ["#4285F4", "#EA4335", "#FBBC04", "#34A853", "#5F6368"];
let themeReady = false;

function ensureTheme() {
  if (themeReady || !window.echarts) return themeReady;
  echarts.registerTheme("gemini", {
    color: G4,
    backgroundColor: "transparent",
    textStyle: { fontFamily: "Roboto, Google Sans, sans-serif",
                 fontSize: 15, color: "#5F6368" },
  });
  themeReady = true;
  return true;
}

/* Axis/grid tuned for a projector: no tick marks, faint splitlines,
   bigger type than the ECharts default (which assumes a laptop). */
const AXIS = {
  axisLine: { lineStyle: { color: "#DADCE0" } },
  axisTick: { show: false },
  axisLabel: { color: "#5F6368", fontSize: 14 },
  splitLine: { lineStyle: { color: "#F1F3F4" } },
};
const GRID = { left: 4, right: 18, top: 14, bottom: 0, containLabel: true };

function specFor(d) {
  const s = d.series || [];
  const labels = s.map((x) => String(x.label ?? ""));
  const values = s.map((x) => Number(x.value) || 0);

  if (d.kind === "pie" || d.kind === "donut") {
    return { series: [{
      type: "pie", radius: ["46%", "72%"], avoidLabelOverlap: true,
      itemStyle: { borderColor: "#fff", borderWidth: 3 },
      label: { fontSize: 15, color: "#3C4043" },
      data: s.map((x) => ({ name: x.label, value: Number(x.value) || 0 })),
    }] };
  }
  if (d.kind === "line" || d.kind === "area") {
    return {
      grid: GRID,
      xAxis: { type: "category", boundaryGap: false, data: labels, ...AXIS },
      yAxis: { type: "value", ...AXIS },
      series: [{
        type: "line", data: values, smooth: true, symbolSize: 9,
        lineStyle: { width: 4 },
        areaStyle: { color: "rgba(66,133,244,.12)" },
      }],
    };
  }
  /* default: horizontal bars — the most legible form from the back of a room */
  return {
    grid: GRID,
    xAxis: { type: "value", ...AXIS },
    yAxis: { type: "category", data: labels.slice().reverse(), ...AXIS },
    series: [{
      type: "bar", data: values.slice().reverse(), barWidth: "55%",
      itemStyle: { borderRadius: [0, 6, 6, 0] },
    }],
  };
}

function hydrateCharts(root) {
  if (!ensureTheme()) return;
  root.querySelectorAll(".echart-slot").forEach((slot) => {
    if (slot.dataset.done) return;
    slot.dataset.done = "1";
    let d = {};
    try { d = JSON.parse(slot.dataset.spec || "{}"); } catch (e) { /* keep {} */ }
    try {
      const chart = echarts.init(slot, "gemini", { renderer: "canvas" });
      chart.setOption(specFor(d));           // animates in by default
      slot._chart = chart;
    } catch (e) {
      slot.innerHTML = `<div class="text-[#80868B] text-xs">chart unavailable</div>`;
    }
  });
}

window.CoPresenterBlocks = { renderBlock, hydrateMermaid, hydrateCharts };
