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
  canceled: ["Cancelado", "canceled"],
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
const LINGUAS_COMUNS = [["pt", "Português"], ["en", "Inglês"], ["es", "Espanhol"]];

function showError(msg) { formError.textContent = msg || ""; formError.hidden = !msg; }
// Playlist/canal ou vários links colados: a mesma lista para marcar.
const emLista = () => !!current && (current.kind === "playlist" || current.kind === "lista");
const links = () => urlInput.value.split(/\s+/).map((u) => u.trim()).filter((u) => /^https?:\/\//i.test(u));
const marcado = (nome) => document.querySelector(`input[name="${nome}"]:checked`)?.value;

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
  $("#quality-hint").textContent = emLista()
    ? "Cada vídeo sai na qualidade mais próxima da escolhida que ele tiver."
    : qualities.length ? "Maior qualidade deixa o arquivo maior e o download mais demorado." : "Este site oferece uma qualidade só.";
}

function renderLegendas(lista) {
  // Vídeo: os idiomas que o site tem. Playlist (ou site que não informou): os mais comuns; baixa se o vídeo tiver.
  const box = $("#leg-langs");
  const itens = lista && lista.length ? lista.map((l) => [l.code, l.auto ? `${l.name} (automática)` : l.name])
    : LINGUAS_COMUNS;
  box.innerHTML = itens.map(([c, n], i) => `<label class="lang"><input type="checkbox" value="${esc(c)}"${
    (c.startsWith("pt") && !itens.slice(0, i).some(([x]) => x.startsWith("pt"))) || (itens.length === 1) ? " checked" : ""}><span>${esc(n)}</span></label>`).join("");
  if (!box.querySelector("input:checked")) box.querySelector("input")?.click();
  // Sem a lista do site (playlist, ou o site não informou): idiomas comuns; vêm junto se o vídeo tiver.
  semLista = !(lista && lista.length);
}
let semLista = true;

function atualizarPainel() {
  const audio = marcado("tipo") === "audio";
  $("#f-qualidade").hidden = audio || current?.audio_only;
  $("#seg-fv").hidden = audio;
  $("#seg-fa").hidden = !audio;
  $("#seg-kbps").hidden = !audio || marcado("fa") !== "mp3";
  const fv = marcado("fv"), fa = marcado("fa");
  $("#formato-hint").textContent = audio
    ? { mp3: "MP3 toca em qualquer aparelho. Converter é rápido (no teste, 33 s de áudio levaram 2 s). 320 kbps = melhor som e arquivo maior.",
        m4a: "M4A (AAC) tem som melhor que MP3 no mesmo tamanho; toca em celulares e no Windows. Muitas vezes sai sem converter.",
        original: "O áudio como o site entrega, sem converter: melhor qualidade, mas pode vir em formatos menos comuns (como Opus)." }[fa]
    : { mp4: "MP4 abre em qualquer aparelho. Quase sempre sai sem conversão; quando precisa converter, demora mais (no teste, 33 s de vídeo levaram 4 s).",
        mkv: "MKV aceita qualquer vídeo, áudio e legenda sem converter. Abre no VLC e no Windows 11; alguns celulares e TVs não abrem.",
        webm: "WebM é o formato da web. Quando precisa converter, é bem mais lento (no teste, 33 s de vídeo levaram 16 s).",
        original: "Como o site entrega, sem converter: o mais rápido, mas o formato pode variar de um site para outro." }[fv];
  // Legendas: em áudio e em WebM só como arquivo .srt
  const soArquivo = audio || fv === "webm";
  $("#seg-legmodo").hidden = soArquivo;
  const aviso = semLista ? (emLista() ? " Vêm junto nos vídeos que tiverem legenda nesses idiomas."
    : " O site não informou as legendas deste vídeo: se ele tiver nesses idiomas, elas vêm junto.") : "";
  $("#leg-hint").textContent = (soArquivo
    ? `As legendas ficam num arquivo .srt ao lado do ${audio ? "áudio" : "vídeo"}, com o mesmo nome.`
    : marcado("legmodo") === "embutir"
      ? "Ficam dentro do vídeo: no player, é só ligar a legenda. Não aumenta o tempo do download."
      : "Ficam num arquivo .srt ao lado do vídeo, com o mesmo nome: a maioria dos players acha sozinha.") + aviso;
  $("#leg-box").hidden = !$("#leg-on").checked;
  $("#cut-box").hidden = !$("#cut-on").checked;
  $("#f-trecho").hidden = emLista();
  atualizarBotao();
}

function selecionados() {
  return [...document.querySelectorAll("#pl-list input:checked")].map((c) => current.entries[Number(c.value)]);
}
function atualizarBotao() {
  const audio = marcado("tipo") === "audio";
  if (emLista()) {
    const n = selecionados().length;
    downloadBtn.textContent = n === 0 ? "Marque os vídeos da lista" : n === 1 ? (audio ? "Baixar 1 áudio" : "Baixar 1 vídeo") : `Baixar ${n} ${audio ? "áudios" : "vídeos"}`;
    downloadBtn.disabled = n === 0;
    $("#pl-sum").textContent = `${n} de ${current.entries.filter((e) => !e.erro).length} marcados`;
  } else {
    downloadBtn.textContent = audio ? "Baixar áudio" : "Baixar vídeo";
    downloadBtn.disabled = !current;
  }
}
document.querySelector(".panel").addEventListener("change", atualizarPainel);

function mostrarPlaylist(info) {
  $("#pl").hidden = false;
  const colados = info.kind === "lista";
  // Lista grande: começa desmarcada, para um clique não baixar centenas de vídeos sem querer. Links colados: todos.
  const todos = colados || info.entries.length <= 50;
  $("#pl-list").innerHTML = info.entries.map((e, i) => e.erro
    ? `<li class="erro"><label><input type="checkbox" value="${i}" disabled>
    <span class="n">${i + 1}</span><span class="t">${esc(e.title)}<small>${esc(e.erro)}</small></span><span class="d"></span></label></li>`
    : `<li><label><input type="checkbox" value="${i}"${todos ? " checked" : ""}>
    <span class="n">${i + 1}</span><span class="t" title="${esc(e.title)}">${esc(e.title)}</span><span class="d">${esc(formatDuration(e.duration))}</span></label></li>`).join("");
  $("#pl-hint").textContent = (info.truncated ? `A lista mostra os primeiros ${info.count} vídeos. ` : "")
    + (todos ? "" : "Marque os vídeos que quiser (ou use Marcar todos). ")
    + (colados
      ? (info.erros === 1 ? "Um link deu erro e ficou de fora (o motivo aparece embaixo dele). "
        : info.erros ? `${info.erros} links deram erro e ficaram de fora (o motivo aparece embaixo de cada um). ` : "")
        + "Cada vídeo marcado entra na lista de downloads, com os ajustes ao lado, na pasta principal."
      : "Cada vídeo marcado entra na lista de downloads, com os ajustes ao lado. Eles ficam numa pasta com o nome da playlist.");
}
$("#pl-list").addEventListener("change", atualizarBotao);
$("#pl-all").addEventListener("click", () => { document.querySelectorAll("#pl-list input:not(:disabled)").forEach((c) => { c.checked = true; }); atualizarBotao(); });
$("#pl-none").addEventListener("click", () => { document.querySelectorAll("#pl-list input").forEach((c) => { c.checked = false; }); atualizarBotao(); });

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("");
  $("#preview").hidden = true;
  current = null;
  atualizarBotao();
  analyzeBtn.disabled = true;
  analyzeBtn.textContent = "Analisando…";
  const lento = setTimeout(() => { analyzeBtn.textContent = "Procurando o vídeo na página…"; }, 4000);
  try {
    const lista = links();
    if (!lista.length) throw new Error("Cole um link começando com http:// ou https://");
    const info = await api("/api/info", { method: "POST", body: JSON.stringify({ url: lista[0], urls: lista.length > 1 ? lista : null, conta: marcado("conta") }) });
    const playlist = info.kind === "playlist" || info.kind === "lista";
    current = { ...info, url: info.webpage_url || lista[0], referer: info.referer || null,
                qualities: info.qualities || [], subtitlesKnown: !playlist };
    $("#title").textContent = info.title || "(sem título)";
    const specs = [[info.platform, "Site"], [info.uploader, "Canal"],
      playlist ? [`${info.count - (info.erros || 0)}${info.truncated ? "+" : ""}`, "Vídeos"] : [formatDuration(info.duration), "Duração"],
      info.erros ? [String(info.erros), "Com erro"] : [null]]
      .filter(([v]) => v).map(([v, k]) => `<span>${esc(k)}: <b>${esc(v)}</b></span>`);
    $("#specs").innerHTML = specs.join("");
    $("#found").hidden = !info.found_in_page;
    const img = $("#thumb");
    const capa = info.thumbnail || (playlist && info.entries.find((x) => x.thumbnail)?.thumbnail);
    if (capa) img.src = capa; else img.removeAttribute("src");
    $("#thumb-box").hidden = playlist && !capa;
    $("#pl").hidden = true;
    if (playlist) mostrarPlaylist(info);
    renderQualities(playlist ? [2160, 1440, 1080, 720, 480, 360] : current.qualities);
    renderLegendas(playlist ? [] : info.subtitles);
    if (info.audio_only) document.querySelector('input[name="tipo"][value="audio"]').checked = true;
    $("#cut-ini").value = ""; $("#cut-fim").value = "";
    $("#preview").hidden = false;
    $("#go-sum").textContent = "";
    atualizarPainel();
  } catch (err) {
    showError(err.message);
  } finally {
    clearTimeout(lento);
    analyzeBtn.disabled = false;
    analyzeBtn.textContent = "Analisar";
  }
});

function opcoes() {
  const tipo = marcado("tipo");
  const o = { tipo, qualidade: marcado("quality") || "best", formato_video: marcado("fv"), formato_audio: marcado("fa"),
              kbps: marcado("kbps"), capa: $("#capa").checked, legendas: [], legendas_modo: marcado("legmodo"),
              conta: marcado("conta") };
  if ($("#leg-on").checked) o.legendas = [...document.querySelectorAll("#leg-langs input:checked")].map((c) => c.value);
  if ($("#cut-on").checked && !emLista()) {
    o.inicio = $("#cut-ini").value.trim() || null;
    o.fim = $("#cut-fim").value.trim() || null;
    o.corte_exato = $("#cut-exato").checked;
  }
  return o;
}

downloadBtn.addEventListener("click", async () => {
  if (!current) return;
  showError("");
  const o = opcoes();
  if ($("#leg-on").checked && !o.legendas.length) return showError("Escolha pelo menos um idioma de legenda, ou desligue as legendas.");
  if ($("#cut-on").checked && !emLista() && !o.inicio && !o.fim) return showError("Informe o início ou o fim do trecho.");
  downloadBtn.disabled = true;
  try {
    if (emLista()) {
      const itens = selecionados().map(({ url, referer, title, thumbnail }) => ({ url, referer: referer || null, title, thumbnail }));
      // links colados vão para a pasta principal; playlist e canal, para uma pasta com o nome deles
      const pasta = current.kind === "playlist" ? current.title : null;
      const r = await api("/api/downloads/lote", { method: "POST", body: JSON.stringify({ itens, opcoes: o, pasta }) });
      $("#go-sum").textContent = `${r.criados} na lista`;
    } else {
      await api("/api/downloads", { method: "POST", body: JSON.stringify({
        url: current.url, opcoes: o, referer: current.referer, title: current.title, thumbnail: current.thumbnail }) });
      $("#go-sum").textContent = "Na lista de downloads";
    }
    await refreshJobs();
  } catch (err) {
    showError(err.message);
  } finally {
    atualizarBotao();
  }
});

/* ---------- campo de links e conta ---------- */
// Enter analisa; Shift+Enter (ou colar várias linhas) põe um link por linha. A caixa cresce com o conteúdo.
function ajustarAltura() { urlInput.style.height = "auto"; urlInput.style.height = Math.min(urlInput.scrollHeight + 3, 220) + "px"; }
urlInput.addEventListener("input", ajustarAltura);
urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
});
const CONTA_DICA = {
  nenhuma: "",
  firefox: "Recomendado. Entre na conta do site no Firefox antes de analisar; o app lê o login de lá.",
  edge: "Feche o Edge por completo antes de analisar. As versões novas às vezes não deixam outros programas lerem o login: se der erro, use o Firefox ou um cookies.txt.",
  chrome: "Feche o Chrome por completo antes de analisar. As versões novas às vezes não deixam outros programas lerem o login: se der erro, use o Firefox ou um cookies.txt.",
  brave: "Feche o Brave por completo antes de analisar. Se der erro, use o Firefox ou um cookies.txt.",
  opera: "Feche o Opera por completo antes de analisar. Se der erro, use o Firefox ou um cookies.txt.",
  arquivo: "Funciona com qualquer navegador: logado no site, exporte o cookies.txt com uma extensão de navegador que faça isso e escolha o arquivo aqui. Ele fica só neste computador. Quem tem o arquivo entra na sua conta: não envie para ninguém.",
};
let temCookies = false;
function atualizarConta() {
  const c = marcado("conta");
  $("#conta-hint").textContent = CONTA_DICA[c] || "";
  $("#conta-hint").hidden = !CONTA_DICA[c];
  $("#cookies-box").hidden = c !== "arquivo";
  $("#cookies-status").textContent = temCookies ? "Arquivo guardado neste computador." : "Nenhum arquivo escolhido ainda.";
  $("#cookies-del").hidden = !temCookies;
}
$("#seg-conta").addEventListener("change", () => {
  try { localStorage.setItem("downloaderlab-conta", marcado("conta")); } catch {}
  atualizarConta();
});
$("#cookies-file").addEventListener("change", async (e) => {
  const arq = e.target.files[0];
  e.target.value = "";
  if (!arq) return;
  try {
    await api("/api/conta/cookies", { method: "POST", body: JSON.stringify({ texto: await arq.text() }) });
    temCookies = true; showError("");
  } catch (err) { showError(err.message); }
  atualizarConta();
});
$("#cookies-del").addEventListener("click", async () => {
  await api("/api/conta/cookies", { method: "DELETE" }).catch(() => {});
  temCookies = false; atualizarConta();
});
try {
  const salva = localStorage.getItem("downloaderlab-conta");
  const radio = salva && document.querySelector(`input[name="conta"][value="${CSS.escape(salva)}"]`);
  if (radio) { radio.checked = true; if (salva !== "nenhuma") $("#conta").open = true; }
} catch {}

/* ---------- lista de downloads ---------- */
const ATIVOS = ["queued", "downloading", "processing"];
function renderJob(job) {
  const [label, kind] = STATUS[job.status] || [job.status, ""];
  const o = job.opcoes || {};
  const card = document.createElement("article");
  card.className = "card";
  const thumb = job.thumbnail ? `<img src="${esc(job.thumbnail)}" alt="" loading="lazy">` : "";
  const dados = [];
  if (job.status === "finished") dados.push(`<span><b>${esc(formatBytes(job.filesize))}</b></span>`);
  else if (job.total_bytes && job.status !== "canceled") dados.push(`<span>${esc(job.stage || "arquivo")}: <b>${esc(formatBytes(job.total_bytes))}</b></span>`);
  else if (job.stage) dados.push(`<span>${esc(job.stage)}</span>`);
  if (o.tipo === "audio") dados.push(`<span>Áudio: <b>${esc((o.formato_audio || "").toUpperCase())}${o.formato_audio === "mp3" ? " " + esc(o.kbps) + " kbps" : ""}</b></span>`);
  else dados.push(`<span>Qualidade: <b>${o.qualidade === "best" ? "melhor" : esc(o.qualidade) + "p"}</b></span>`);
  if (o.inicio != null || o.fim != null) dados.push(`<span>Trecho: <b>${esc(formatDuration(o.inicio || 0))}–${o.fim != null ? esc(formatDuration(o.fim)) : "fim"}</b></span>`);
  if (o.legendas && o.legendas.length) dados.push(`<span>Legendas: <b>${esc(o.legendas.join(", "))}</b></span>`);
  if (job.pasta) dados.push(`<span>Pasta: <b>${esc(job.pasta)}</b></span>`);
  card.innerHTML = `<div class="card-head"><div class="thumb">${thumb}</div>
    <div class="card-txt"><span class="name">${esc(job.title || job.url)}</span><div class="specs">${dados.join("")}</div></div>
    <span class="pill ${kind}">${esc(label)}</span></div>`;

  if (ATIVOS.includes(job.status)) {
    const pct = job.status === "processing" ? 100 : job.progress || 0;
    const right = job.status === "processing" ? (job.stage || "finalizando")
      : [job.speed ? `${formatBytes(job.speed)}/s` : "", job.eta != null ? `faltam ${formatDuration(job.eta)}` : ""].filter(Boolean).join(" · ");
    card.insertAdjacentHTML("beforeend", `<div class="progress"><div class="track"><div class="fill" style="width:${pct}%"></div></div>
      <div class="meta"><span class="pct">${job.status === "queued" ? "esperando" : nf(pct) + "%"}</span><span>${esc(right)}</span></div></div>`);
    const actions = document.createElement("div");
    actions.className = "actions";
    const cancel = document.createElement("button");
    cancel.type = "button"; cancel.className = "small"; cancel.textContent = "Cancelar";
    cancel.addEventListener("click", () => api(`/api/downloads/${job.id}/cancelar`, { method: "POST" }).then(refreshJobs).catch((err) => showError(err.message)));
    actions.append(cancel);
    card.append(actions);
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
    if (job.extras && job.extras.length) card.insertAdjacentHTML("beforeend", `<p class="extras">Legendas: ${job.extras.map(esc).join(" · ")}</p>`);
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
  const naFila = jobs.filter((j) => j.status === "queued").length;
  $("#cancel-queue").hidden = naFila < 2;
  $("#cancel-queue").textContent = `Cancelar os ${naFila} da fila`;
  $("#clear-done").hidden = !jobs.some((j) => !ATIVOS.includes(j.status));
  clearTimeout(pollTimer);
  if (jobs.some((j) => ATIVOS.includes(j.status))) pollTimer = setTimeout(refreshJobs, 1000);
}
const downloadsAtivos = () => jobsCache.some((j) => ATIVOS.includes(j.status));
$("#cancel-queue").addEventListener("click", () => api("/api/downloads/cancelar-fila", { method: "POST" }).then(refreshJobs));
$("#clear-done").addEventListener("click", () => api("/api/downloads/limpar", { method: "POST" }).then(refreshJobs));

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

/* ---------- pasta do app (padrão C:\\rla-lab) ---------- */
const MOVER_ADIADO = "downloaderlab-mover-adiado";
async function verPasta() {
  let r;
  try { r = await api("/api/instalacao"); } catch { return; }
  if (r && r.videos_movidos) {   // os vídeos de antes acabaram de vir para a pasta do app: avisa uma vez
    const v = r.videos_movidos;
    $("#mover-title").textContent = "Seus vídeos mudaram de pasta";
    $("#mover-body").innerHTML = `<p>${v.quantos === 1 ? "O arquivo que estava" : `Os ${v.quantos} arquivos que estavam`} em <span class="mono">${esc(v.de)}</span> agora ${v.quantos === 1 ? "está" : "estão"} em <b class="mono">${esc(v.para)}</b>, junto do app.</p>
      <p class="upd-note">Os vídeos novos também vão para lá. Para abrir, use "Abrir a pasta" no painel ao lado.</p>
      <div class="grp"><button class="small" type="button" data-mover="abrir">Abrir a pasta</button><button class="small primary" type="button" data-mover="ok">Entendi</button></div>`;
    $("#mover-dlg").showModal();
    return;
  }
  if (!r || r.no_padrao) return;
  $("#mover-atual").textContent = r.pasta;
  $("#mover-destino").textContent = r.destino;
  $("#mover-btn").hidden = false;
  let adiado = 0;
  try { adiado = Number(localStorage.getItem(MOVER_ADIADO) || 0); } catch {}
  if (Date.now() - adiado > 30 * 864e5) $("#mover-dlg").showModal();   // pergunta de novo depois de 30 dias
}
async function moverAgora() {
  if (downloadsAtivos()) { $("#mover-body").innerHTML = "<p>Há um download em andamento. Espere terminar para mover o programa.</p>"; return; }
  $("#mover-body").innerHTML = "<p>Movendo o programa… o Video Downloader Lab reabre sozinho em instantes.</p>";
  try {
    const r = await api("/api/instalacao/mover", { method: "POST" });
    if (!r.ok) throw new Error(r.erro || "erro desconhecido");
    $("#mover-body").innerHTML = `<p><b>Pronto.</b> O programa agora está em <span class="mono">${esc(r.pasta)}</span>. Reabrindo…</p>`;
    esperarReabrir();
  } catch (err) {
    $("#mover-body").innerHTML = `<p>Não consegui mover o programa: ${esc(err.message)}. Nada foi alterado; o app continua funcionando onde está.</p>`;
  }
}
document.querySelector("#mover-dlg").addEventListener("click", (e) => {
  const k = e.target.dataset?.mover;
  if (k === "ir") moverAgora();
  if (k === "depois") { try { localStorage.setItem(MOVER_ADIADO, String(Date.now())); } catch {} $("#mover-dlg").close(); }
  if (k === "ok") $("#mover-dlg").close();
  if (k === "abrir") { api("/api/pasta", { method: "POST" }).catch((err) => showError(err.message)); $("#mover-dlg").close(); }
});
$("#mover-btn").addEventListener("click", () => $("#mover-dlg").showModal());

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
    temCookies = !!h.cookies_arquivo;
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
    setTimeout(verPasta, 1500);
    setInterval(() => { if (navigator.onLine !== false) checkUpdate(false); }, UPDATE_EVERY);
  }
  atualizarPainel();
  atualizarConta();
  refreshJobs();
})();
