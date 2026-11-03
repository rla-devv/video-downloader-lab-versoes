const $ = (id) => document.getElementById(id);

const form = $("url-form");
const urlInput = $("url");
const analyzeBtn = $("analyze-btn");
const formError = $("form-error");
const preview = $("preview");
const qualitySelect = $("quality");
const downloadBtn = $("download-btn");
const jobsList = $("jobs");
const emptyMsg = $("empty");

let currentUrl = null;
let currentReferer = null;
let pollTimer = null;
let desktop = false;

const STATUS_LABEL = {
  queued: "Na fila",
  downloading: "Baixando",
  processing: "Finalizando",
  finished: "Concluído",
  error: "Erro",
};

function showError(msg) {
  formError.textContent = msg;
  formError.hidden = !msg;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Erro ${res.status}`);
  return data;
}

function formatDuration(sec) {
  if (!sec) return "";
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = String(sec % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

function formatBytes(b) {
  if (!b) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(i ? 1 : 0)} ${units[i]}`;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("");
  preview.hidden = true;
  analyzeBtn.disabled = true;
  analyzeBtn.textContent = "Analisando…";
  const lento = setTimeout(() => { analyzeBtn.textContent = "Procurando o vídeo na página…"; }, 4000);
  try {
    const info = await api("/api/info", {
      method: "POST",
      body: JSON.stringify({ url: urlInput.value }),
    });
    currentUrl = info.webpage_url || urlInput.value;
    currentReferer = info.referer || null;
    $("title").textContent = info.title || "(sem título)";
    $("meta").textContent = [info.found_in_page ? `${info.platform} (encontrado na página)` : info.platform,
      info.uploader, formatDuration(info.duration)].filter(Boolean).join(" · ");
    $("thumb").src = info.thumbnail || "";
    $("thumb").hidden = !info.thumbnail;

    qualitySelect.innerHTML = "";
    qualitySelect.append(new Option("Melhor disponível", "best"));
    for (const h of info.qualities) qualitySelect.append(new Option(`${h}p`, String(h)));
    preview.hidden = false;
  } catch (err) {
    showError(err.message);
  } finally {
    clearTimeout(lento);
    analyzeBtn.disabled = false;
    analyzeBtn.textContent = "Analisar";
  }
});

downloadBtn.addEventListener("click", async () => {
  if (!currentUrl) return;
  downloadBtn.disabled = true;
  try {
    await api("/api/downloads", {
      method: "POST",
      body: JSON.stringify({ url: currentUrl, quality: qualitySelect.value, referer: currentReferer }),
    });
    await refreshJobs();
  } catch (err) {
    showError(err.message);
  } finally {
    downloadBtn.disabled = false;
  }
});

function renderJob(job) {
  const li = document.createElement("li");
  li.className = "card job";

  const head = document.createElement("div");
  head.className = "job-head";
  const title = document.createElement("span");
  title.className = "job-title";
  title.textContent = job.title || job.url;
  const status = document.createElement("span");
  status.className = `job-status ${job.status}`;
  status.textContent = STATUS_LABEL[job.status] || job.status;
  head.append(title, status);
  li.append(head);

  if (job.status === "downloading" || job.status === "processing" || job.status === "queued") {
    const bar = document.createElement("div");
    bar.className = "bar";
    const fill = document.createElement("span");
    fill.style.width = `${job.status === "processing" ? 100 : job.progress}%`;
    bar.append(fill);
    li.append(bar);

    const detail = document.createElement("div");
    detail.className = "job-detail";
    const parts = [];
    if (job.stage) parts.push(job.stage);
    if (job.status === "downloading") {
      parts.push(`${job.progress}%`);
      if (job.total_bytes) parts.push(`de ${formatBytes(job.total_bytes)}`);
      if (job.speed) parts.push(`${formatBytes(job.speed)}/s`);
      if (job.eta != null) parts.push(`faltam ${formatDuration(job.eta) || "0:00"}`);
    }
    detail.textContent = parts.join(" · ");
    li.append(detail);
  }

  if (job.status === "finished") {
    const detail = document.createElement("div");
    detail.className = "job-detail";
    detail.textContent = `${job.filename} · ${formatBytes(job.filesize)}`;
    li.append(detail);
    if (desktop) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "save";
      btn.textContent = "Mostrar na pasta";
      btn.addEventListener("click", () =>
        api(`/api/downloads/${job.id}/mostrar`, { method: "POST" }).catch((err) => showError(err.message)));
      li.append(btn);
    } else {
      const link = document.createElement("a");
      link.className = "save";
      link.href = `/api/downloads/${job.id}/file`;
      link.textContent = "Salvar arquivo";
      li.append(link);
    }
  }

  if (job.status === "error") {
    const detail = document.createElement("div");
    detail.className = "job-detail error";
    detail.textContent = job.error;
    li.append(detail);
  }
  return li;
}

async function refreshJobs() {
  let jobs = [];
  try {
    jobs = await api("/api/downloads");
  } catch {
    return;
  }
  jobsList.replaceChildren(...jobs.map(renderJob));
  emptyMsg.hidden = jobs.length > 0;

  const active = jobs.some((j) => ["queued", "downloading", "processing"].includes(j.status));
  clearTimeout(pollTimer);
  if (active) pollTimer = setTimeout(refreshJobs, 1000);
}

async function init() {
  try {
    const h = await api("/api/health");
    desktop = h.desktop;
    $("version").textContent = `versão ${h.version}`;
    if (h.download_dir) $("where").textContent = `Os vídeos ficam em ${h.download_dir}`;
  } catch {}
  refreshJobs();
}

init();
