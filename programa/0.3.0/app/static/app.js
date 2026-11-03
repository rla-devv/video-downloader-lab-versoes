/* Video Downloader Lab — página (padrão RLA.dev). */
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// O app instalado se identifica pela marca que o servidor injeta; sem ela, é o navegador (modo de desenvolvimento).
const desktopApp = window.__DOWNLOADERLAB_APP || null;
let APP_VERSION = "";
let current = null;      // vídeo analisado: { url, referer, title, thumbnail, qualities }
let pollTimer = null;
let jobsCache = [];

const STATUS = {
  queued: ["Na fila", "working"],
  downloading: ["Baixando", "working"],
  processing: ["Finalizando", "working"],
  finished: ["Pronto", "done"],
  error: ["Erro", "error"],
};

/* ---------- utilidades ---------- */
async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (desktopApp) headers["X-VDL-Token"] = desktopApp.token;
  const res = await fetch(path, { cache: "no-store", ...options, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Erro ${res.status}`);
  return data;
}
// Números no formato brasileiro: 1,5 MB.
const nf = (n, d = 1) => n.toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d });
function formatBytes(b) {
  if (!b) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  return `${i ? nf(b) : Math.round(b)} ${units[i]}`;
}
function formatDuration(sec) {
  if (sec == null || isNaN(sec)) return "";
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = String(sec % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}
function qualityName(h) {
  if (h >= 2160) return "4K · ultra alta definição";
  if (h >= 1440) return "2K · muito alta definição";
  if (h >= 1080) return "Full HD · alta definição";
  if (h >= 720) return "HD · boa para a maioria das telas";
  if (h >= 480) return "Média · arquivo menor";
  return "Baixa · arquivo bem pequeno";
}

/* ---------- analisar o link ---------- */
const form = $("#url-form"), urlInput = $("#url"), analyzeBtn = $("#analyze-btn"), formError = $("#form-error");
const downloadBtn = $("#download-btn");

function showError(msg) { formError.textContent = msg || ""; formError.hidden = !msg; }

function renderQualities(qualities) {
  const box = $("#quality");
  box.innerHTML = "";
  const opts = [["best", "Melhor disponível", "A maior qualidade que o site oferece"]]
    .concat(qualities.map((h) => [String(h), `${h}p`, qualityName(h)]));
  opts.forEach(([value, title, desc], i) => {
    const label = document.createElement("label");
    label.className = "level" + (i === 0 ? " wide" : "");
    label.innerHTML = `<input type="radio" name="quality" value="${esc(value)}"${i === 0 ? " checked" : ""}><b>${esc(title)}</b><small>${esc(desc)}</small>`;
    box.append(label);
  });
  $("#quality-hint").textContent = qualities.length
    ? "Maior qualidade deixa o arquivo maior e o download mais demorado."
    : "Este site oferece uma qualidade só.";
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("");
  $("#preview").hidden = true;
  current = null;
  downloadBtn.disabled = true;
  analyzeBtn.disabled = true;
  analyzeBtn.textContent = "Analisando…";
  const lento = setTimeout(() => { analyzeBtn.textContent = "Procurando o vídeo na página…"; }, 4000);
  try {
    const info = await api("/api/info", { method: "POST", body: JSON.stringify({ url: urlInput.value }) });
    current = { url: info.webpage_url || urlInput.value, referer: info.referer || null, title: info.title,
                thumbnail: info.thumbnail || null, qualities: info.qualities || [] };
    $("#title").textContent = info.title || "(sem título)";
    const specs = [[info.platform, "Site"], [info.uploader, "Canal"], [formatDuration(info.duration), "Duração"]]
      .filter(([v]) => v).map(([v, k]) => `<span>${esc(k)}: <b>${esc(v)}</b></span>`);
    $("#specs").innerHTML = specs.join("");
    $("#found").hidden = !info.found_in_page;
    const img = $("#thumb");
    if (info.thumbnail) img.src = info.thumbnail; else img.removeAttribute("src");
    renderQualities(current.qualities);
    $("#preview").hidden = false;
    downloadBtn.disabled = false;
    $("#go-sum").textContent = "";
  } catch (err) {
    showError(err.message);
  } finally {
    clearTimeout(lento);
    analyzeBtn.disabled = false;
    analyzeBtn.textContent = "Analisar";
  }
});

downloadBtn.addEventListener("click", async () => {
  if (!current) return;
  downloadBtn.disabled = true;
  const quality = document.querySelector('input[name="quality"]:checked')?.value || "best";
  try {
    await api("/api/downloads", { method: "POST", body: JSON.stringify({
      url: current.url, quality, referer: current.referer, title: current.title, thumbnail: current.thumbnail }) });
    $("#go-sum").textContent = "Na lista de downloads";
    await refreshJobs();
  } catch (err) {
    showError(err.message);
  } finally {
    downloadBtn.disabled = false;
  }
});

/* ---------- lista de downloads ---------- */
function renderJob(job) {
  const [label, kind] = STATUS[job.status] || [job.status, ""];
  const card = document.createElement("article");
  card.className = "card";
  const thumb = job.thumbnail ? `<img src="${esc(job.thumbnail)}" alt="">` : "";
  const dados = [];
  if (job.status === "finished") dados.push(`<span><b>${esc(formatBytes(job.filesize))}</b></span>`);
  else if (job.total_bytes) dados.push(`<span>${esc(job.stage || "arquivo")}: <b>${esc(formatBytes(job.total_bytes))}</b></span>`);
  else if (job.stage) dados.push(`<span>${esc(job.stage)}</span>`);
  if (job.quality) dados.push(`<span>Qualidade: <b>${job.quality === "best" ? "melhor" : esc(job.quality) + "p"}</b></span>`);
  card.innerHTML = `<div class="card-head"><div class="thumb">${thumb}</div>
    <div class="card-txt"><span class="name">${esc(job.title || job.url)}</span><div class="specs">${dados.join("")}</div></div>
    <span class="pill ${kind}">${esc(label)}</span></div>`;

  if (["queued", "downloading", "processing"].includes(job.status)) {
    const pct = job.status === "processing" ? 100 : job.progress || 0;
    const right = job.status === "processing" ? "juntando vídeo e áudio"
      : [job.speed ? `${formatBytes(job.speed)}/s` : "", job.eta != null ? `faltam ${formatDuration(job.eta)}` : ""].filter(Boolean).join(" · ");
    card.insertAdjacentHTML("beforeend", `<div class="progress"><div class="track"><div class="fill" style="width:${pct}%"></div></div>
      <div class="meta"><span class="pct">${job.status === "queued" ? "esperando" : nf(pct) + "%"}</span><span>${esc(right)}</span></div></div>`);
  }
  if (job.status === "finished") {
    const actions = document.createElement("div");
    actions.className = "actions";
    actions.innerHTML = `<span class="note mono">${esc(job.filename)}</span>`;
    if (desktopApp) {
      const btn = document.createElement("button");
      btn.type = "button"; btn.className = "small primary"; btn.textContent = "Mostrar na pasta";
      btn.addEventListener("click", () => api(`/api/downloads/${job.id}/mostrar`, { method: "POST" }).catch((err) => showError(err.message)));
      actions.prepend(btn);
    } else {
      actions.insertAdjacentHTML("afterbegin", `<a class="btn-link primary small save" href="/api/downloads/${esc(job.id)}/file">Salvar arquivo</a>`);
    }
    card.append(actions);
  }
  if (job.status === "error") card.insertAdjacentHTML("beforeend", `<p class="note bad">${esc(job.error)}</p>`);
  return card;
}

async function refreshJobs() {
  let jobs;
  try { jobs = await api("/api/downloads"); } catch { return; }
  jobsCache = jobs;
  $("#jobs").replaceChildren(...jobs.map(renderJob));
  $("#empty").hidden = jobs.length > 0;
  clearTimeout(pollTimer);
  if (jobs.some((j) => ["queued", "downloading", "processing"].includes(j.status))) pollTimer = setTimeout(refreshJobs, 1000);
}
const downloadsAtivos = () => jobsCache.some((j) => ["queued", "downloading", "processing"].includes(j.status));

/* ---------- atualização (só no app instalado) ---------- */
const VER_RE = /^\d+(\.\d+){1,3}$/;
const cmpVer = (a, b) => { const x = String(a).split(".").map(Number), y = String(b).split(".").map(Number); for (let i = 0; i < 4; i++) if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) - (y[i] || 0); return 0; };
const UPDATE_EVERY = 15 * 60e3;
let updChecking = false, updAdiada = null, updAnterior = null;

const closeUpdate = () => { const d = $("#update-dlg"); if (d.open) d.close(); };
function updateBar(html, handlers = {}) {
  const bar = $("#update-bar"), dlg = $("#update-dlg");
  if (!html) return closeUpdate();
  bar.innerHTML = `<div class="pop-body">${html}</div>`;
  const grp = bar.querySelector(".pop-body > .grp:last-child");
  if (grp) { grp.querySelectorAll(".primary").forEach((b) => grp.append(b)); const foot = document.createElement("div"); foot.className = "upd-foot"; foot.append(grp); bar.append(foot); }
  bar.querySelectorAll("[data-u]").forEach((b) => b.addEventListener("click", () => { const k = b.dataset.u; if (handlers[k]) handlers[k](); else closeUpdate(); }));
  if (!dlg.open) dlg.showModal();
}
function flagUpdate(versao, destaque = true) {
  const btn = $("#update-btn");
  btn.classList.toggle("has-update", !!versao && destaque);
  $("#update-label").textContent = versao ? `Nova versão ${versao}` : "Buscar atualização";
  btn.title = versao ? "Há uma versão nova do Video Downloader Lab. Clique para ver as novidades." : "";
}
const closeBtn = (t = "Fechar") => `<button class="small" data-u="close">${t}</button>`;
const voltarLink = () => updAnterior ? `<p class="upd-voltar"><button type="button" data-u="voltar">Voltar para a versão ${esc(updAnterior)}</button></p>` : "";
const esperarTerminar = (acao) => updateBar(`<p>Há um download em andamento. Espere terminar para ${acao}.</p><div class="grp">${closeBtn()}</div>`);

async function checkUpdate(manual) {
  if (updChecking) return;
  updChecking = true;
  const btn = $("#update-btn");
  if (manual) { btn.disabled = true; updateBar("<p>Consultando se há uma versão nova…</p>"); }
  try {
    const r = await api("/api/atualizacao" + (manual ? "?forcar=1" : ""));
    updAnterior = VER_RE.test(r.anterior || "") && cmpVer(r.anterior, APP_VERSION) < 0 ? r.anterior : null;
    if (r.erro) throw new Error(r.erro);
    const m = r.nova || { versao: r.atual, novidades: [] };
    if (cmpVer(m.versao, APP_VERSION) > 0) {
      if (manual) showUpdate(m); else flagUpdate(m.versao, updAdiada !== m.versao);
    } else {
      flagUpdate(null);
      if (manual) updateBar(`<p>Você já está na versão mais recente (${esc(APP_VERSION)}).</p>${voltarLink()}<div class="grp">${closeBtn()}</div>`, { voltar: confirmarVolta });
    }
  } catch {
    if (manual) updateBar(`<p>Não consegui consultar se há versão nova. Confira se o computador está conectado à internet e tente de novo em instantes.</p>${voltarLink()}<div class="grp">${closeBtn()}</div>`, { voltar: confirmarVolta });
  }
  updChecking = false;
  btn.disabled = false;
}
// "Agora não": tira o ponto laranja daquela versão (só enquanto a página está aberta).
function adiarVersao(versao) { updAdiada = versao; flagUpdate(versao, false); closeUpdate(); }

function showUpdate(m) {
  flagUpdate(m.versao, updAdiada !== m.versao);
  const news = m.novidades && m.novidades.length ? m.novidades : ["Correções e melhorias."];
  updateBar(`<div class="upd-card"><span class="upd-ico" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/></svg></span>
      <span><b>Nova versão disponível: ${esc(m.versao)}</b><small>Você usa a versão ${esc(APP_VERSION)}.</small></span></div>
    <p class="upd-note">Seus vídeos, o Python e o ffmpeg continuam como estão; só o programa é trocado, e a versão atual fica guardada.</p>
    <p class="upd-label">Novidades</p>
    <ul class="upd-news${news.length === 1 ? " one" : ""}">${news.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>
    <hr class="upd-sep"><p class="upd-foot-note">Só o número da versão é consultado; nenhum dado seu sai do computador.</p>
    ${voltarLink()}<div class="grp">${closeBtn("Agora não")}<button class="small primary" data-u="go">Atualizar agora</button></div>`, {
    go: () => installInApp(m), voltar: confirmarVolta, close: () => adiarVersao(m.versao),
  });
}

async function esperarReabrir() {
  await new Promise((ok) => setTimeout(ok, 2500));
  for (let i = 0; i < 40; i++) {
    try { if ((await fetch("/api/health?t=" + Date.now(), { cache: "no-store" })).ok) break; } catch {}
    await new Promise((ok) => setTimeout(ok, 500));
  }
  location.reload();
}

async function installInApp(m) {
  if (downloadsAtivos()) return esperarTerminar("atualizar");
  updateBar(`<p>Baixando e conferindo a versão ${esc(m.versao)}…</p>`);
  let r;
  try { r = await api("/api/atualizacao", { method: "POST" }); }
  catch { return updateBar(`<p>O Video Downloader Lab não respondeu. Feche, abra de novo pelo atalho e tente outra vez.</p><div class="grp">${closeBtn()}</div>`); }
  if (!r.ok) return updateBar(`<p>Não consegui instalar a atualização com segurança: ${esc(r.erro || "erro desconhecido")}. Nada foi alterado.</p><div class="grp">${closeBtn()}</div>`);
  if (r.reiniciar) { updateBar(`<p><b>Versão ${esc(r.versao)} instalada.</b> Reabrindo o Video Downloader Lab…</p>`); return esperarReabrir(); }
  updateBar(`<p><b>Versão ${esc(r.versao)} instalada.</b> A versão anterior ficou guardada, caso precise voltar.</p>
    <div class="grp"><button class="small primary" data-u="reload">Recarregar agora</button></div>`, { reload: () => location.reload() });
}

function confirmarVolta() {
  const v = updAnterior;
  if (!desktopApp || !v) return closeUpdate();
  if (downloadsAtivos()) return esperarTerminar("voltar de versão");
  updateBar(`<p><b>Voltar para a versão ${esc(v)}?</b> Depois, se quiser, é só atualizar de novo pelo botão.</p>
    <div class="grp">${closeBtn("Cancelar")}<button class="small primary" data-u="go">Voltar</button></div>`, { go: () => voltarVersao(v) });
}
async function voltarVersao(v) {
  updateBar(`<p>Voltando para a versão ${esc(v)}…</p>`);
  let r;
  try { r = await api("/api/atualizacao/restaurar", { method: "POST" }); }
  catch { return updateBar(`<p>O Video Downloader Lab não respondeu. Feche, abra de novo pelo atalho e tente outra vez.</p><div class="grp">${closeBtn()}</div>`); }
  if (!r.ok) return updateBar(`<p>Não consegui voltar para a versão ${esc(v)}: ${esc(r.erro || "erro desconhecido")}. Nada foi alterado.</p><div class="grp">${closeBtn()}</div>`);
  updateBar(`<p><b>De volta à versão ${esc(r.versao || v)}.</b> ${r.reiniciar ? "Reabrindo o Video Downloader Lab…" : "Recarregando…"}</p>`);
  if (r.reiniciar) return esperarReabrir();
  setTimeout(() => location.reload(), 1200);
}

/* ---------- popups, apoio e tema ---------- */
$("#support-btn").addEventListener("click", () => $("#support-dlg").showModal());
$("#privacy-btn").addEventListener("click", () => $("#privacy-dlg").showModal());
$("#privacy-dlg [data-pop-close-btn]").addEventListener("click", () => $("#privacy-dlg").close());
const SUPPORT_URL = "https://link.mercadopago.com.br/rladevapps";
$("#support-copy").addEventListener("click", async (ev) => {
  const b = ev.currentTarget;
  try { await navigator.clipboard.writeText(SUPPORT_URL); }
  catch {
    const t = document.createElement("textarea"); t.value = SUPPORT_URL; t.style.position = "fixed"; t.style.opacity = "0";
    document.body.append(t); t.select(); try { document.execCommand("copy"); } catch {} t.remove();
  }
  b.textContent = "Copiado!"; clearTimeout(b._t); b._t = setTimeout(() => { b.textContent = "Copiar link"; }, 2000);
});
// Janelas por cima da página: fecham no X, no Esc e clicando fora.
document.querySelectorAll(".pop-dlg").forEach((d) => {
  d.querySelector("[data-pop-close]").addEventListener("click", () => d.close());
  d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
});

const root = document.documentElement;
const darkQuery = matchMedia("(prefers-color-scheme: dark)");
function applyTheme(choice) {
  if (choice === "light" || choice === "dark") root.dataset.theme = choice;
  const mode = choice || root.dataset.theme || (darkQuery.matches ? "dark" : "light");
  root.dataset.mode = mode;
  $("#theme-label").textContent = mode === "dark" ? "Tema escuro" : "Tema claro";
  $("#theme-btn").setAttribute("aria-pressed", String(mode === "dark"));
}
let savedTheme = null;
try { savedTheme = localStorage.getItem("downloaderlab-theme"); } catch {}
applyTheme(savedTheme || "dark");   // abre sempre no escuro, a não ser que a pessoa tenha escolhido o claro
$("#theme-btn").addEventListener("click", () => {
  const next = root.dataset.mode === "dark" ? "light" : "dark";
  applyTheme(next);
  try { localStorage.setItem("downloaderlab-theme", next); } catch {}
});

/* ---------- início ---------- */
if (desktopApp) {
  $("#brand-onde").textContent = "computador";
  document.title = document.title.replace("no seu navegador", "no seu computador");
}
(async function init() {
  try {
    const h = await api("/api/health");
    APP_VERSION = h.version;
    $("#app-version").textContent = h.version;
    if (h.download_dir) {
      $("#where").textContent = h.download_dir;
      $("#where-field").hidden = false;
      $("#open-folder").addEventListener("click", () => api("/api/pasta", { method: "POST" }).catch((err) => showError(err.message)));
    }
  } catch {}
  if (desktopApp) {
    $("#update-btn").hidden = false;
    $("#update-btn").addEventListener("click", () => checkUpdate(true));
    setTimeout(() => checkUpdate(false), 3000);   // consulta discreta ao abrir e a cada 15 min
    setInterval(() => { if (navigator.onLine !== false) checkUpdate(false); }, UPDATE_EVERY);
  }
  refreshJobs();
})();
