// Settings page: backend connection + CV in storage.local, criteria in storage.sync.
// The CV sections, criteria and notification email are also saved to the backend profile, which
// the discovery worker uses while this browser is closed.

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
  if (!h.database_ok) problems.push("the server can't reach its database");
  if (!h.data_key_configured) problems.push("the server has no AUTOAPPLIER_DATA_KEY");
  if (!h.openai_key_configured) problems.push("the server has no OPENAI_API_KEY");
  if (!h.account) problems.push(`the account token was not accepted (${h.accountError})`);
  if (problems.length) setStatus("connection-status", "err", `Reachable, but ${problems.join("; ")}.`);
  else setStatus("connection-status", "ok", `Connected as account “${h.account.label || h.account.id}”. The backend is ready.`);
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
    const synced = await syncProfile({ master_cv: cv }, "resume-status");
    if (synced) setStatus("resume-status", "ok", "CV saved and read. Check its sections below.");
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
  if (await syncProfile({ master_cv: editedCv }, "sections-status")) {
    setStatus("sections-status", "ok", "Sections saved.");
  }
}

// ---------- backend profile ----------

// Saves part of the profile on the backend. Returns true on success; on failure the setting is
// still saved in this browser, and the status line says so.
async function syncProfile(patch, statusId) {
  const res = await chrome.runtime.sendMessage({ type: "syncProfile", patch });
  if (res && res.ok) return true;
  setStatus(statusId, "err", `Saved in this browser, but not on the backend: ${(res && res.error) || "no response"}. Automatic job discovery uses the backend copy.`);
  return false;
}

async function saveNotifyEmail() {
  const email = $("notify-email").value.trim();
  if (email && !$("notify-email").checkValidity()) {
    setStatus("notify-status", "err", "That doesn't look like an email address.");
    return;
  }
  if (await syncProfile({ notify_email: email || null }, "notify-status")) {
    setStatus("notify-status", "ok", email ? "Email saved." : "Email removed.");
  }
}

// Loads the backend profile, and uploads local settings the backend doesn't have yet (for
// installs that saved a CV before profiles existed).
async function loadProfile(local, criteria) {
  if (!local.apiKey) return;
  const res = await chrome.runtime.sendMessage({ type: "getProfile" });
  if (!res || !res.ok) {
    setStatus("notify-status", "err", `Couldn't load your backend profile: ${(res && res.error) || "no response"}`);
    return;
  }
  const profile = res.value;
  $("notify-email").value = profile.notify_email || "";
  const missing = {};
  if (!profile.master_cv && local.masterCv) missing.master_cv = local.masterCv;
  if (!profile.criteria && criteria) missing.criteria = criteria;
  if (Object.keys(missing).length) await syncProfile(missing, "notify-status");
}

// ---------- watchlist ----------

const SIMPLIFY_LISTS = {
  "add-simplify-interns": "https://github.com/SimplifyJobs/Summer2027-Internships",
  "add-simplify-newgrad": "https://github.com/SimplifyJobs/New-Grad-Positions",
};

async function send(message) {
  const res = await chrome.runtime.sendMessage(message);
  if (!res || !res.ok) throw new Error((res && res.error) || "No response from the extension.");
  return res.value;
}

function timeAgo(iso) {
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 90) return "just now";
  if (secs < 5400) return `${Math.round(secs / 60)} min ago`;
  if (secs < 129600) return `${Math.round(secs / 3600)} h ago`;
  return `${Math.round(secs / 86400)} days ago`;
}

function sourceStatus(s) {
  if (s.last_error) return { text: `Last check failed: ${s.last_error}`, err: true };
  if (!s.last_polled_at) return { text: "Waiting for its first check (jobs open now are skipped)." };
  const parts = [`Checked ${timeAgo(s.last_polled_at)}`, `${s.open_postings} open`];
  parts.push(`${s.new_postings_7d} new this week`);
  return { text: parts.join(" · ") };
}

const KIND_LABELS = { greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby", simplify: "SimplifyJobs list" };

async function loadAutomation() {
  try {
    const me = await send({ type: "me" });
    $("automation").checked = !!me.automation_enabled;
    $("automation-cap").textContent = `— up to ${me.daily_llm_cap} per day`;
  } catch (e) {
    $("automation").disabled = true;
  }
}

async function setAutomation() {
  const enabled = $("automation").checked;
  try {
    await send({ type: "setAutomation", enabled });
    setStatus("sources-status", "ok", enabled ? "Automatic tailoring is on." : "Automatic tailoring is off.");
  } catch (e) {
    $("automation").checked = !enabled;
    setStatus("sources-status", "err", e.message);
  }
}

async function loadWatchlist() {
  const local = await chrome.storage.local.get("apiKey");
  if (!local.apiKey) return;
  loadAutomation();
  try {
    const [list, recent] = await Promise.all([send({ type: "listSources" }), send({ type: "recentPostings", limit: 15 })]);
    renderSources(list);
    renderPostings(recent);
  } catch (e) {
    setStatus("sources-status", "err", `Couldn't load your watchlist: ${e.message}`);
  }
}

function renderSources(list) {
  $("sources-list").replaceChildren(...list.map((s) => {
    const status = sourceStatus(s);
    const remove = el("button", { textContent: "Remove" });
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      try {
        await send({ type: "removeSource", id: s.id });
        await loadWatchlist();
      } catch (e) {
        remove.disabled = false;
        setStatus("sources-status", "err", e.message);
      }
    });
    return el("div", { className: "source" }, [
      el("div", { className: "grow" }, [
        el("div", { className: "name", textContent: `${s.name || s.slug} — ${KIND_LABELS[s.kind] || s.kind}` }),
        el("div", { className: `meta${status.err ? " err" : ""}`, textContent: status.text }),
      ]),
      remove,
    ]);
  }));
}

function renderPostings(postings) {
  $("postings-empty").hidden = postings.length > 0;
  $("postings").replaceChildren(...postings.map((p) => {
    const label = [p.title, p.company, p.location].filter(Boolean).join(" — ");
    const safe = /^https?:\/\//i.test(p.apply_url) ? p.apply_url : null;
    const link = safe
      ? el("a", { href: safe, target: "_blank", rel: "noopener noreferrer", textContent: label })
      : el("span", { textContent: label });
    return el("li", {}, [link, el("span", { className: "muted", textContent: ` · found ${timeAgo(p.first_seen_at)}` })]);
  }));
}

async function addSource(url) {
  url = (url || "").trim();
  if (!url) {
    setStatus("sources-status", "err", "Paste a job board URL first.");
    return;
  }
  setStatus("sources-status", "", "Checking that board…");
  try {
    const source = await send({ type: "addSource", url });
    $("source-url").value = "";
    setStatus("sources-status", "ok", `Watching ${source.name || source.slug}.`);
    await loadWatchlist();
  } catch (e) {
    setStatus("sources-status", "err", e.message);
  }
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
    const criteria = readCriteria();
    await chrome.storage.sync.set({ criteria });
    if (await syncProfile({ criteria }, "criteria-status")) setStatus("criteria-status", "ok", "Criteria saved.");
  } catch (e) {
    setStatus("criteria-status", "err", e.message);
  }
}

async function resetCriteria() {
  const defaults = await defaultCriteria();
  fillCriteria(defaults);
  await chrome.storage.sync.remove("criteria");
  if (await syncProfile({ criteria: defaults }, "criteria-status")) setStatus("criteria-status", "ok", "Reset to defaults.");
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
  loadProfile(local, criteria);
  loadWatchlist();

  $("save-connection").addEventListener("click", saveConnection);
  $("test-connection").addEventListener("click", testConnection);
  $("resume-file").addEventListener("change", loadResumeFile);
  $("resume").addEventListener("input", updateResumeCount);
  $("save-resume").addEventListener("click", saveResume);
  $("save-sections").addEventListener("click", saveSections);
  $("reparse").addEventListener("click", reparse);
  $("save-criteria").addEventListener("click", saveCriteria);
  $("save-notify").addEventListener("click", saveNotifyEmail);
  $("add-source").addEventListener("click", () => addSource($("source-url").value));
  $("automation").addEventListener("change", setAutomation);
  $("source-url").addEventListener("keydown", (e) => e.key === "Enter" && addSource($("source-url").value));
  Object.entries(SIMPLIFY_LISTS).forEach(([id, url]) => $(id).addEventListener("click", () => addSource(url)));
  $("reset-criteria").addEventListener("click", resetCriteria);
}

init();
