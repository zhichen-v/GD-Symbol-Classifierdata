const fileInput = document.querySelector("#file-input");
const uploadForm = document.querySelector("#upload-form");
const dropZone = document.querySelector("#drop-zone");
const fileSummary = document.querySelector("#file-summary");
const fileList = document.querySelector("#file-list");
const submitButton = document.querySelector("#submit-button");
const clearButton = document.querySelector("#clear-button");
const refreshButton = document.querySelector("#refresh-button");
const systemPill = document.querySelector("#system-pill");
const emptyState = document.querySelector("#empty-state");
const jobList = document.querySelector("#job-list");
const logoutButton = document.querySelector("#logout-button");

const jobs = new Map();
const pollTimers = new Map();
const terminalStatuses = new Set(["complete", "failed", "cancelled"]);
const openLogJobIds = new Set();
const activeJobsStorageKey = "mip-active-job-ids";

function formatBytes(size) {
  if (!Number.isFinite(size)) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function statusText(status) {
  return {
    queued: "排隊中",
    running: "處理中",
    complete: "完成",
    failed: "失敗",
    cancelled: "已取消",
  }[status] || status || "未知";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function redirectIfUnauthorized(response) {
  if (response.status !== 401) return false;
  window.location.href = "/login";
  return true;
}

function activeJobIds() {
  return Array.from(jobs.values())
    .filter((job) => !terminalStatuses.has(job.status))
    .map((job) => job.id);
}

function storedActiveJobIds() {
  try {
    const rawValue = sessionStorage.getItem(activeJobsStorageKey);
    const parsed = rawValue ? JSON.parse(rawValue) : [];
    return Array.isArray(parsed) ? parsed.filter(Boolean) : [];
  } catch {
    return [];
  }
}

function persistActiveJobs() {
  const ids = activeJobIds();
  if (ids.length) {
    sessionStorage.setItem(activeJobsStorageKey, JSON.stringify(ids));
  } else {
    sessionStorage.removeItem(activeJobsStorageKey);
  }
}

function sendCancel(jobId) {
  const url = `/api/jobs/${encodeURIComponent(jobId)}/cancel`;
  if (navigator.sendBeacon && navigator.sendBeacon(url, new Blob([], { type: "text/plain" }))) {
    return;
  }
  fetch(url, {
    method: "POST",
    credentials: "same-origin",
    keepalive: true,
  }).catch(() => {});
}

function cancelActiveJobs() {
  const ids = new Set([...storedActiveJobIds(), ...activeJobIds()]);
  ids.forEach(sendCancel);
}

function cancelStaleJobsFromPreviousPage() {
  const ids = storedActiveJobIds();
  sessionStorage.removeItem(activeJobsStorageKey);
  ids.forEach(sendCancel);
}

function updateSelectedFiles() {
  const files = Array.from(fileInput.files || []);
  fileSummary.textContent = files.length ? `${files.length} 個檔案已選擇` : "尚未選擇檔案";
  fileList.innerHTML = files
    .map(
      (file) => `
        <div class="file-chip">
          <span>${escapeHtml(file.name)}</span>
          <span>${formatBytes(file.size)}</span>
        </div>
      `,
    )
    .join("");
}

function setFiles(fileListValue) {
  const transfer = new DataTransfer();
  Array.from(fileListValue).forEach((file) => {
    if (file.type.startsWith("image/")) transfer.items.add(file);
  });
  fileInput.files = transfer.files;
  updateSelectedFiles();
}

async function uploadFiles(event) {
  event.preventDefault();
  const files = Array.from(fileInput.files || []);
  if (!files.length) {
    fileSummary.textContent = "請先選擇表格截圖";
    return;
  }

  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));
  submitButton.disabled = true;
  submitButton.textContent = "上傳中";

  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      body: formData,
      credentials: "same-origin",
    });
    if (redirectIfUnauthorized(response)) return;
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Upload failed");
    jobs.set(payload.id, payload);
    persistActiveJobs();
    renderJobs();
    schedulePoll(payload.id);
    fileInput.value = "";
    updateSelectedFiles();
  } catch (error) {
    fileSummary.textContent = error.message;
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "開始處理";
  }
}

async function refreshCurrentJobs() {
  const jobIds = Array.from(jobs.keys());
  if (!jobIds.length) {
    renderJobs();
    return;
  }
  await Promise.all(
    jobIds.map(async (jobId) => {
      try {
        const response = await fetch(`/api/jobs/${jobId}`, { credentials: "same-origin" });
        if (redirectIfUnauthorized(response)) return;
        if (response.status === 404) {
          jobs.delete(jobId);
          return;
        }
        const job = await response.json();
        if (!response.ok) throw new Error(job.detail || "Cannot load job");
        jobs.set(job.id, job);
        if (!terminalStatuses.has(job.status)) schedulePoll(job.id);
      } catch {
        // Keep the current in-memory state if a refresh probe fails.
      }
    }),
  );
  persistActiveJobs();
  renderJobs();
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health", { credentials: "same-origin" });
    if (!response.ok) throw new Error("health failed");
    systemPill.textContent = "API 已連線";
    systemPill.classList.add("ok");
  } catch {
    systemPill.textContent = "API 無回應";
    systemPill.classList.remove("ok");
  }
}

async function syncAuthState() {
  if (!logoutButton) return;
  try {
    const response = await fetch("/api/auth", { credentials: "same-origin" });
    const payload = await response.json();
    logoutButton.hidden = !(payload.enabled && payload.authenticated);
  } catch {
    logoutButton.hidden = true;
  }
}

function schedulePoll(jobId) {
  if (pollTimers.has(jobId)) return;
  const tick = async () => {
    try {
      const response = await fetch(`/api/jobs/${jobId}`, { credentials: "same-origin" });
      if (redirectIfUnauthorized(response)) return;
      const job = await response.json();
      if (!response.ok) throw new Error(job.detail || "Cannot load job");
      jobs.set(job.id, job);
      persistActiveJobs();
      renderJobs();
      if (!terminalStatuses.has(job.status)) {
        pollTimers.set(jobId, setTimeout(tick, 1400));
      } else {
        pollTimers.delete(jobId);
      }
    } catch {
      pollTimers.set(jobId, setTimeout(tick, 2400));
    }
  };
  pollTimers.set(jobId, setTimeout(tick, 250));
}

function renderJobs() {
  rememberOpenLogs();
  const ordered = Array.from(jobs.values()).sort((a, b) =>
    String(b.created_at || "").localeCompare(String(a.created_at || "")),
  );
  const liveJobIds = new Set(ordered.map((job) => job.id));
  openLogJobIds.forEach((jobId) => {
    if (!liveJobIds.has(jobId)) openLogJobIds.delete(jobId);
  });
  emptyState.classList.toggle("hidden", ordered.length > 0);
  jobList.innerHTML = ordered.map(renderJob).join("");
}

function rememberOpenLogs() {
  document.querySelectorAll("details.log-view[data-job-id]").forEach((details) => {
    const jobId = details.dataset.jobId;
    if (!jobId) return;
    if (details.open) {
      openLogJobIds.add(jobId);
    } else {
      openLogJobIds.delete(jobId);
    }
  });
}

function renderJob(job) {
  const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
  const files = (job.files || []).map((file) => file.original_name).join(", ");
  return `
    <article class="job-panel">
      <div class="job-meta">
        <div>
          <h3 class="job-title">${escapeHtml(files || job.id)}</h3>
          <p class="job-subtitle">${escapeHtml(job.id)}</p>
        </div>
        <span class="status-badge ${escapeHtml(job.status)}">${statusText(job.status)}</span>
      </div>
      <div class="progress-track" aria-label="progress">
        <div class="progress-bar" style="width: ${progress}%"></div>
      </div>
      <p class="job-step">${escapeHtml(job.step || "")} · ${progress}%</p>
      ${renderMessages(job.errors || [], job.warnings || [])}
      ${renderResults(job)}
      ${renderLog(job)}
    </article>
  `;
}

function renderMessages(errors, warnings) {
  const errorHtml = errors.length
    ? `
    <ul class="message-list">
      ${errors.map((message) => `<li class="message error">${escapeHtml(message)}</li>`).join("")}
    </ul>
  `
    : "";
  const warningHtml = warnings.length
    ? `
    <details class="warning-view">
      <summary>處理提醒 (${warnings.length})</summary>
      <ul class="message-list compact">
        ${warnings.map((message) => `<li class="message warning">${escapeHtml(message)}</li>`).join("")}
      </ul>
    </details>
  `
    : "";
  if (!errorHtml && !warningHtml) return "";
  return `
    ${errorHtml}
    ${warningHtml}
  `;
}

function renderResults(job) {
  const results = job.results || [];
  if (!results.length) return "";
  return `
    <div class="result-grid">
      ${results.map((result, index) => renderResult(job, result, index)).join("")}
    </div>
  `;
}

function renderResult(job, result, index) {
  const cacheKey = encodeURIComponent(job.updated_at || Date.now());
  const preview = result.preview_url
    ? `<img src="${result.preview_url}?v=${cacheKey}" alt="${escapeHtml(result.source_name)} Excel preview" />`
    : `<div class="preview-missing">預覽圖未產生</div>`;
  const download = result.download_url
    ? `<a class="download-link" href="${result.download_url}">下載 MIP Excel</a>`
    : "";
  const previewLabel = result.preview_source === "fallback_renderer" ? "表格預覽" : "Excel 截圖";
  return `
    <section class="result-frame">
      <div class="preview-wrap">${preview}</div>
      <div class="result-body">
        <h3>${escapeHtml(result.source_name || `Result ${index + 1}`)}</h3>
        <p class="result-meta">
          ${escapeHtml(previewLabel)} · ${escapeHtml(result.row_count ?? "-")} rows · ${escapeHtml(result.validation_status || "-")}
        </p>
        <div class="result-actions">${download}</div>
      </div>
    </section>
  `;
}

function renderLog(job) {
  if (!job.log || !job.log.length) return "";
  const recent = job.log.slice(-80).join("\n");
  const openAttribute = openLogJobIds.has(job.id) ? " open" : "";
  return `
    <details class="log-view" data-job-id="${escapeHtml(job.id)}"${openAttribute}>
      <summary>log</summary>
      <pre>${escapeHtml(recent)}</pre>
    </details>
  `;
}

fileInput.addEventListener("change", updateSelectedFiles);
uploadForm.addEventListener("submit", uploadFiles);
refreshButton.addEventListener("click", refreshCurrentJobs);
logoutButton?.addEventListener("click", async () => {
  cancelActiveJobs();
  try {
    await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
  } finally {
    window.location.replace("/login");
  }
});
jobList.addEventListener(
  "click",
  (event) => {
    const summary = event.target.closest("summary");
    if (!summary) return;
    const details = summary.parentElement;
    if (!details || !details.classList.contains("log-view")) return;
    const jobId = details.dataset.jobId;
    if (!jobId) return;
    if (details.open) {
      openLogJobIds.delete(jobId);
    } else {
      openLogJobIds.add(jobId);
    }
  },
  true,
);
jobList.addEventListener(
  "toggle",
  (event) => {
    const details = event.target;
    if (!details.classList || !details.classList.contains("log-view")) return;
    const jobId = details.dataset.jobId;
    if (!jobId) return;
    if (details.open) {
      openLogJobIds.add(jobId);
    } else {
      openLogJobIds.delete(jobId);
    }
  },
  true,
);
clearButton.addEventListener("click", () => {
  fileInput.value = "";
  updateSelectedFiles();
});

dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropZone.classList.add("dragging");
});

dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragging"));

dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropZone.classList.remove("dragging");
  setFiles(event.dataTransfer.files);
});

window.addEventListener("pagehide", cancelActiveJobs);
window.addEventListener("beforeunload", cancelActiveJobs);

cancelStaleJobsFromPreviousPage();
checkHealth();
syncAuthState();
renderJobs();
