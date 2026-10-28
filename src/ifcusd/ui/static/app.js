// IfcUSD Studio: convert IFC <-> OpenUSD and browse the data, served by `ifcusd ui`.
"use strict";

const TOKEN = document.querySelector('meta[name="ifcusd-token"]').content;
const $ = (selector, root = document) => root.querySelector(selector);

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

async function api(path, { method = "GET", body } = {}) {
  const init = { method, headers: { "X-IfcUSD-Token": TOKEN } };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers["Content-Type"] = "application/json";
  }
  let response;
  try {
    response = await fetch("/api/" + path, init);
  } catch {
    throw new Error("IfcUSD Studio is not reachable. Start it again with `ifcusd ui` or `python app.py`.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.setRequestHeader("X-IfcUSD-Token", TOKEN);
    xhr.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
    xhr.upload.onprogress = (event) => event.lengthComputable && onProgress(event.loaded / event.total);
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status < 300) resolve(data);
      else reject(new Error(data.error || xhr.statusText));
    };
    xhr.onerror = () => reject(new Error("The upload failed. Is IfcUSD Studio still running?"));
    xhr.send(file);
  });
}

const downloadUrl = (id) => `/api/files/${id}/download?token=${encodeURIComponent(TOKEN)}`;
const fmtSize = (bytes) => {
  if (bytes == null) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes, unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return `${value < 10 && unit ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
};
const fmtCount = (n) => Number(n).toLocaleString();
const humanize = (key) => key.replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase().replace(/^./, (c) => c.toUpperCase())
  .replace(/\busd\b/gi, "USD").replace(/\bifc\b/gi, "IFC");

let toastTimer;
function toast(message, bad = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("bad", bad);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 7000 : 3000);
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied"); } catch { toast("Copy is not available here", true); }
}

// ---------------------------------------------------------------- state

const state = {
  info: null,
  files: [],
  direction: "to-usd",
  toUsd: { input: null, profile: "generic", format: "usda", options: {}, validate: true },
  toIfc: { input: null, source: null, layers: [], schema: "IFC4", options: {}, validate: false },
  job: null,
  inspect: { file: null, selected: null },
};
const listeners = new Set();
const fileById = (id) => state.files.find((f) => f.id === id);

async function refreshFiles() {
  const info = await api("state");
  state.info = info;
  state.files = info.files;
  listeners.forEach((fn) => fn());
}

// ---------------------------------------------------------------- tabs

const views = ["convert", "inspect", "schema"];
function show(view, push = true) {
  if (!views.includes(view)) view = "convert";
  for (const name of views) {
    const tab = $(`#tab-${name}`);
    tab.setAttribute("aria-selected", String(name === view));
    tab.tabIndex = name === view ? 0 : -1;
    $(`#${name}`).hidden = name !== view;
  }
  if (push && location.hash !== `#${view}`) history.pushState(null, "", `#${view}`);
  if (view === "convert") renderSides();
  if (view === "inspect") renderInspectFiles();
  if (view === "schema") loadSchema();
}
document.querySelectorAll(".tabs button").forEach((tab, index, tabs) => {
  tab.addEventListener("click", () => show(tab.dataset.view));
  tab.addEventListener("keydown", (event) => {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
    if (!step) return;
    const next = tabs[(index + step + tabs.length) % tabs.length];
    next.focus();
    show(next.dataset.view);
  });
});
window.addEventListener("popstate", () => show(location.hash.slice(1), false));

// ---------------------------------------------------------------- file picker

const ACCEPT = { ifc: ".ifc,.ifczip,.ifcx", usd: ".usd,.usda,.usdc,.usdz" };
const KIND_LABEL = { ifc: "IFC", usd: "OpenUSD" };

// A drop zone with a file input, files already here, samples and a local path.
function filePicker({ kind, value, onChange, prompt, optional }) {
  const zone = h("div", { class: "drop" });
  const input = h("input", { type: "file", accept: ACCEPT[kind], hidden: true });
  const status = h("p", { class: "prompt" });

  async function take(file) {
    if (!file) return;
    status.textContent = `Uploading ${file.name}…`;
    try {
      const entry = await uploadFile(file, (fraction) => { status.textContent = `Uploading ${file.name}: ${Math.round(fraction * 100)}%`; });
      await refreshFiles();
      onChange(entry.id);
    } catch (error) {
      status.textContent = error.message;
      toast(error.message, true);
    }
  }
  input.addEventListener("change", () => take(input.files[0]));
  zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("over");
    take(event.dataTransfer.files[0]);
  });

  const chosen = value && fileById(value);
  if (chosen) {
    zone.append(h("div", { class: "chosen" },
      h("strong", { text: chosen.name }),
      h("span", { class: "meta", text: [fmtSize(chosen.size), originLabel(chosen.origin)].filter(Boolean).join(", ") }),
      h("button", { type: "button", class: "link", onclick: () => onChange(null) }, optional ? "Remove" : "Change")));
  } else {
    status.textContent = prompt;
    zone.append(status);
    const others = state.files.filter((f) => f.kind === kind && f.origin !== "sample");
    const row = h("div", { class: "pick-row" },
      h("button", { type: "button", class: "quiet", onclick: () => input.click() }, `Choose ${KIND_LABEL[kind]} file…`));
    if (others.length) {
      const select = h("select", { "aria-label": `${KIND_LABEL[kind]} files already here` },
        h("option", { value: "" }, "Or use a file already here"),
        others.map((f) => h("option", { value: f.id }, `${f.name} (${originLabel(f.origin)})`)));
      select.addEventListener("change", () => select.value && onChange(select.value));
      row.append(select);
    }
    zone.append(row);
    const samples = state.files.filter((f) => f.kind === kind && f.origin === "sample");
    if (samples.length) {
      zone.append(h("div", { class: "samples" }, "Samples:",
        samples.map((f) => h("button", { type: "button", class: "chip-button", onclick: () => onChange(f.id) }, f.name))));
    }
    const pathInput = h("input", { type: "text", placeholder: "Or paste a path on this computer", "aria-label": "File path on this computer" });
    const open = async () => {
      if (!pathInput.value.trim()) return;
      try {
        const entry = await api("open-path", { method: "POST", body: { path: pathInput.value } });
        await refreshFiles();
        onChange(entry.id);
      } catch (error) { toast(error.message, true); }
    };
    pathInput.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); open(); } });
    if (state.info.allowPaths) {
      zone.append(h("div", { class: "path-row" }, pathInput, h("button", { type: "button", class: "quiet", onclick: open }, "Open")));
    }
  }
  zone.append(input);
  return zone;
}

const originLabel = (origin) => ({ upload: "uploaded", sample: "sample", local: "on this computer", output: "converted" })[origin] || origin;

// ---------------------------------------------------------------- convert

const USD_GROUPS = [
  { title: "Stage", fields: [
    { name: "up_axis", label: "Up axis", choices: [["Z", "Z, as in IFC and Isaac Sim"], ["Y", "Y, as in Unity"]] },
    { name: "meters_per_unit", label: "Stage unit", choices: [[1, "Metre"], [0.01, "Centimetre"], [0.001, "Millimetre"]] },
    { name: "naming", label: "Prim names", choices: [["name_guid", "Name and GlobalId"], ["guid", "GlobalId"], ["name", "Name"]] },
  ] },
  { title: "Geometry", fields: [
    { name: "instancing", label: "Share repeated geometry", note: "USD instancing for type geometry" },
    { name: "include_curves", label: "Axis and footprint curves" },
    { name: "include_openings", label: "Opening volumes" },
    { name: "spaces", label: "Spaces", choices: [["hidden", "Hidden"], ["visible", "Visible"], ["skip", "Left out"]] },
    { name: "use_world_coords", label: "Bake placements into points" },
  ] },
  { title: "Data", fields: [
    { name: "include_properties", label: "Property sets" },
    { name: "include_quantities", label: "Quantity sets" },
    { name: "include_materials", label: "IFC materials" },
    { name: "include_relationships", label: "Relationships" },
    { name: "inherit_type_properties", label: "Copy type properties onto occurrences" },
    { name: "semantic_labels", label: "Semantic class labels", note: "UsdSemantics, for synthetic data" },
    { name: "legacy_semantics", label: "Legacy Omniverse semantics" },
  ] },
  { title: "Simulation and look", fields: [
    { name: "physics", label: "Apply IfcUSD_Physics as UsdPhysics", note: "Mass, rigid bodies, colliders, friction", tone: "physics" },
    { name: "material_model", label: "Materials", choices: [["preview", "UsdPreviewSurface"], ["preview+mdl", "UsdPreviewSurface and OmniPBR"]] },
  ] },
];
const IFC_GROUPS = [
  { title: "Changes to the source model", needsSource: true, fields: [
    { name: "update_attributes", label: "Changed attributes", note: "Name, description, class" },
    { name: "update_properties", label: "Changed property values" },
    { name: "update_placements", label: "Moved objects" },
  ] },
  { title: "New data from USD", fields: [
    { name: "include_new_objects", label: "New objects", note: "Robots, sensors, props added in USD" },
    { name: "include_geometry", label: "Geometry of new objects" },
    { name: "include_physics", label: "Physics as IfcUSD_Physics", tone: "physics" },
    { name: "include_semantics", label: "Semantic labels as classifications" },
    { name: "include_time_series", label: "Time samples as IFC time series" },
    { name: "include_trajectories", label: "Animated motion as trajectories" },
    { name: "property_templates", label: "Templates for new property sets" },
  ] },
];
const PROFILE_LABELS = { generic: "Any OpenUSD tool", omniverse: "Omniverse and Isaac Sim", unity: "Unity" };
const FORMAT_LABELS = { usda: "USDA text", usdc: "USDC binary", usdz: "USDZ package" };

function segmented(name, choices, current, onChange) {
  return h("div", { class: "segmented", role: "radiogroup" },
    choices.map(([value, label]) => {
      const input = h("input", { type: "radio", name, value: String(value), checked: String(value) === String(current) });
      input.addEventListener("change", () => onChange(value));
      return h("label", {}, input, h("span", { text: label }));
    }));
}

function currentOptions() {
  if (state.direction === "to-usd") {
    return { ...state.info.defaults[state.toUsd.profile], ...state.toUsd.options };
  }
  return { ...state.info.exportDefaults, ...state.toIfc.options };
}

function renderOptions() {
  const form = $("#options");
  form.replaceChildren();
  const toUsd = state.direction === "to-usd";
  const values = currentOptions();
  const target = toUsd ? state.toUsd : state.toIfc;
  for (const group of toUsd ? USD_GROUPS : IFC_GROUPS) {
    const off = group.needsSource && !state.toIfc.source;
    const set = h("fieldset", { class: off ? "disabled" : null, disabled: off }, h("legend", { text: group.title }));
    if (off) set.append(h("p", { class: "choice-note", text: "Used when you update an existing IFC model." }));
    for (const field of group.fields) {
      if (field.choices) {
        const select = h("select", { id: `opt-${field.name}` },
          field.choices.map(([value, label]) => h("option", { value: String(value), selected: String(value) === String(values[field.name]) }, label)));
        select.addEventListener("change", () => {
          const choice = field.choices.find(([value]) => String(value) === select.value);
          target.options[field.name] = choice ? choice[0] : select.value;
        });
        set.append(h("label", { class: "field", for: `opt-${field.name}` }, h("span", { text: field.label }), select));
      } else {
        const box = h("input", { type: "checkbox", checked: Boolean(values[field.name]) });
        box.addEventListener("change", () => { target.options[field.name] = box.checked; });
        set.append(h("label", { class: `check ${field.tone || ""}` }, box,
          h("span", {}, field.label, field.note ? h("small", { text: field.note }) : null)));
      }
    }
    form.append(set);
  }
  const check = h("input", { type: "checkbox", checked: target.validate });
  check.addEventListener("change", () => { target.validate = check.checked; });
  $("#validate").replaceChildren(check, h("span", {}, toUsd ? "Validate the result" : "Validate the IFC",
    h("small", { text: toUsd ? "Structure and round-trip checks against the source" : "IfcOpenShell schema validation" })));
}

function renderSides() {
  const toUsd = state.direction === "to-usd";
  const ends = $(".ends");
  ends.dataset.direction = state.direction;
  $("#end-from").textContent = toUsd ? "IFC" : "OpenUSD";
  $("#end-to").textContent = toUsd ? "OpenUSD" : "IFC";
  $("#swap").setAttribute("aria-label", toUsd ? "Swap direction: convert OpenUSD to IFC" : "Swap direction: convert IFC to OpenUSD");
  $("#lede").textContent = toUsd
    ? "Turn an IFC building model into an OpenUSD stage for Omniverse, Isaac Sim, Unity or any OpenUSD tool. Every attribute, property and physics setting is kept."
    : "Write what changed in USD back into IFC: edited values, moved objects, robots and sensors you added, and physics from the simulation.";
  document.documentElement.style.setProperty("--from-tone", toUsd ? "var(--ifc)" : "var(--usd)");
  document.documentElement.style.setProperty("--to-tone", toUsd ? "var(--usd)" : "var(--ifc)");

  const from = $("#side-from");
  const to = $("#side-to");
  from.replaceChildren();
  to.replaceChildren();
  if (toUsd) {
    const s = state.toUsd;
    from.append(h("h2", { text: "IFC model to convert" }),
      filePicker({ kind: "ifc", value: s.input, prompt: "Drop an .ifc, .ifczip or .ifcx file here.", onChange: (id) => { s.input = id; renderConvert(); } }));
    to.append(h("h2", { text: "OpenUSD stage to write" }),
      h("div", { class: "field" }, h("span", { text: "Target" }),
        segmented("profile", state.info.profiles.map((p) => [p, PROFILE_LABELS[p] || p]), s.profile, (p) => { s.profile = p; s.options = {}; renderOptions(); })),
      h("div", { class: "field" }, h("span", { text: "File format" }),
        segmented("format", Object.entries(FORMAT_LABELS), s.format, (f) => { s.format = f; })),
      h("p", { class: "choice-note", text: "The target sets the defaults below: Omniverse adds OmniPBR materials and legacy semantics, Unity uses Y-up and leaves physics out." }));
  } else {
    const s = state.toIfc;
    from.append(h("h2", { text: "OpenUSD stage to read" }),
      filePicker({ kind: "usd", value: s.input, prompt: "Drop a .usd, .usda, .usdc or .usdz file here.", onChange: (id) => { s.input = id; renderConvert(); } }));
    const layers = h("div", { class: "field" }, h("span", { text: "Edit layers on top (optional)" }));
    if (s.layers.length) {
      layers.append(h("ol", { class: "layer-list" }, s.layers.map((id, index) => h("li", {},
        h("span", {}, fileById(id)?.name || id, " ", h("span", { class: "order", text: index === s.layers.length - 1 ? "(strongest)" : "" })),
        h("button", { type: "button", class: "link", onclick: () => { s.layers.splice(index, 1); renderConvert(); } }, "Remove")))));
    }
    layers.append(filePicker({ kind: "usd", value: null, prompt: "Add a layer with your edits, such as one saved from Isaac Sim.", onChange: (id) => { if (id && !s.layers.includes(id)) s.layers.push(id); renderConvert(); } }));
    from.append(layers);

    to.append(h("h2", { text: "IFC model to write" }),
      h("div", { class: "field" }, h("span", { text: "Update an existing IFC model (recommended)" }),
        filePicker({ kind: "ifc", value: s.source, optional: true, prompt: "The IFC the stage was converted from. Only what changed is written.", onChange: (id) => { s.source = id; renderConvert(); } })));
    if (!s.source) {
      to.append(h("div", { class: "field" }, h("span", { text: "Or build a new model in" }),
        segmented("schema", state.info.ifcSchemas.map((x) => [x, x]), s.schema, (x) => { s.schema = x; })));
    }
  }
}

function renderRun() {
  const toUsd = state.direction === "to-usd";
  const input = toUsd ? state.toUsd.input : state.toIfc.input;
  const busy = state.job && ["queued", "running"].includes(state.job.status);
  const run = $("#run");
  run.textContent = toUsd ? "Convert to OpenUSD" : "Convert to IFC";
  run.disabled = !input || busy;
  const hint = $("#run-hint");
  if (busy) hint.textContent = "Converting. You can keep browsing; the result appears below.";
  else if (!input) hint.textContent = `Choose ${toUsd ? "an IFC model" : "an OpenUSD stage"} to convert.`;
  else if (!toUsd) hint.textContent = state.toIfc.source ? "Only what changed in USD is written into the model." : `A new ${state.toIfc.schema} model is built from the stage.`;
  else hint.textContent = `Writes ${fileById(input)?.name.replace(/\.[^.]+$/, "")}.${state.toUsd.format}.`;
}

function renderConvert() {
  renderSides();
  renderOptions();
  renderRun();
}

$("#swap").addEventListener("click", () => {
  const ends = $(".ends");
  ends.classList.add("swapping");
  setTimeout(() => {
    state.direction = state.direction === "to-usd" ? "to-ifc" : "to-usd";
    renderConvert();
    ends.classList.remove("swapping");
  }, matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 200);
});

$("#run").addEventListener("click", async () => {
  const toUsd = state.direction === "to-usd";
  const request = toUsd
    ? { direction: "to-usd", input: state.toUsd.input, profile: state.toUsd.profile, format: state.toUsd.format,
        options: state.toUsd.options, validate: state.toUsd.validate }
    : { direction: "to-ifc", input: state.toIfc.input, source: state.toIfc.source, layers: state.toIfc.layers,
        options: { ...state.toIfc.options, schema: state.toIfc.source ? null : state.toIfc.schema }, validate: state.toIfc.validate };
  try {
    state.job = await api("convert", { method: "POST", body: request });
    renderRun();
    renderJob();
    pollJob();
  } catch (error) {
    toast(error.message, true);
  }
});

let pollTimer;
async function pollJob() {
  clearTimeout(pollTimer);
  if (!state.job) return;
  try {
    state.job = await api(`jobs/${state.job.id}`);
  } catch (error) {
    toast(error.message, true);
    return;
  }
  if (["queued", "running"].includes(state.job.status)) {
    renderJob();
    pollTimer = setTimeout(pollJob, 400);
  } else {
    await refreshFiles();  // the outputs must be known before the result offers to inspect them
    renderJob();
    renderRun();
    if (state.job.status === "done") toast(state.job.direction === "to-usd" ? "Converted to OpenUSD" : "Converted to IFC");
  }
}

const STAGE_LABELS = {
  queued: "Waiting", starting: "Starting", read: "Reading the IFC model", semantics: "Writing IFC data",
  geometry: "Tessellating geometry", write: "Saving the stage", converting: "Converting", validating: "Validating", done: "Done",
};

function renderJob() {
  const job = state.job;
  const box = $("#result");
  box.hidden = !job;
  if (!job) return;
  box.classList.toggle("failed", job.status === "failed");
  box.replaceChildren();
  const running = ["queued", "running"].includes(job.status);
  if (running) {
    const known = typeof job.fraction === "number" && !["queued", "starting"].includes(job.stage);
    const percent = known ? Math.round(job.fraction * 100) : null;
    const label = `${STAGE_LABELS[job.stage] || humanize(job.stage)}${percent != null ? ` ${percent}%` : ""}`;
    box.append(h("div", { class: `dimension${known ? "" : " indeterminate"}`, role: "progressbar", "aria-label": label,
      "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": percent ?? undefined },
      h("span", { class: "label", text: label }), h("div", { class: "line" }), h("div", { class: "fill", style: `width:${percent ?? 0}%` })));
    return;
  }
  if (job.status === "failed") {
    box.append(h("h2", { text: "The conversion stopped" }), h("p", { class: "error", text: job.error }));
    if (job.log.length) box.append(h("details", {}, h("summary", { text: "Details" }), h("pre", { class: "lines", text: job.log.join("\n") })));
    return;
  }
  const seconds = job.finished && job.started ? ` in ${(job.finished - job.started).toFixed(1)} s` : "";
  box.append(h("h2", { text: `${job.direction === "to-usd" ? "Converted to OpenUSD" : "Converted to IFC"}${seconds}` }));
  if (job.summary.length) box.append(h("ul", { class: "summary" }, job.summary.map((line) => h("li", { text: line }))));

  for (const output of job.outputs) {
    box.append(h("div", { class: "outputs" },
      h("a", { class: "download", href: downloadUrl(output.id), download: output.name }, `Download ${output.name}`, h("span", { text: fmtSize(output.size) })),
      h("button", { type: "button", class: "quiet", onclick: () => { state.inspect.file = output.id; state.inspect.selected = null; show("inspect"); } }, "Inspect the result"),
      h("button", { type: "button", class: "quiet", onclick: () => downloadJson(job.report, output.name.replace(/\.[^.]+$/, ".report.json")) }, "Download the report")),
      h("p", { class: "location" }, "Saved at", h("code", { text: output.location }),
        h("button", { type: "button", class: "link", onclick: () => copy(output.location) }, "Copy path")));
  }
  const counts = Object.entries(job.report?.counts || {}).filter(([, n]) => typeof n === "number" && n > 0);
  if (counts.length) {
    box.append(h("dl", { class: "counts" }, counts.map(([key, n]) =>
      h("div", { class: /physics/i.test(key) ? "physics" : null }, h("dt", { text: humanize(key) }), h("dd", { text: fmtCount(n) })))));
  }
  if (job.validation) {
    box.append(h("details", { open: !job.validation.ok },
      h("summary", { class: job.validation.ok ? "status-ok" : "status-bad", text: job.validation.ok ? "Validation passed" : "Validation found problems" }),
      job.validation.summary.length ? h("pre", { class: "lines", text: job.validation.summary.join("\n") }) : null));
  }
  const warnings = job.report?.warnings || [];
  if (warnings.length) {
    box.append(h("details", {}, h("summary", { text: `${warnings.length} ${warnings.length === 1 ? "warning" : "warnings"}` }),
      h("pre", { class: "lines", text: warnings.join("\n") })));
  }
}

function downloadJson(data, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const link = h("a", { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---------------------------------------------------------------- inspect

function renderInspectFiles() {
  const select = $("#inspect-file");
  const files = state.files;
  if (!state.inspect.file || !fileById(state.inspect.file)) {  // open on the latest converted file
    state.inspect.file = [...files].reverse().find((f) => f.origin === "output")?.id || null;
  }
  select.replaceChildren(
    h("option", { value: "" }, files.length ? "Choose a file" : "No files yet: convert or open one first"),
    ...files.map((f) => h("option", { value: f.id }, `${f.name} (${KIND_LABEL[f.kind]}, ${originLabel(f.origin)})`)));
  select.value = state.inspect.file || "";
  $("#inspect-remove").hidden = !state.inspect.file;
  if (state.inspect.file && fileById(state.inspect.file) && state.inspect.loaded !== state.inspect.file) loadInspect();
  if (!state.inspect.file) {
    $("#facts").replaceChildren();
    $("#tree").replaceChildren();
    $("#detail").replaceChildren(h("p", { class: "empty", text: "Pick a file to browse its structure, IFC attributes, property sets, physics and USD schemas. Converted files appear here too." }));
  }
}

$("#inspect-file").addEventListener("change", (event) => {
  state.inspect.file = event.target.value || null;
  state.inspect.selected = null;
  renderInspectFiles();
});
$("#inspect-remove").addEventListener("click", async () => {
  const entry = fileById(state.inspect.file);
  if (!entry) return;
  const deletes = ["upload", "output"].includes(entry.origin);
  if (deletes && !confirm(`Delete ${entry.name} from the workspace folder?`)) return;
  try {
    await api(`files/${entry.id}`, { method: "DELETE" });
    state.inspect.file = null;
    state.inspect.loaded = null;
    for (const s of [state.toUsd, state.toIfc]) {
      if (s.input === entry.id) s.input = null;
      if (s.source === entry.id) s.source = null;
      if (s.layers) s.layers = s.layers.filter((id) => id !== entry.id);
    }
    await refreshFiles();
    renderInspectFiles();
    toast(deletes ? `Deleted ${entry.name}` : `Removed ${entry.name} from the list`);
  } catch (error) { toast(error.message, true); }
});

async function loadInspect() {
  const id = state.inspect.file;
  state.inspect.loaded = id;
  $("#facts").replaceChildren(h("div", {}, h("dt", { text: "Opening" }), h("dd", { text: fileById(id)?.name })));
  $("#tree").replaceChildren();
  $("#detail").replaceChildren();
  try {
    const summary = await api(`files/${id}/summary`);
    if (state.inspect.file !== id) return;
    state.inspect.summary = summary;
    $("#facts").replaceChildren(...summary.facts.map((f) => h("div", {}, h("dt", { text: f.label }), h("dd", { text: f.value }))));
    const roots = await api(`files/${id}/children?path=${encodeURIComponent(summary.root)}`);
    const tree = $("#tree");
    tree.replaceChildren(...roots.nodes.map((node) => treeItem(node, 1)));
    // Open the first branch down to the storeys, so the model's elements are visible straight away.
    let item = tree.firstElementChild;
    for (let depth = 0; item && depth < 5; depth++) {
      if (item.getAttribute("aria-expanded") === "false") await toggle(item);
      item = item.querySelector(":scope > ul > li");
    }
    if (state.inspect.selected) selectNode(state.inspect.selected);
    else renderContents(summary);
  } catch (error) {
    $("#detail").replaceChildren(h("p", { class: "error", text: error.message }));
  }
}

const TWIST = '<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M3 1l5 4-5 4z"/></svg>';

function treeItem(node, level) {
  const twist = h("button", { type: "button", class: `twist${node.hasChildren ? "" : " leaf"}`, tabindex: -1, "aria-hidden": true });
  twist.innerHTML = TWIST;
  const item = h("li", { role: "treeitem", "aria-level": level, "aria-selected": "false", tabindex: -1, "data-id": node.id,
    "aria-expanded": node.hasChildren ? "false" : null },
    h("div", { class: "row", title: node.id },
      twist,
      h("span", { class: "node-name", text: node.name }),
      node.tags.includes("physics") ? h("span", { class: "dot", title: "Has physics" }) : null,
      h("span", { class: "cls", text: node.ifcClass || node.type || (node.count ? fmtCount(node.count) : "") })));
  twist.addEventListener("click", (event) => { event.stopPropagation(); toggle(item); });
  item.querySelector(".row").addEventListener("click", () => selectNode(node.id, item));
  item.addEventListener("keydown", (event) => treeKey(event, item));
  return item;
}

async function toggle(item, offset = 0) {
  if (!item.hasAttribute("aria-expanded")) return;
  const open = item.getAttribute("aria-expanded") === "true";
  if (open && !offset) {
    item.setAttribute("aria-expanded", "false");
    item.querySelector(":scope > ul")?.remove();
    return;
  }
  const id = state.inspect.file;
  const level = Number(item.getAttribute("aria-level")) + 1;
  const data = await api(`files/${id}/children?path=${encodeURIComponent(item.dataset.id)}&offset=${offset}`);
  let list = item.querySelector(":scope > ul");
  if (!list) {
    list = h("ul", { role: "group" });
    item.append(list);
  }
  list.querySelector(":scope > .more")?.remove();
  list.append(...data.nodes.map((node) => treeItem(node, level)));
  const shown = data.offset + data.nodes.length;
  if (shown < data.total) {
    list.append(h("li", { class: "more" }, h("button", { type: "button", class: "link", onclick: () => toggle(item, shown) },
      `Show ${fmtCount(Math.min(500, data.total - shown))} more of ${fmtCount(data.total)}`)));
  }
  item.setAttribute("aria-expanded", "true");
}

function treeKey(event, item) {
  const items = [...$("#tree").querySelectorAll('[role="treeitem"]')].filter((el) => el.offsetParent !== null);
  const index = items.indexOf(item);
  const focus = (el) => { if (el) { el.tabIndex = 0; el.focus(); } };
  switch (event.key) {
    case "ArrowDown": focus(items[index + 1]); break;
    case "ArrowUp": focus(items[index - 1]); break;
    case "ArrowRight":
      if (item.getAttribute("aria-expanded") === "false") toggle(item);
      else focus(item.querySelector('[role="treeitem"]'));
      break;
    case "ArrowLeft":
      if (item.getAttribute("aria-expanded") === "true") toggle(item);
      else focus(item.parentElement.closest('[role="treeitem"]'));
      break;
    case "Enter": case " ": selectNode(item.dataset.id, item); break;
    default: return;
  }
  event.preventDefault();
  event.stopPropagation();
}

async function selectNode(id, item) {
  state.inspect.selected = id;
  document.querySelectorAll('#tree [aria-selected="true"]').forEach((el) => el.setAttribute("aria-selected", "false"));
  const target = item || [...document.querySelectorAll("#tree [data-id]")].find((el) => el.dataset.id === id);
  if (target) { target.setAttribute("aria-selected", "true"); target.tabIndex = 0; }
  const detail = $("#detail");
  try {
    const data = await api(`files/${state.inspect.file}/node?path=${encodeURIComponent(id)}`);
    if (state.inspect.selected !== id) return;
    renderNode(data);
  } catch (error) {
    detail.replaceChildren(h("p", { class: "error", text: error.message }));
  }
}

function renderContents(summary) {
  const blocks = [];
  const bars = (title, rows, tone) => {
    if (!rows?.length) return null;
    const max = Math.max(...rows.map((r) => r.count));
    return h("section", {}, h("h3", { text: title }),
      h("div", { class: `bars ${tone}` }, rows.slice(0, 18).flatMap((r) => [
        h("span", { text: r.name }), h("div", { class: "bar", style: `width:${Math.max(1, (r.count / max) * 100)}%` }), h("span", { class: "n", text: fmtCount(r.count) })])));
  };
  blocks.push(bars("IFC classes", summary.classes, "ifc"));
  blocks.push(bars("Prim types", summary.types, "usd"));
  blocks.push(bars("Physics schemas", summary.physics, "physics"));
  blocks.push(bars("Property sets", summary.propertySets, "ifc"));
  $("#detail").replaceChildren(
    h("div", { class: "detail-head" }, h("h2", { text: summary.name }),
      h("p", { class: "choice-note", text: "What the file contains. Select an item on the left to see its data." })),
    h("div", { class: "contents" }, blocks.filter(Boolean)));
}

function renderNode(data) {
  const detail = $("#detail");
  const usd = state.inspect.summary?.kind === "usd";
  const badges = [];
  const ifcClass = usd ? data.groups.flatMap((g) => g.rows).find((r) => r.name === "ifc:class")?.value : data.type;
  if (ifcClass) badges.push(h("span", { class: "badge ifc", text: ifcClass }));
  if (usd && data.type) badges.push(h("span", { class: "badge usd", text: data.type }));
  if (data.kind) badges.push(h("span", { class: "badge", text: `kind ${data.kind}` }));
  if (data.instance) badges.push(h("span", { class: "badge usd", text: "instance" }));
  for (const schema of data.schemas) badges.push(h("span", { class: `badge ${schema.startsWith("Physics") ? "physics" : schema.startsWith("Ifc") ? "ifc" : "usd"}`, text: schema }));

  const head = h("div", { class: "detail-head" },
    h("h2", { text: data.name }),
    badges.length ? h("div", { class: "badges" }, badges) : null,
    h("p", { class: "path" }, h("code", { text: data.path }), h("button", { type: "button", class: "link", onclick: () => copy(data.path) }, "Copy")));
  const groups = data.groups.filter((g) => g.rows.length).map((group) => h("section", { class: `group ${group.tone}` },
    h("h3", { text: group.title }),
    group.description ? h("p", { text: group.description }) : null,
    h("table", {}, h("thead", {}, h("tr", {}, h("th", { text: "Name" }), h("th", { text: "Value" }), h("th", { text: usd ? "Type" : "" }))),
      h("tbody", {}, group.rows.map((row) => h("tr", {},
        h("td", { class: "name" }, row.label, row.label !== row.name && usd ? h("small", { class: "code", text: row.name }) : null,
          row.note ? h("small", { text: row.note }) : null),
        h("td", { class: "value" }, row.value,
          row.count != null ? h("small", { text: `${fmtCount(row.count)} items` }) : null,
          row.samples ? h("small", { text: `${fmtCount(row.samples)} time samples; first shown` }) : null),
        h("td", { class: "meta" }, [row.type, row.valueType, row.unit].filter(Boolean).join(", "))))))));
  const links = [...data.relationships.map((r) => [r.label || r.name, r.targets]), ...Object.entries(data.materials).map(([k, v]) => [`Bound ${k} material`, [v]])];
  const related = links.length ? h("section", { class: "group" }, h("h3", { text: "Relationships" }),
    h("table", {}, h("tbody", {}, links.map(([name, targets]) => h("tr", {},
      h("td", { class: "name", text: name }),
      h("td", { class: "value" }, targets.map((t) => usd && t.startsWith("/")
        ? h("div", {}, h("button", { type: "button", class: "link", onclick: () => selectNode(t.split(".")[0]) }, t))
        : h("div", { text: t })))))))) : null;
  detail.replaceChildren(head, ...groups, related);
}

let searchTimer;
$("#search").addEventListener("input", (event) => {
  clearTimeout(searchTimer);
  const query = event.target.value.trim();
  searchTimer = setTimeout(async () => {
    const id = state.inspect.file;
    if (!id) return;
    if (!query) { state.inspect.loaded = null; loadInspect(); return; }
    try {
      const data = await api(`files/${id}/search?q=${encodeURIComponent(query)}`);
      const tree = $("#tree");
      tree.replaceChildren(...(data.nodes.length ? data.nodes.map((node) => treeItem({ ...node, hasChildren: false }, 1))
        : [h("li", { class: "empty", text: `Nothing matches “${query}”.` })]));
    } catch (error) { toast(error.message, true); }
  }, 250);
});

// ---------------------------------------------------------------- schema

let schemaLoaded = false;
async function loadSchema() {
  if (schemaLoaded) return;
  const body = $("#schema-body");
  try {
    const data = await api("schema");
    schemaLoaded = true;
    const table = (rows, a, b) => h("table", { class: "map" }, h("thead", {}, h("tr", {}, h("th", { text: a }), h("th", { text: b }))),
      h("tbody", {}, rows.map((r) => h("tr", {}, h("td", { text: r.ifc }), h("td", { text: r.usd })))));
    body.replaceChildren(
      h("section", {}, h("h2", { text: "How IFC is written in OpenUSD" }),
        h("p", { class: "intro", text: `IfcUSD ${data.version} writes mapping version ${data.mappingVersion}, recorded in every file it writes. Nothing in the IFC model is dropped: what has no native USD form is kept as typed, namespaced attributes.` }),
        table(data.mapping, "IFC", "OpenUSD")),
      h("section", {}, h("h2", { text: "Physics in both directions" }),
        h("p", { text: "ifcusd to-ifc stores a simulation's physics in IFC as the IfcUSD_Physics property set; ifcusd convert turns it back into native UsdPhysics, so the next simulation starts where the last one ended. Removing a rigid body or collider in the simulation writes it back as disabled." }),
        table(data.physicsMapping, "IfcUSD_Physics", "UsdPhysics")),
      h("section", {}, h("h2", { text: "IfcUSD schema plugin" }),
        h("p", { text: "Codeless USD API schemas that give IFC data a declared place in USD. Register them with PXR_PLUGINPATH_NAME (see ifcusd schema) for schema-aware tools." }),
        data.schemas.map((schema) => h("div", { class: "schema-api" },
          h("h3", {}, schema.name, " ", h("span", { class: "badge usd", text: schema.apply === "multiple" ? "multiple-apply" : "single-apply" })),
          h("p", { text: schema.doc }),
          schema.properties.length ? h("table", {}, h("thead", {}, h("tr", {}, h("th", { text: "Property" }), h("th", { text: "Type" }), h("th", { text: "Meaning" }))),
            h("tbody", {}, schema.properties.map((p) => h("tr", {}, h("td", { class: "name code", text: p.name }), h("td", { class: "meta", text: p.type }),
              h("td", { text: p.doc + (p.tokens.length ? ` (${p.tokens.join(", ")})` : "") }))))) : null))),
      h("section", {}, h("h2", { text: "IFC extension property sets" }),
        h("p", { text: "Property sets IfcUSD adds to IFC for robot and simulation data. They are ordinary IFC4 property sets with templates, so any IFC tool can read them." }),
        data.extension.map((set) => h("div", { class: "schema-api extension" }, h("h3", { text: set.name }), h("p", { text: set.description }),
          h("table", {}, h("thead", {}, h("tr", {}, h("th", { text: "Property" }), h("th", { text: "Meaning" }))),
            h("tbody", {}, set.properties.map((p) => h("tr", {}, h("td", { class: "name", text: p.name }), h("td", { text: p.doc }))))))))
    );
  } catch (error) {
    body.replaceChildren(h("p", { class: "error", text: error.message }));
  }
}

// ---------------------------------------------------------------- start

listeners.add(() => {
  if (!$("#convert").hidden) renderSides();
  if (!$("#inspect").hidden) renderInspectFiles();
});

(async function start() {
  try {
    await refreshFiles();
  } catch (error) {
    $("#main").replaceChildren(h("p", { class: "error", text: error.message }));
    return;
  }
  $("#version").textContent = `IfcUSD ${state.info.version}, mapping ${state.info.mappingVersion}`;
  renderConvert();
  // Reloading the page keeps the latest conversion in view.
  state.job = state.info.jobs.at(-1) || null;
  if (state.job && ["queued", "running"].includes(state.job.status)) pollJob();
  else renderJob();
  renderRun();
  show(location.hash.slice(1) || "convert", false);
})();
