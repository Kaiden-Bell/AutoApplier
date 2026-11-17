// Settings page: backend connection + CV in storage.local, criteria in storage.sync.

const $ = (id) => document.getElementById(id);
const DEFAULT_BACKEND_URL = "http://localhost:8000";
const MAX_RESUME_CHARS = 50000; // matches the backend's request limit
const BUILT_IN_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000"];

function setStatus(id, kind, text) {
  const node = $(id);
  node.className = `status ${kind}`;
  node.textContent = text;
  if (kind === "ok") setTimeout(() => node.textContent === text && (node.textContent = ""), 3000);
}

const splitList = (text) => text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
const joinList = (list) => (list || []).join(", ");

async function defaultCriteria() {
  const res = await fetch(chrome.runtime.getURL("criteria.json"));
  return res.json();
}

// ---------- connection ----------

function normalizeUrl(raw) {
  const url = new URL((raw || DEFAULT_BACKEND_URL).trim());
  if (!/^https?:$/.test(url.protocol)) throw new Error("The URL must start with http:// or https://");
  return url.origin + url.pathname.replace(/\/+$/, "");
}

function saveConnection() {
  let backendUrl;
  try {
    backendUrl = normalizeUrl($("backend-url").value);
  } catch (e) {
    setStatus("connection-status", "err", e.message || "Invalid URL.");
    return;
  }
  const origin = new URL(backendUrl).origin;
  // permissions.request must run directly inside the click handler (user gesture).
  const permission = BUILT_IN_ORIGINS.includes(origin)
    ? Promise.resolve(true)
    : chrome.permissions.request({ origins: [origin + "/*"] });

  permission
    .then((granted) => {
      if (!granted) throw new Error(`Permission to contact ${origin} was not granted.`);
      return chrome.storage.local.set({ backendUrl, apiKey: $("api-key").value.trim() });
    })
    .then(() => {
      $("backend-url").value = backendUrl;
      setStatus("connection-status", "ok", "Saved.");
    })
    .catch((e) => setStatus("connection-status", "err", e.message));
}

async function testConnection() {
  setStatus("connection-status", "", "Testing…");
  const res = await chrome.runtime.sendMessage({ type: "health" });
  if (!res || !res.ok) {
    setStatus("connection-status", "err", (res && res.error) || "No response.");
    return;
  }
  const h = res.value;
  const problems = [];
  if (!h.pdflatex_available) problems.push("pdflatex is not installed on the server");
  if (!h.api_key_configured) problems.push("the server has no AUTOAPPLIER_API_KEY");
  if (!h.openai_key_configured) problems.push("the server has no OPENAI_API_KEY");
  if (problems.length) setStatus("connection-status", "err", `Reachable, but ${problems.join("; ")}.`);
  else setStatus("connection-status", "ok", "Connected. The backend is ready.");
}

// ---------- resume ----------

function updateResumeCount() {
  const n = $("resume").value.length;
  $("resume-count").textContent = `— ${n.toLocaleString()} / ${MAX_RESUME_CHARS.toLocaleString()} characters`;
}

async function loadResumeFile(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  const text = await file.text();
  if (text.length > MAX_RESUME_CHARS) {
    setStatus("resume-status", "err", `That file has ${text.length.toLocaleString()} characters; the limit is ${MAX_RESUME_CHARS.toLocaleString()}.`);
    return;
  }
  $("resume").value = text;
  updateResumeCount();
  setStatus("resume-status", "", `Loaded ${file.name}. Click Save CV to keep it.`);
}

async function saveResume() {
  const text = $("resume").value;
  if (!text.trim()) {
    setStatus("resume-status", "err", "The CV is empty.");
    return;
  }
  await chrome.storage.local.set({ resumeText: text });
  setStatus("resume-status", "ok", "CV saved.");
}

// ---------- criteria ----------

function fillCriteria(c) {
  $("target-tokens").value = joinList(c.target_tokens);
  $("banned-tokens").value = joinList(c.banned_tokens);
  $("allowed-locations").value = joinList(c.allowed_locations);
  $("min-hourly").value = c.minimum_hourly_rate ?? 0;
  $("min-annual").value = c.minimum_annual_salary ?? 0;
}

function readCriteria() {
  const num = (id) => Math.max(0, Number($(id).value) || 0);
  return {
    target_tokens: splitList($("target-tokens").value),
    banned_tokens: splitList($("banned-tokens").value),
    allowed_locations: splitList($("allowed-locations").value),
    minimum_hourly_rate: num("min-hourly"),
    minimum_annual_salary: num("min-annual"),
  };
}

async function saveCriteria() {
  try {
    await chrome.storage.sync.set({ criteria: readCriteria() });
    setStatus("criteria-status", "ok", "Criteria saved.");
  } catch (e) {
    setStatus("criteria-status", "err", e.message);
  }
}

async function resetCriteria() {
  const defaults = await defaultCriteria();
  fillCriteria(defaults);
  await chrome.storage.sync.remove("criteria");
  setStatus("criteria-status", "ok", "Reset to defaults.");
}

// ---------- init ----------

async function init() {
  const local = await chrome.storage.local.get(["backendUrl", "apiKey", "resumeText"]);
  const { criteria } = await chrome.storage.sync.get("criteria");

  $("backend-url").value = local.backendUrl || DEFAULT_BACKEND_URL;
  $("api-key").value = local.apiKey || "";
  $("resume").value = local.resumeText || "";
  updateResumeCount();
  fillCriteria(criteria || (await defaultCriteria()));

  $("save-connection").addEventListener("click", saveConnection);
  $("test-connection").addEventListener("click", testConnection);
  $("resume-file").addEventListener("change", loadResumeFile);
  $("resume").addEventListener("input", updateResumeCount);
  $("save-resume").addEventListener("click", saveResume);
  $("save-criteria").addEventListener("click", saveCriteria);
  $("reset-criteria").addEventListener("click", resetCriteria);
}

init();
