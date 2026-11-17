// AutoApplier background service worker: the only code that talks to the backend.
//
// Run state lives in chrome.storage.session ("lastRun") so the popup can close and reopen at
// any time. The backend queues each generation and is polled, because MV3 terminates a
// service worker whose fetch() takes longer than 30 seconds to respond.

const DEFAULT_BACKEND_URL = "http://localhost:8000";
const POLL_INTERVAL_MS = 3000;
const MAX_WAIT_MS = 5 * 60 * 1000;
const MAX_POLL_FAILURES = 5;

// True while this service-worker instance is actively submitting or polling a run.
let runActive = false;

chrome.runtime.onInstalled.addListener(({ reason }) => {
  if (reason === chrome.runtime.OnInstalledReason.INSTALL) {
    chrome.tabs.create({ url: chrome.runtime.getURL("welcome.html") });
  }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const handlers = {
    tailor: () => startTailor(message.job),
    resume: () => resumeIfNeeded(),
    download: () => downloadFromLastRun(message.kind),
    health: () => checkHealth(),
    clear: () => chrome.storage.session.remove("lastRun"),
  };
  const handler = handlers[message && message.type];
  if (!handler) return false;
  Promise.resolve()
    .then(handler)
    .then((value) => sendResponse({ ok: true, value }), (err) => sendResponse({ ok: false, error: err.message }));
  return true; // async response
});

// ---------- config & storage ----------

async function defaultCriteria() {
  const res = await fetch(chrome.runtime.getURL("criteria.json"));
  return res.json();
}

async function getConfig() {
  const local = await chrome.storage.local.get(["backendUrl", "apiKey", "resumeText"]);
  const { criteria } = await chrome.storage.sync.get("criteria");
  return {
    backendUrl: (local.backendUrl || DEFAULT_BACKEND_URL).replace(/\/+$/, ""),
    apiKey: local.apiKey || "",
    resumeText: local.resumeText || "",
    criteria: criteria || (await defaultCriteria()),
  };
}

async function getRun() {
  const { lastRun } = await chrome.storage.session.get("lastRun");
  return lastRun || null;
}

async function setRun(run) {
  await chrome.storage.session.set({ lastRun: run });
  return run;
}

// ---------- backend calls ----------

function describeError(status, data) {
  const detail = data && data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => `${(d.loc || []).slice(-1)[0] || "input"}: ${d.msg}`).join("; ");
  }
  if (status === 0) return "Could not reach the backend.";
  return `Backend returned HTTP ${status}.`;
}

async function api(cfg, path, { method = "GET", body, headers = {} } = {}) {
  let res;
  try {
    res = await fetch(cfg.backendUrl + path, {
      method,
      headers: { "Content-Type": "application/json", "X-AutoApplier-Key": cfg.apiKey, ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    const err = new Error(`Could not reach the backend at ${cfg.backendUrl}. Is it running?`);
    err.transient = true;
    throw err;
  }
  let data = null;
  try {
    data = await res.json();
  } catch (e) {
    data = null;
  }
  if (!res.ok) {
    const err = new Error(describeError(res.status, data));
    err.status = res.status;
    err.transient = res.status >= 500 && res.status !== 503;
    throw err;
  }
  return data;
}

async function checkHealth() {
  const cfg = await getConfig();
  const res = await fetch(cfg.backendUrl + "/health").catch(() => null);
  if (!res || !res.ok) throw new Error(`Could not reach the backend at ${cfg.backendUrl}.`);
  return res.json();
}

// ---------- tailoring flow ----------

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Extension API calls reset the service worker's idle timer while we wait between polls.
function startKeepAlive() {
  const id = setInterval(() => chrome.runtime.getPlatformInfo(() => {}), 20000);
  return () => clearInterval(id);
}

async function startTailor(job) {
  const cfg = await getConfig();
  if (!cfg.resumeText.trim()) throw new Error("Add your master CV in AutoApplier settings first.");
  if (!cfg.apiKey) throw new Error("Add the backend API key in AutoApplier settings first.");
  if (!job || !String(job.description || "").trim()) throw new Error("The job description is empty.");

  if (runActive) throw new Error("A resume is already being generated. Wait for it to finish.");

  const run = await setRun({
    status: "running",
    phase: "submitting",
    startedAt: Date.now(),
    requestId: null,
    idempotencyKey: crypto.randomUUID(),
    job: { title: job.title || "", location: job.location || "", url: job.url || "" },
  });

  // Respond to the popup right away; the rest continues in the background.
  submitAndPoll(run, cfg, job);
  return { started: true };
}

async function submitAndPoll(run, cfg, job) {
  runActive = true;
  const stopKeepAlive = startKeepAlive();
  try {
    const body = {
      job: {
        title: job.title || "",
        location: job.location || "",
        pay: job.pay || "",
        description: job.description,
        url: job.url || null,
      },
      resume_text: cfg.resumeText,
      criteria: cfg.criteria,
    };

    let data;
    for (let attempt = 0; ; attempt++) {
      try {
        data = await api(cfg, "/api/tailor", {
          method: "POST",
          body,
          headers: { "Idempotency-Key": run.idempotencyKey },
        });
        break;
      } catch (e) {
        // The idempotency key makes a retry safe: the backend will not start a second generation.
        if (!e.transient || attempt >= 2) throw e;
        await sleep(1500);
      }
    }

    if (data.status === "running") {
      run = await setRun({ ...run, phase: "generating", requestId: data.request_id });
      data = await pollUntilDone(run, cfg);
    }
    await finish(run, data);
  } catch (e) {
    await setRun({ ...run, status: "error", error: e.message, finishedAt: Date.now() });
  } finally {
    runActive = false;
    stopKeepAlive();
  }
}

async function pollUntilDone(run, cfg) {
  let failures = 0;
  while (Date.now() - run.startedAt < MAX_WAIT_MS) {
    await sleep(POLL_INTERVAL_MS);
    try {
      const data = await api(cfg, `/api/requests/${encodeURIComponent(run.requestId)}`);
      failures = 0;
      if (data.status !== "running") return data;
    } catch (e) {
      if (e.status === 404) throw new Error("The backend no longer has this request (it may have restarted).");
      if (!e.transient || ++failures >= MAX_POLL_FAILURES) throw e;
    }
  }
  throw new Error("Timed out waiting for the resume. Try again.");
}

// If the service worker was restarted mid-run, pick polling back up (triggered by the popup).
async function resumeIfNeeded() {
  const run = await getRun();
  if (!run || run.status !== "running" || runActive) return run;
  if (!run.requestId) {
    // The worker stopped before the backend accepted the request.
    return setRun({ ...run, status: "error", error: "The request was interrupted. Try again.", finishedAt: Date.now() });
  }
  const cfg = await getConfig();
  runActive = true;
  const stopKeepAlive = startKeepAlive();
  (async () => {
    try {
      await finish(run, await pollUntilDone(run, cfg));
    } catch (e) {
      await setRun({ ...run, status: "error", error: e.message, finishedAt: Date.now() });
    } finally {
      runActive = false;
      stopKeepAlive();
    }
  })();
  return run;
}

async function finish(run, data) {
  if (data.status === "error") {
    await setRun({ ...run, status: "error", error: data.error || "Generation failed.", finishedAt: Date.now() });
    return;
  }
  const result = data.result;
  const done = await setRun({ ...run, status: "done", result, finishedAt: Date.now() });
  if (result && result.stage === "complete" && result.resume_pdf_base64) {
    await downloadPdf(done);
  }
}

// ---------- downloads ----------

function safeName(title) {
  const cleaned = String(title || "").replace(/[^\w\- ]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 60);
  return cleaned || "tailored";
}

function downloadPdf(run) {
  return chrome.downloads.download({
    url: "data:application/pdf;base64," + run.result.resume_pdf_base64,
    filename: `AutoApplier/Resume - ${safeName(run.job.title)}.pdf`,
    conflictAction: "uniquify",
  });
}

async function downloadFromLastRun(kind) {
  const run = await getRun();
  if (!run || !run.result) throw new Error("Nothing to download yet.");
  if (kind === "tex") {
    if (!run.result.latex_source) throw new Error("No LaTeX source available.");
    return chrome.downloads.download({
      url: "data:application/x-tex;charset=utf-8," + encodeURIComponent(run.result.latex_source),
      filename: `AutoApplier/Resume - ${safeName(run.job.title)}.tex`,
      conflictAction: "uniquify",
    });
  }
  if (!run.result.resume_pdf_base64) throw new Error("No PDF available for this run.");
  return downloadPdf(run);
}
