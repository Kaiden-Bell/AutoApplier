// Popup: extract and confirm the posting, start tailoring, and show the latest run.
// All backend traffic goes through background.js; the run state is read from storage.session.

const $ = (id) => document.getElementById(id);
const FIELDS = ["title", "location", "pay", "description"];
let currentTab = null;
let elapsedTimer = null;

function send(message) {
  return chrome.runtime.sendMessage(message).then((res) => {
    if (!res || !res.ok) throw new Error((res && res.error) || "No response from the extension.");
    return res.value;
  });
}

function el(tag, { text, cls, attrs } = {}, children = []) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  if (attrs) Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  children.forEach((c) => c && node.appendChild(c));
  return node;
}

function button(label, onClick, cls) {
  const b = el("button", { text: label, cls });
  b.addEventListener("click", onClick);
  return b;
}

// ---------- setup banner ----------

async function checkSetup() {
  const { resumeText, apiKey } = await chrome.storage.local.get(["resumeText", "apiKey"]);
  const missing = [];
  if (!resumeText || !resumeText.trim()) missing.push("your master CV");
  if (!apiKey) missing.push("the backend API key");
  $("setup-banner").hidden = missing.length === 0;
  $("setup-text").textContent = missing.length ? `Add ${missing.join(" and ")} in settings before tailoring.` : "";
  return missing.length === 0;
}

// ---------- job form ----------

function readForm() {
  const job = {};
  FIELDS.forEach((f) => (job[f] = $(f).value.trim()));
  job.url = currentTab && currentTab.url ? currentTab.url : "";
  return job;
}

function fillForm(job) {
  FIELDS.forEach((f) => ($(f).value = (job && job[f]) || ""));
}

function saveDraft() {
  chrome.storage.session.set({ draftJob: { ...readForm(), tabUrl: currentTab ? currentTab.url : "" } });
}

function showSource(source) {
  const badge = $("source-badge");
  const note = $("extract-note");
  badge.hidden = !source;
  note.hidden = !source;
  if (source === "jsonld") {
    badge.textContent = "Structured job data";
    note.textContent = "Fields came from the page's schema.org JobPosting data. Review them before sending.";
  } else if (source === "heuristic") {
    badge.textContent = "Best guess";
    note.textContent = "This page has no structured job data. Check the title, add the location and pay, and trim the description to the posting (tip: select the posting text on the page, then extract again).";
  }
}

async function extract() {
  if (!currentTab || !/^https?:/.test(currentTab.url || "")) {
    showSource(null);
    $("extract-note").hidden = false;
    $("extract-note").textContent = "Open a job posting page (http/https) first.";
    return;
  }
  $("extract").disabled = true;
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: currentTab.id },
      files: ["jsonld.js", "content_script.js"],
    });
    const value = results && results[0] && results[0].result;
    if (!value || !value.job) throw new Error("Nothing could be extracted from this page.");
    fillForm(value.job);
    showSource(value.source);
    saveDraft();
  } catch (e) {
    $("extract-note").hidden = false;
    $("extract-note").textContent = `Could not read this page: ${e.message}`;
  } finally {
    $("extract").disabled = false;
  }
}

async function tailor() {
  const job = readForm();
  if (!job.description) {
    renderMessage("err", "Missing description", "Extract the posting or paste the job description first.");
    return;
  }
  if (!(await checkSetup())) return;
  $("tailor").disabled = true;
  try {
    await send({ type: "tailor", job });
  } catch (e) {
    renderMessage("err", "Could not start", e.message);
    $("tailor").disabled = false;
  }
}

// ---------- run status ----------

function renderMessage(kind, heading, body, extra = []) {
  const box = $("run-status");
  box.replaceChildren(el("div", { cls: `card ${kind}` }, [el("h3", { text: heading }), body ? el("p", { text: body }) : null, ...extra]));
  box.hidden = false;
}

function renderRun(run) {
  clearInterval(elapsedTimer);
  const box = $("run-status");
  $("tailor").disabled = !!run && run.status === "running";
  if (!run) {
    box.hidden = true;
    return;
  }
  const title = run.job && run.job.title ? `“${run.job.title}”` : "this posting";
  const dismiss = button("Dismiss", () => send({ type: "clear" }));

  if (run.status === "running") {
    const status = el("p", { cls: "muted" });
    const tick = () => {
      const secs = Math.round((Date.now() - run.startedAt) / 1000);
      status.textContent = `${run.phase === "submitting" ? "Sending" : "Generating and compiling"}… ${secs}s. This usually takes under a minute. You can close this popup; the PDF downloads automatically.`;
    };
    tick();
    elapsedTimer = setInterval(tick, 1000);
    box.replaceChildren(el("div", { cls: "card" }, [el("h3", {}, [el("span", { cls: "spinner" }), document.createTextNode(`Tailoring for ${title}`)]), status]));
    box.hidden = false;
    return;
  }

  if (run.status === "error") {
    renderMessage("err", "Something went wrong", run.error, [el("div", { cls: "row" }, [dismiss])]);
    return;
  }

  const r = run.result || {};
  if (r.stage === "criteria") {
    renderMessage("warn", "Skipped by your criteria", r.reason, [
      el("p", { cls: "muted", text: "No LLM call was made. Adjust your criteria in settings if this is wrong." }),
      el("div", { cls: "row" }, [dismiss]),
    ]);
  } else if (r.stage === "llm") {
    renderMessage("warn", "Not a strong match", r.gap_analysis || r.reason, [el("div", { cls: "row" }, [dismiss])]);
  } else if (r.stage === "compile") {
    renderMessage("err", "Resume generated, but it didn't compile", r.reason, [
      r.gap_analysis ? el("p", { cls: "muted", text: r.gap_analysis }) : null,
      el("pre", { text: r.compile_log || "(no log)" }),
      el("p"),
      el("div", { cls: "row" }, [button("Download .tex", () => send({ type: "download", kind: "tex" }), "primary"), dismiss]),
    ]);
  } else if (r.stage === "complete") {
    const overflow = r.page_count > 1;
    const note = r.reason && r.reason !== "Resume generated." ? r.reason : "";
    renderMessage(overflow ? "warn" : "ok", overflow ? `Resume is ${r.page_count} pages` : `Resume ready for ${title}`,
      "The PDF was saved to your Downloads/AutoApplier folder. Open it and review it before you apply.", [
      note ? el("p", { text: note }) : null,
      r.gap_analysis ? el("p", { cls: "muted", text: r.gap_analysis }) : null,
      el("div", { cls: "row" }, [
        button("Download PDF again", () => send({ type: "download", kind: "pdf" }), "primary"),
        button("Download .tex", () => send({ type: "download", kind: "tex" })),
        dismiss,
      ]),
    ]);
  } else {
    renderMessage("err", "Unexpected response", "The backend returned a result this extension doesn't understand.", [dismiss]);
  }
}

// ---------- init ----------

async function init() {
  $("extract").addEventListener("click", extract);
  $("tailor").addEventListener("click", tailor);
  $("open-settings").addEventListener("click", () => chrome.runtime.openOptionsPage());
  FIELDS.forEach((f) => $(f).addEventListener("input", saveDraft));

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "session" && changes.lastRun) renderRun(changes.lastRun.newValue);
    if (area === "local" && (changes.resumeText || changes.apiKey)) checkSetup();
  });

  [currentTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  checkSetup();

  const run = await send({ type: "resume" }).catch(() => null);
  const { lastRun, draftJob } = await chrome.storage.session.get(["lastRun", "draftJob"]);
  renderRun(lastRun || run);

  if (draftJob && currentTab && draftJob.tabUrl === currentTab.url) {
    fillForm(draftJob);
  } else {
    extract();
  }
}

init();
