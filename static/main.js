const headings = {
  overview: ["Desk", "Overview"],
  records: ["This run", "Records"],
  crm: ["Downstream", "CRM inbox"],
  failures: ["Failure paths", "Checks"],
};

const state = {
  view: "overview",
  selected: null,
  data: null,
};

let lastSig = "";

const viewEl = document.getElementById("view");
const noteEl = document.getElementById("note");
const pillEl = document.getElementById("pill");
const runBtn = document.getElementById("run-btn");
const progressEl = document.getElementById("progress");

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function chip(label) {
  const key = String(label || "").toLowerCase();
  return `<span class="chip ${esc(key)}">${esc(label || "—")}</span>`;
}

async function load() {
  const resp = await fetch("/api/dashboard");
  state.data = await resp.json();
  const status = state.data.status || {};
  const sig = JSON.stringify({
    note: status.note,
    phase: status.phase,
    done: status.done,
    records: (state.data.records || []).length,
    failures: (state.data.failures || []).length,
    crm: (state.data.crm || []).length,
  });
  paintStatus();
  if (sig !== lastSig) {
    lastSig = sig;
    paint();
  }
}

function paintStatus() {
  const status = state.data.status || {};
  const running = Boolean(status.running);
  runBtn.disabled = running;
  runBtn.textContent = running ? "Running" : "Run pipeline";
  pillEl.className = "pill";
  if (running) {
    pillEl.classList.add("busy");
    pillEl.textContent = status.total ? `${status.done || 0} of ${status.total}` : (status.phase || "Running");
  } else if (status.phase === "error") {
    pillEl.classList.add("bad");
    pillEl.textContent = "Stopped";
  } else if (status.phase === "done") {
    pillEl.classList.add("good");
    pillEl.textContent = "Finished";
  } else {
    pillEl.textContent = "Idle";
  }
  noteEl.textContent = status.note || "";
  if (running && status.total) {
    progressEl.classList.remove("hidden");
    const pct = Math.round((100 * (status.done || 0)) / status.total);
    progressEl.firstElementChild.style.width = pct + "%";
  } else {
    progressEl.classList.add("hidden");
  }
}

function paint() {
  if (state.view === "overview") paintOverview();
  else if (state.view === "records") paintRecords();
  else if (state.view === "crm") paintCrm();
  else paintFailures();
}

function paintOverview() {
  const data = state.data;
  const run = data.run;
  const records = data.records || [];
  const delivered = records.filter((row) => row.status === "delivered").length;
  const escalated = records.filter((row) => row.result && row.result.action && row.result.action.escalate).length;
  const leads = records.filter((row) => row.source === "linkedin").length;
  const patients = records.filter((row) => row.source === "fhir").length;
  const counts = {};
  records.forEach((row) => {
    const label = row.label || "unknown";
    counts[label] = (counts[label] || 0) + 1;
  });
  const max = Math.max(1, ...Object.values(counts));
  const bars = Object.keys(counts).map((label) => {
    const width = Math.round((100 * counts[label]) / max);
    return `<div class="bar-row"><span>${esc(label)}</span><div class="track"><i style="width:${width}%"></i></div><em>${counts[label]}</em></div>`;
  }).join("");
  const summary = (run && run.summary) || {};
  const logLines = (data.status.log || []).slice(-8).map((line) => `<li>${esc(line)}</li>`).join("");
  viewEl.innerHTML = `
    <section class="stats">
      <div class="stat"><b>${leads}</b><span>leads in this run</span></div>
      <div class="stat"><b>${patients}</b><span>sandbox patients</span></div>
      <div class="stat"><b>${delivered}</b><span>accepted by the CRM</span></div>
      <div class="stat"><b>${escalated}</b><span>flagged for a person</span></div>
    </section>
    <div class="split">
      <article class="panel">
        <h2>Mix</h2>
        <div class="bars">${bars || '<p class="quiet">Run the pipeline and this fills in from real records.</p>'}</div>
        <h3>Run</h3>
        <p class="quiet">${run ? esc(run.id) : "Nothing has been processed yet."}</p>
        <p class="quiet">${summary.provider ? "Model: " + esc(summary.provider) : ""}</p>
      </article>
      <article class="panel">
        <h2>What just happened</h2>
        <ul class="log">${logLines || "<li>Waiting for a run.</li>"}</ul>
      </article>
    </div>
  `;
}

function paintRecords() {
  const records = state.data.records || [];
  if (!records.length) {
    viewEl.innerHTML = `<p class="empty">No records yet. Start a run and they will show up here as the agent finishes each one.</p>`;
    return;
  }
  if (!state.selected || !records.some((row) => String(row.id) === String(state.selected))) {
    state.selected = records[0].id;
  }
  const current = records.find((row) => String(row.id) === String(state.selected));
  viewEl.innerHTML = `
    <div class="split">
      <div class="list">${records.map(renderRow).join("")}</div>
      <article class="panel">${renderDetail(current)}</article>
    </div>
  `;
  viewEl.querySelectorAll(".row").forEach((button) => {
    button.addEventListener("click", () => {
      state.selected = button.dataset.id;
      paintRecords();
    });
  });
}

function renderRow(row) {
  const selected = String(row.id) === String(state.selected) ? "selected" : "";
  const action = (row.result && row.result.action) || {};
  return `
    <button type="button" class="row ${selected}" data-id="${esc(row.id)}">
      <div class="row-top">
        <span class="name">${esc(row.name)}</span>
        ${chip(row.label)}
      </div>
      <div class="meta">
        <span>${chip(row.source === "linkedin" ? "lead" : "patient")} ${esc(action.queue || row.status || "")}</span>
        <span>${esc(row.score ?? "")}</span>
      </div>
    </button>
  `;
}

function renderDetail(row) {
  if (!row) return "";
  const result = row.result || {};
  const action = result.action || {};
  const message = result.message || {};
  const qual = result.qualification || {};
  const trace = result.trace || [];
  const crm = result.crm || {};
  const crmLine = crm.accepted
    ? `CRM accepted this as #${crm.crm_id}`
    : (crm.error || row.status || "not delivered");
  return `
    <p class="quiet">${esc(row.record_id)}</p>
    <h2>${esc(row.name)}</h2>
    <div class="chips">
      ${chip(row.source === "linkedin" ? "lead" : "patient")}
      ${chip(qual.label || row.label)}
      <span class="chip">score ${esc(qual.score ?? row.score ?? "")}</span>
      <span class="chip">${esc(action.channel || "no channel")}</span>
      <span class="chip">${esc(action.queue || "")}</span>
    </div>
    <h3>Why</h3>
    <p>${esc(result.rationale || "")}</p>
    <h3>${esc(message.subject || "Message")}</h3>
    <div class="message">${esc(message.body || "")}</div>
    <h3>Agent trace</h3>
    <ul class="trace">
      ${trace.map((step) => `<li><b>${esc(step.step)}</b><span>${esc(step.detail)}</span></li>`).join("")}
    </ul>
    <h3>CRM</h3>
    <p class="quiet">${esc(crmLine)}</p>
  `;
}

function paintCrm() {
  const rows = state.data.crm || [];
  if (!rows.length) {
    viewEl.innerHTML = `<p class="empty">The inbox is empty. Accepted payloads land here after the CRM route checks the contract.</p>`;
    return;
  }
  viewEl.innerHTML = rows.map((row) => `
    <article class="card crm-item">
      <div class="row-top">
        <strong>${esc(row.payload.person && row.payload.person.name)}</strong>
        <span class="quiet">#${esc(row.id)} · ${esc(row.received_at)}</span>
      </div>
      <p class="quiet">${esc(row.payload.record_id)} · ${esc(row.payload.action && row.payload.action.queue)} · ${esc(row.payload.message && row.payload.message.subject)}</p>
      <pre>${esc(JSON.stringify(row.payload, null, 2))}</pre>
    </article>
  `).join("");
}

function paintFailures() {
  const rows = state.data.failures || [];
  if (!rows.length) {
    viewEl.innerHTML = `<p class="empty">No failures recorded for the latest run.</p>`;
    return;
  }
  viewEl.innerHTML = rows.map((row) => {
    const expected = row.stage === "crm_contract";
    return `
      <article class="card fail-item ${expected ? "expected" : ""}">
        <div class="row-top">
          <strong>${esc(expected ? "Expected contract check" : row.stage)}</strong>
          <span class="quiet">${esc(row.ref)}</span>
        </div>
        <p>${esc(row.message)}</p>
      </article>
    `;
  }).join("");
}

document.querySelectorAll("nav button").forEach((button) => {
  button.addEventListener("click", () => {
    state.view = button.dataset.view;
    document.querySelectorAll("nav button").forEach((item) => item.classList.toggle("active", item === button));
    const [eyebrow, heading] = headings[state.view];
    document.getElementById("eyebrow").textContent = eyebrow;
    document.getElementById("heading").textContent = heading;
    paint();
  });
});

runBtn.addEventListener("click", async () => {
  runBtn.disabled = true;
  const resp = await fetch("/api/pipeline/run", { method: "POST" });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    noteEl.textContent = body.error || "Could not start";
    runBtn.disabled = false;
    return;
  }
  state.view = "overview";
  document.querySelectorAll("nav button").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === "overview");
  });
  document.getElementById("eyebrow").textContent = headings.overview[0];
  document.getElementById("heading").textContent = headings.overview[1];
  await load();
});

load();
setInterval(load, 1500);
