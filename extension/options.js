// Settings page: backend connection + CV in storage.local, criteria in storage.sync.

const $ = (id) => document.getElementById(id);
const DEFAULT_BACKEND_URL = "http://localhost:2005";
const MAX_RESUME_CHARS = 50000; // matches the backend's request limit
const BUILT_IN_ORIGINS = ["http://localhost:2005", "http://127.0.0.1:2005"];

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

async function sha256(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function saveResume() {
  const text = $("resume").value;
  if (!text.trim()) {
    setStatus("resume-status", "err", "The CV is empty.");
    return;
  }
  const hash = await sha256(text);
  const { masterCv, masterCvHash } = await chrome.storage.local.get(["masterCv", "masterCvHash"]);
  await chrome.storage.local.set({ resumeText: text });
  if (masterCv && masterCvHash === hash) {
    setStatus("resume-status", "ok", "CV saved. Its text is unchanged, so its sections were kept.");
    return;
  }
  await parseResume(text, hash);
}

// ---------- CV sections ----------

const SECTION_KINDS = [
  "summary", "experience", "projects", "education", "skills", "coursework",
  "certifications", "publications", "leadership", "languages", "other",
];

let editedCv = null;

async function parseResume(text, hash) {
  // A parse of the old text must not be used with the new text.
  await chrome.storage.local.remove(["masterCv", "masterCvHash", "masterCvUnmatched"]);
  renderSections(null, []);
  $("save-resume").disabled = true;
  $("reparse").disabled = true;
  setStatus("resume-status", "", "CV saved. Reading its sections… this can take up to a minute.");
  try {
    const res = await chrome.runtime.sendMessage({ type: "parseCv", text });
    if (!res || !res.ok) throw new Error((res && res.error) || "No response from the extension.");
    const { master_cv: cv, unmatched_bullets: unmatched } = res.value;
    await chrome.storage.local.set({ masterCv: cv, masterCvHash: hash, masterCvUnmatched: unmatched || [] });
    renderSections(cv, unmatched || []);
    setStatus("resume-status", "ok", "CV saved and read. Check its sections below.");
  } catch (e) {
    setStatus("resume-status", "err", `CV saved, but reading its sections failed: ${e.message}`);
  } finally {
    $("save-resume").disabled = false;
    $("reparse").disabled = false;
  }
}

async function reparse() {
  const { resumeText } = await chrome.storage.local.get("resumeText");
  if (!resumeText || !resumeText.trim()) {
    setStatus("sections-status", "err", "Save your CV first.");
    return;
  }
  await parseResume(resumeText, await sha256(resumeText));
}

function el(tag, props = {}, children = []) {
  const node = Object.assign(document.createElement(tag), props);
  children.forEach((c) => c && node.appendChild(c));
  return node;
}

function renderSections(cv, unmatched) {
  editedCv = cv ? structuredClone(cv) : null;
  $("sections-panel").hidden = !cv;
  const list = $("sections-list");
  list.replaceChildren();
  const missList = $("unmatched").querySelector("ul");
  missList.replaceChildren(...(unmatched || []).map((u) => el("li", { textContent: u })));
  $("unmatched").hidden = !unmatched || unmatched.length === 0;
  if (!cv) return;

  cv.sections.forEach((section, i) => {
    const heading = el("input", { type: "text", value: section.heading, id: `heading-${i}` });
    heading.addEventListener("input", () => (editedCv.sections[i].heading = heading.value));
    const kind = el("select", { id: `kind-${i}` },
      SECTION_KINDS.map((k) => el("option", { value: k, textContent: k })));
    kind.value = section.kind;
    kind.addEventListener("change", () => (editedCv.sections[i].kind = kind.value));

    const entries = section.entries.map((e) => {
      const bits = [e.title || "(untitled)", e.organization, e.dates].filter(Boolean).join(" · ");
      const count = e.bullets.length ? ` (${e.bullets.length} ${e.bullets.length === 1 ? "item" : "items"})` : "";
      return el("li", { textContent: bits + count });
    });

    list.appendChild(el("div", { className: "cv-section" }, [
      el("div", { className: "row" }, [
        el("div", { className: "grow" }, [el("label", { htmlFor: `heading-${i}`, textContent: "Heading" }), heading]),
        el("div", {}, [el("label", { htmlFor: `kind-${i}`, textContent: "Type" }), kind]),
      ]),
      entries.length ? el("ul", {}, entries) : null,
    ]));
  });
}

async function saveSections() {
  if (!editedCv) return;
  if (editedCv.sections.some((s) => !s.heading.trim())) {
    setStatus("sections-status", "err", "Every section needs a heading.");
    return;
  }
  await chrome.storage.local.set({ masterCv: editedCv });
  setStatus("sections-status", "ok", "Sections saved.");
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
  const local = await chrome.storage.local.get(["backendUrl", "apiKey", "resumeText", "masterCv", "masterCvUnmatched"]);
  const { criteria } = await chrome.storage.sync.get("criteria");

  $("backend-url").value = local.backendUrl || DEFAULT_BACKEND_URL;
  $("api-key").value = local.apiKey || "";
  $("resume").value = local.resumeText || "";
  updateResumeCount();
  renderSections(local.masterCv || null, local.masterCvUnmatched || []);
  fillCriteria(criteria || (await defaultCriteria()));

  $("save-connection").addEventListener("click", saveConnection);
  $("test-connection").addEventListener("click", testConnection);
  $("resume-file").addEventListener("change", loadResumeFile);
  $("resume").addEventListener("input", updateResumeCount);
  $("save-resume").addEventListener("click", saveResume);
  $("save-sections").addEventListener("click", saveSections);
  $("reparse").addEventListener("click", reparse);
  $("save-criteria").addEventListener("click", saveCriteria);
  $("reset-criteria").addEventListener("click", resetCriteria);
}

init();
