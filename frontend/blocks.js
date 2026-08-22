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
    <h2 class="text-2xl font-semibold leading-tight mb-3">${esc(d.title)}</h2>
    ${d.body ? `<p class="text-zinc-400 text-sm mb-3">${esc(d.body)}</p>` : ""}
    <ul class="space-y-2">
      ${(d.bullets || []).map((b) => `
        <li class="flex gap-3 text-zinc-300 text-[15px]">
          <span class="text-zinc-600 mt-[7px] w-1 h-1 rounded-full bg-zinc-600 shrink-0"></span>
          <span>${esc(b)}</span>
        </li>`).join("")}
    </ul>`,

  stat: (d) => `
    <div class="flex flex-col justify-center h-full">
      <div class="text-6xl font-semibold tracking-tight tabular-nums">${esc(d.value)}</div>
      <div class="text-zinc-500 text-sm mt-2">${esc(d.label)}</div>
      ${d.delta ? `<div class="text-emerald-400 text-xs mt-3 font-mono">${esc(d.delta)}</div>` : ""}
    </div>`,

  diagram: (d) => `
    ${title(d.title || "diagram")}
    <div class="mermaid-slot" data-src="${esc(d.mermaid)}">
      <pre class="text-[11px] text-zinc-600 font-mono whitespace-pre-wrap">${esc(d.mermaid)}</pre>
    </div>`,

  chart: (d) => {
    const s = d.series || [];
    const max = Math.max(1, ...s.map((x) => Number(x.value) || 0));
    if (d.kind === "pie") {
      const total = s.reduce((a, b) => a + (Number(b.value) || 0), 0) || 1;
      let acc = 0;
      const arcs = s.map((x, i) => {
        const frac = (Number(x.value) || 0) / total;
        const seg = `${frac * 100} ${100 - frac * 100}`;
        const off = 25 - acc * 100;
        acc += frac;
        return `<circle r="15.9" cx="21" cy="21" fill="none" stroke-width="9"
          stroke="${["#8b5cf6","#f59e0b","#10b981","#f43f5e","#38bdf8"][i % 5]}"
          stroke-dasharray="${seg}" stroke-dashoffset="${off}"/>`;
      }).join("");
      return `${title(d.title || "chart")}
        <div class="flex items-center gap-6">
          <svg viewBox="0 0 42 42" class="w-32 h-32 -rotate-90">${arcs}</svg>
          <div class="space-y-1 text-sm">${s.map((x, i) => `
            <div class="flex gap-2 items-center text-zinc-400">
              <span class="w-2 h-2 rounded-full" style="background:${["#8b5cf6","#f59e0b","#10b981","#f43f5e","#38bdf8"][i % 5]}"></span>
              ${esc(x.label)} <span class="text-zinc-600 tabular-nums">${esc(x.value)}</span>
            </div>`).join("")}</div>
        </div>`;
    }
    return `${title(d.title || "chart")}
      <div class="space-y-3 mt-1">${s.map((x) => `
        <div>
          <div class="flex justify-between text-xs text-zinc-500 mb-1">
            <span>${esc(x.label)}</span>
            <span class="tabular-nums">${esc(x.value)}${esc(d.unit || "")}</span>
          </div>
          <div class="h-2.5 rounded-full bg-[#1e1e28] overflow-hidden">
            <div class="h-full rounded-full bg-violet-500 transition-all duration-700"
                 style="width:${((Number(x.value) || 0) / max) * 100}%"></div>
          </div>
        </div>`).join("")}</div>`;
  },

  table: (d) => `
    ${title(d.title || "table")}
    <table class="w-full text-sm">
      <thead><tr class="text-zinc-600 text-xs uppercase tracking-wider">
        ${(d.columns || []).map((c) => `<th class="text-left font-normal pb-2">${esc(c)}</th>`).join("")}
      </tr></thead>
      <tbody>${(d.rows || []).map((r) => `
        <tr class="border-t border-[#20202a]">
          ${r.map((c, i) => `<td class="py-2.5 ${i === 0 ? "text-zinc-500" : "text-zinc-200"}">${esc(c)}</td>`).join("")}
        </tr>`).join("")}</tbody>
    </table>`,

  image: (d) => d.src
    ? `<img src="${esc(d.src)}" alt="${esc(d.alt)}" class="w-full rounded-xl">
       ${d.caption ? `<div class="text-zinc-600 text-xs mt-3">${esc(d.caption)}</div>` : ""}`
    : `<div class="shimmer w-full h-40 rounded-xl"></div>
       <div class="text-zinc-700 text-xs mt-3">${esc(d.caption || "imagining…")}</div>`,

  map: (d) => `
    ${title(`${d.from} → ${d.to}`)}
    ${d.embed_url
      ? `<iframe src="${esc(d.embed_url)}" class="w-full h-48 rounded-xl border-0"
           loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>`
      : `<svg viewBox="0 0 200 180" class="w-full h-48 rounded-xl bg-[#101018]">
           <polyline points="${(d.polyline || []).map((p) => p.join(",")).join(" ")}"
             fill="none" stroke="#8b5cf6" stroke-width="3" stroke-linecap="round"
             stroke-dasharray="600" stroke-dashoffset="600">
             <animate attributeName="stroke-dashoffset" to="0" dur="1.2s" fill="freeze"/>
           </polyline>
         </svg>`}
    <div class="flex gap-4 mt-3 text-sm">
      <span class="text-zinc-200">${esc(d.duration || "")}</span>
      <span class="text-zinc-600">${esc(d.distance || "")}</span>
      <span class="text-zinc-700 text-xs self-center">${esc(d.mode || "")}</span>
    </div>`,

  code: (d) => `
    ${title(d.title || d.lang || "code")}
    <pre class="text-[13px] leading-relaxed font-mono text-zinc-300 overflow-x-auto
                whitespace-pre">${esc(d.source)}</pre>`,
};

function renderBlock(type, data) {
  const fn = RENDER[type];
  if (!fn) return `<div class="text-rose-500 text-xs font-mono">unknown block type: ${esc(type)}</div>`;
  try {
    return fn(data || {});
  } catch (e) {
    return `<div class="text-rose-500 text-xs font-mono">render error: ${esc(e.message)}</div>`;
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

window.CoPresenterBlocks = { renderBlock, hydrateMermaid };
