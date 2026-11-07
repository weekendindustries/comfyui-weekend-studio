// Weekend Studio inside ComfyUI: the sidebar tab and the right-click setting picker.
// Plan: reviews/comfyui-plan-2026-10-14.md ("Where the user turns it on", "The node pack").
//
//   Tab       Connect / Disconnect (and which account); "Show in Weekend Studio" for the open
//             workflow; the settings it shows (rename, group, limits); every published workflow;
//             the two switches (same-computer mode, automatic updates); Network and data.
//   Picker    right-click a node → Weekend Studio → tick a setting to show it in Weekend Studio.
//
// The choices live in the workflow itself (workflow.extra.weekendStudio = { id, name, marks, install }),
// so they travel with the .json; publishing sends the API-format graph + marks to this ComfyUI's own
// /weekend-studio routes (routes.py). Nothing here talks to Weekend Studio directly.
//
// 0.3.0 (security review B1–B3):
//   · every call carries X-Weekend-Studio: 1 — routes.py refuses requests without it (another site
//     can't send it), from a foreign Host, or with a foreign Origin;
//   · `install` ties the choices to THIS ComfyUI. A workflow file set up elsewhere (shared on a forum,
//     downloaded with a picture) is "foreign": its choices stay off and nothing is published until
//     you say "Use here". Nothing is ever published afresh behind your back;
//   · automatic updates are a switch, off until you turn it on, and the tab says when it's on;
//   · settings that look like file paths, web addresses or code can't be ticked.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const BRIDGE = new Set(["WeekendImageIn", "WeekendMaskIn", "WeekendOutput"]);
const SKIP_WIDGETS = new Set(["control_after_generate", "upload", "weekend_ref"]);
const POLL_MS = 2000;

// ── Small helpers ────────────────────────────────────────────────────────────────────────────
const el = (tag, props = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "style") n.style.cssText = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v !== undefined && v !== null && v !== false) n.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) n.append(k.nodeType ? k : String(k));
  return n;
};

// The Weekend Studio apps a workflow can be kept out of — the same ids and order as @core/comfy's
// COMFY_APPS (packages/comfy/fit.js). An app the suite adds later shows every workflow until listed here.
const STUDIO_APPS = [
  { id: "image-editor", name: "Images" },
  { id: "moods", name: "Moods" },
  { id: "slides", name: "Doc" },
  { id: "sketchbook", name: "Sketchbook" },
  { id: "animate", name: "Animate" },
];

async function call(path, init = {}) {
  const r = await api.fetchApi(path, {
    ...init,
    // X-Weekend-Studio: routes.py's guard. A page on another site can't add this header (ComfyUI's
    // CORS preflight doesn't allow it), so it can't drive these routes even with --enable-cors-header.
    headers: { "content-type": "application/json", "X-Weekend-Studio": "1", ...(init.headers || {}) },
  });
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (!r.ok) {
    const e = new Error(body.message || body.error || `HTTP ${r.status}`);
    e.status = r.status;
    throw e;
  }
  return body;
}

function notify(text) {
  const toast = app.extensionManager?.toast;
  if (toast?.add) toast.add({ severity: "info", summary: "Weekend Studio", detail: text, life: 6000 });
  else window.alert(text);
}

// What this ComfyUI said about itself last (GET /weekend-studio/status): its install id and switches.
// null until the first answer — and while null, nothing is published by itself.
let config = null;
const publishedIds = new Set();

async function refreshConfig() {
  const [status, list] = await Promise.all([
    call("/weekend-studio/status"),
    call("/weekend-studio/workflows").then((r) => r.workflows || []),
  ]);
  config = status;
  publishedIds.clear();
  for (const w of list) publishedIds.add(w.id);
  return { status, list };
}

// The open workflow's choices. Reading never writes: a workflow nobody ticked anything in stays
// byte-for-byte as it was (no "modified" dot). `write` attaches the record when there's a choice.
const EMPTY = Object.freeze({ id: null, marks: Object.freeze([]) });
function studio(write = false) {
  const extra = app.graph?.extra;
  if (extra?.weekendStudio) return extra.weekendStudio;
  if (!write) return EMPTY;
  app.graph.extra ||= {};
  return (app.graph.extra.weekendStudio = { id: null, marks: [], install: config?.installId });
}

// ── Rules (pure; lifted and run by tests/web_rules.test.mjs) ─────────────────────────────────
// Whose choices these are: "none" (nothing set up), "ours" (set up on this ComfyUI), "foreign"
// (set up on another ComfyUI, or before 0.3.0 — the workflow file came with them).
function recordState(s, installId) {
  if (!s || (!s.id && !(s.marks && s.marks.length))) return "none";
  return installId && s.install === installId ? "ours" : "foreign";
}

// Mirrors weekend_studio/description.py remote_refusal (tests/test_refusals.py checks the lists).
const REFUSED_WORDS = new Set(["path", "paths", "filepath", "file", "files", "filename", "filenames", "dir", "dirs", "directory",
  "folder", "folders", "url", "urls", "uri", "link", "code", "script", "command", "cmd", "expr",
  "expression", "eval", "exec", "python", "shell", "module", "import"]);
const REFUSED_NODE_WORDS = new Set(["python", "exec", "execute", "eval", "shell", "script", "command", "cmd", "code", "expr", "expression",
  "subprocess", "bash", "powershell", "terminal", "javascript", "lambda"]);

function words(name) {
  return String(name || "").replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
}

function inputSpec(nodeData, name) {
  return nodeData?.input?.required?.[name] ?? nodeData?.input?.optional?.[name] ?? null;
}

// Why a widget can't be shown in Weekend Studio (in words), or null when it can.
function widgetRefusal(nodeData, name) {
  const spec = inputSpec(nodeData, name);
  if (!spec || spec[0] !== "STRING") return null;
  const nodeWords = [...words(nodeData?.name), ...words(nodeData?.display_name)];
  if (nodeWords.some((w) => REFUSED_NODE_WORDS.has(w))) return "its node looks like it runs code";
  const promptBox = (nodeData?.output || []).includes("CONDITIONING") && !!spec[1]?.multiline;
  if (promptBox) return null;
  if (words(name).some((w) => REFUSED_WORDS.has(w))) return "it looks like a file path, a web address or code";
  return null;
}
// ── end of rules ─────────────────────────────────────────────────────────────────────────────

function markDirty() {
  app.graph.setDirtyCanvas?.(true, true);
  // The new frontend tracks unsaved changes itself; asking it to look marks the tab as modified.
  try { app.extensionManager?.workflow?.activeWorkflow?.changeTracker?.checkState?.(); } catch { /* older frontend */ }
}

function workflowName() {
  const wf = app.extensionManager?.workflow?.activeWorkflow;
  const raw = wf?.filename || wf?.path || "";
  return String(raw).replace(/^.*[\\/]/, "").replace(/\.json$/i, "").trim();
}

const inputNames = (nodeData) => new Set([
  ...Object.keys(nodeData?.input?.required || {}),
  ...Object.keys(nodeData?.input?.optional || {}),
]);

const nodeDefs = new Map();   // class → its definition (for the refusal rule in the picker)

// ── Marks: which settings show in Weekend Studio ─────────────────────────────────────────────
const isMarked = (node, name) => recordState(studio(), config?.installId) === "ours"
  && studio().marks.some((m) => m.node === String(node.id) && m.input === name);

async function toggleMark(node, widget) {
  const state = recordState(studio(), config?.installId);
  if (!config) { notify("Weekend Studio's node isn't answering here. Open its tab to see why."); return; }
  if (state === "foreign") {
    notify("This workflow's Weekend Studio settings came from another ComfyUI. Open the Weekend Studio tab and choose “Use here” first.");
    return;
  }
  const s = studio(true);
  s.install = config.installId;
  if (isMarked(node, widget.name)) {
    s.marks = s.marks.filter((m) => !(m.node === String(node.id) && m.input === widget.name));
  } else {
    s.marks.push({ node: String(node.id), input: widget.name, label: widget.label || undefined });
  }
  markDirty();
  await republishIfShown();
  panel?.refresh();
}

// ── Publishing ───────────────────────────────────────────────────────────────────────────────
// Only ever from something the user did here (the checkbox, Update, Use here, a setting they edited)
// or from the automatic-updates switch they turned on. `fresh` = publish as a new workflow (new id).
async function publish(name, { fresh = false } = {}) {
  if (!config?.installId) throw new Error("Weekend Studio's node isn't answering here.");
  const s = studio(true);
  s.install = config.installId;
  const prompt = (await app.graphToPrompt()).output;
  const sig = runSignature(prompt);
  const body = { name, prompt, marks: s.marks.map(clean) };
  if (s.id && !fresh) body.id = s.id;
  const rec = await call("/weekend-studio/workflows", { method: "POST", body: JSON.stringify(body) });
  s.id = rec.id;
  s.name = rec.name;
  publishedIds.add(rec.id);
  sent.set(s, sig);   // what's in Weekend Studio now — so the sync below doesn't send it a second time
  markDirty();
  return rec;
}

// ── What a run depends on ────────────────────────────────────────────────────────────────────
// The sync below compares this, not the raw API graph (0.2.2). Two things change that graph without
// anyone touching the workflow, and each change republished it — waking the relay and making open
// forms in Weekend Studio stale:
//   · a seed with "control after generate" (randomize/increment) re-rolls on every Queue;
//   · a dynamic-prompt text ({cat|dog}) is rolled afresh by every graphToPrompt — every 3 s here.
// So re-rolled values count as unchanged, and a dynamic prompt counts as its text before rolling.
const CONTROL = /^control_(after|before)_generate$/;
const sent = new WeakMap();   // the workflow's weekendStudio record → the signature last published/seen

function runSignature(prompt) {
  const out = JSON.parse(JSON.stringify(prompt || {}));
  for (const node of app.graph?.nodes ?? app.graph?._nodes ?? []) {
    const inputs = out[String(node.id)]?.inputs;
    const ws = node.widgets || [];
    if (!inputs) continue;
    ws.forEach((w, i) => {
      if (!w?.name || !(w.name in inputs) || Array.isArray(inputs[w.name])) return;   // a wire, not a value
      const rerolled = [...(w.linkedWidgets || []), ws[i + 1]].some((x) => CONTROL.test(x?.name || "")
        && x.value !== "fixed");
      if (rerolled) inputs[w.name] = "~";
      else if (w.options?.dynamicPrompts || w.dynamicPrompts) inputs[w.name] = w.value;
    });
  }
  return JSON.stringify(out);
}

async function unpublish(id) {
  try { await call(`/weekend-studio/workflows/${encodeURIComponent(id)}`, { method: "DELETE" }); }
  catch (e) { if (!/no such workflow/i.test(e.message)) throw e; }
  publishedIds.delete(id);
  const s = studio();
  if (s.id === id) { s.id = null; markDirty(); }
}

// A change to a SHOWN workflow of OUR OWN: send it again under the same id. Never publishes afresh:
// a workflow that isn't in this ComfyUI's list (removed here, or a file from elsewhere) is left alone.
async function republishIfShown() {
  const s = studio();
  if (!s.id || recordState(s, config?.installId) !== "ours" || !publishedIds.has(s.id)) return;
  try {
    const rec = await publish(s.name || workflowName() || "Untitled");
    if (rec.problems?.length) panel?.flash(rec.problems.join(" "), true);
    else panel?.flash("Weekend Studio has your latest changes.");
  } catch (e) {
    if (/no such workflow/i.test(e.message)) {
      publishedIds.delete(s.id);
      panel?.flash("This workflow was removed from Weekend Studio. Tick “Show in Weekend Studio” to add it again.", true);
    } else panel?.flash(e.message, true);
  }
}

const clean = (m) => {
  const out = { node: m.node, input: m.input };
  for (const k of ["label", "group", "help", "role"]) if (typeof m[k] === "string" && m[k].trim()) out[k] = m[k].trim();
  for (const k of ["min", "max", "step"]) if (Number.isFinite(m[k])) out[k] = m[k];
  return out;
};

// ── The tab ──────────────────────────────────────────────────────────────────────────────────
let panel = null;

const CSS = `
.wks { padding: 10px 12px 24px; font-size: 13px; line-height: 1.4; color: var(--fg-color, #ddd); }
.wks h3 { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; opacity: .7; margin: 18px 0 8px; }
.wks h3:first-child { margin-top: 4px; }
.wks p { margin: 0 0 8px; opacity: .85; }
.wks .wks-dim { opacity: .6; font-size: 12px; }
.wks a { color: inherit; text-decoration: underline; }
.wks button { font: inherit; font-size: 12px; padding: 5px 12px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--border-color, #555); background: var(--comfy-input-bg, #222); color: inherit; }
.wks button.wks-primary { background: var(--fg-color, #eee); color: var(--bg-color, #111); border-color: transparent; }
.wks button:disabled { opacity: .5; cursor: default; }
.wks input[type=text], .wks input[type=number] { font: inherit; font-size: 12px; padding: 3px 6px; border-radius: 6px; min-width: 0;
  border: 1px solid var(--border-color, #555); background: var(--comfy-input-bg, #222); color: inherit; }
.wks .wks-row { display: flex; align-items: center; gap: 8px; margin: 6px 0; flex-wrap: wrap; }
.wks .wks-card { border: 1px solid var(--border-color, #444); border-radius: 10px; padding: 10px; margin: 6px 0; }
.wks .wks-code { font-family: ui-monospace, Menlo, monospace; font-size: 20px; letter-spacing: .12em; margin: 4px 0 8px; }
.wks .wks-mark { display: grid; grid-template-columns: 1fr auto; gap: 4px 6px; padding: 6px 0; border-top: 1px solid var(--border-color, #333); }
.wks .wks-mark .wks-sub { grid-column: 1 / -1; display: flex; gap: 6px; }
.wks .wks-mark .wks-sub input { flex: 1; }
.wks .wks-switch { display: flex; align-items: flex-start; gap: 8px; cursor: pointer; margin: 6px 0; }
.wks .wks-switch input { margin-top: 2px; }
.wks .wks-err { color: #ff8a80; }
.wks .wks-showin { gap: 4px 10px; font-size: 12px; }
.wks .wks-check { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; }
.wks .wks-ok { color: #8fd694; }
.wks .wks-next { outline: 2px solid #8fd694; outline-offset: 4px; border-radius: 6px; }
.wks .wks-flash { margin-top: 10px; font-size: 12px; }
.wks details { margin-top: 18px; }
.wks summary { cursor: pointer; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; opacity: .7; }
.wks details ul { margin: 8px 0 0; padding-left: 18px; font-size: 12px; opacity: .85; }
.wks details li { margin: 0 0 6px; }
`;

/** "weekendstudio.app/#comfy": the connect link's site with the short address the site opens the code field at. */
function entryAddress(approveUrl) {
  try { return `${new URL(approveUrl).host}/#comfy`; } catch { return "weekendstudio.app/#comfy"; }
}

function mountPanel(root) {
  if (!document.getElementById("wks-css")) document.head.append(el("style", { id: "wks-css" }, CSS));
  const box = el("div", { class: "wks" });
  root.replaceChildren(box);
  let status = null;
  let published = [];
  let timer = null;
  let flashMsg = null;
  let pendingName = null;   // the machine name typed before Connect
  let nudgeShown = false;   // the "tick Show in Weekend Studio" step was scrolled to (once per time it's needed)
  // Connected, and nothing shown yet: the one step left is the checkbox (owner, 2026-11-05: connecting used
  // to look like it had done nothing, because Weekend Studio's list stays empty until a workflow is shown).
  const nudge = () => status?.state === "connected" && published.length === 0;

  const flash = (text, bad = false) => { flashMsg = { text, bad }; render(); };

  async function load() {
    const before = status?.state;
    try {
      const r = await refreshConfig();
      status = r.status;
      published = r.list;
    } catch (e) {
      status = { state: "error", detail: e.message, forbidden: e.status === 403 };
    }
    // A note about connecting (a blocked tab, an error) is stale once the connection changes.
    if (before && before !== status.state && status.state === "connected") flashMsg = null;
    render();
    clearTimeout(timer);
    if (root.isConnected && ["pairing", "connecting"].includes(status?.state)) timer = setTimeout(load, POLL_MS);
  }

  async function setSwitch(key, value) {
    try {
      status = await call("/weekend-studio/settings", { method: "POST", body: JSON.stringify({ [key]: value }) });
      config = status;
    } catch (e) { flash(e.message, true); }
    load();
  }

  async function connect(nameInput) {
    // No new tab: the tab shows a code, and the person enters it in Weekend Studio (owner, 2026-11-03).
    // The connect page stays one click away for anyone who prefers it.
    const name = (nameInput?.value || "").trim();
    try {
      await call("/weekend-studio/connect", { method: "POST", body: JSON.stringify(name ? { name } : {}) });
    } catch (e) {
      flash(e.message, true);
    }
    load();
  }

  async function disconnect() {
    if (!confirm("Disconnect this ComfyUI from your Weekend Studio account? Its workflows stop showing there.")) return;
    try { await call("/weekend-studio/disconnect", { method: "POST", body: "{}" }); } catch (e) { flash(e.message, true); }
    load();
  }

  const account = () => (status?.account?.handle ? el("b", {}, `@${status.account.handle}`) : "your Weekend Studio account");

  function connectionSection() {
    const st = status?.state;
    const out = [el("h3", {}, "Connection")];
    if (st === "connected") {
      out.push(el("p", {}, el("span", { class: "wks-ok" }, "● "), "Connected to ", account(), " as ",
          el("b", {}, status.machineName || "this ComfyUI"), "."),
        nudge() ? el("p", { class: "wks-ok" }, "Next: tick ", el("b", {}, "Show in Weekend Studio"), " below.") : null,
        el("p", { class: "wks-dim" }, "The workflows you show below appear in Weekend Studio's generator list on any device you're signed in on. Not your account? Disconnect."),
        status.detail ? el("p", { class: "wks-err" }, status.detail) : null,
        el("button", { onClick: disconnect }, "Disconnect"));
    } else if (st === "pairing" && status.pairing) {
      // Where to type it, as an address short enough to type on another device (owner, 2026-11-05: it used
      // to say "under My ComfyUI", four steps deep in an app). The address is also a link carrying the
      // code, for a browser on this computer.
      out.push(el("p", {}, "Enter this code at ",
          el("a", { href: status.pairing.approveUrl, target: "_blank", rel: "noopener" }, entryAddress(status.pairing.approveUrl)), ":"),
        el("div", { class: "wks-code" }, status.pairing.code),
        el("p", { class: "wks-dim" }, `Valid for ${Math.max(1, Math.ceil(status.pairing.expiresIn / 60))} more minute(s).`));
    } else if (st === "connecting") {
      out.push(el("p", {}, "Connecting to ", account(), "…"), status.detail ? el("p", { class: "wks-dim" }, status.detail) : null,
        el("button", { onClick: disconnect }, "Disconnect"));
    } else if (st === "refused") {
      out.push(el("p", { class: "wks-err" }, status.detail || "This account can't connect right now."),
        el("button", { onClick: disconnect }, "Disconnect"));
    } else if (st === "error") {
      out.push(el("p", { class: "wks-err" }, status.forbidden ? status.detail : `The Weekend Studio node isn't answering: ${status.detail}`));
    } else {
      const nameInput = el("input", { type: "text", value: pendingName ?? status?.defaultName ?? "ComfyUI", maxlength: "60",
        placeholder: "Name for this ComfyUI", style: "flex:1", onInput: (e) => { pendingName = e.target.value; } });
      out.push(el("p", {}, "Use your workflows as generators in Weekend Studio, from any device you're signed in on. You pick which settings show; results land straight in your document."),
        status?.detail ? el("p", { class: "wks-dim" }, status.detail) : null,
        el("div", { class: "wks-row" }, nameInput),
        el("p", { class: "wks-dim" }, "This name is how the computer shows in Weekend Studio. It's saved with your account."),
        el("button", { class: "wks-primary", onClick: () => connect(nameInput), disabled: status?.canConnect === false }, "Connect"));
    }
    if (st && st !== "error") {
      const on = !!status?.settings?.sameComputer;
      const check = el("input", { type: "checkbox", ...(on ? { checked: true } : {}), onChange: (e) => setSwitch("sameComputer", e.target.checked) });
      out.push(el("label", { class: "wks-switch" }, check, el("span", {},
        "Use from a browser on this computer, without an account",
        el("br"),
        el("span", { class: "wks-dim" }, on
          ? (status.local?.port ? `On: Weekend Studio's website can reach this ComfyUI at 127.0.0.1:${status.local.port}, from this computer only.` : "On, but it couldn't start: ports 8189–8193 are in use.")
          : "Off. When on, Weekend Studio's website in Chrome or Edge on this computer can run your shown workflows here."))));
    }
    return out;
  }

  function thisWorkflowSection() {
    const s = studio();
    const state = recordState(s, config?.installId);
    const out = [el("h3", {}, "This workflow")];
    if (state === "foreign") {
      const mine = s.id && published.find((w) => w.id === s.id);
      out.push(el("div", { class: "wks-card" },
        el("p", {}, "This workflow came with Weekend Studio settings. They're off here until you choose."),
        s.marks?.length ? el("p", { class: "wks-dim" }, `It would show ${s.marks.length} setting(s): ${s.marks.map((m) => m.label || m.input).join(", ")}.`) : null,
        el("div", { class: "wks-row" },
          mine ? el("button", { class: "wks-primary", onClick: async () => {
            try {
              const rec = await publish(mine.name);
              flash(rec.problems?.length ? rec.problems.join(" ") : `“${rec.name}” in Weekend Studio now uses this workflow.`, !!rec.problems?.length);
            } catch (e) { flash(e.message, true); }
            load();
          } }, `Use here, as my “${mine.name}”`) : null,
          el("button", { class: mine ? undefined : "wks-primary", onClick: async () => {
            try {
              const rec = await publish(s.name || workflowName() || "Untitled", { fresh: true });
              flash(rec.problems?.length ? rec.problems.join(" ") : `“${rec.name}” is in Weekend Studio. Save the workflow to keep this choice in its file.`, !!rec.problems?.length);
            } catch (e) { flash(e.message, true); }
            load();
          } }, mine ? "Use here, as a new workflow" : "Use here and show it in Weekend Studio"),
          el("button", { onClick: () => { delete app.graph.extra.weekendStudio; markDirty(); render(); } }, "Clear these settings"))));
      return out;
    }
    const shown = !!s.id && published.some((w) => w.id === s.id);
    const name = el("input", { type: "text", value: s.name || workflowName() || "", placeholder: "Name in Weekend Studio", style: "flex:1" });
    const check = el("input", { type: "checkbox", ...(shown ? { checked: true } : {}) });
    check.addEventListener("change", async () => {
      check.disabled = true;
      try {
        if (check.checked) {
          const n = name.value.trim() || workflowName() || "Untitled";
          // Ticked by hand: a workflow removed from the list since comes back as a new one.
          const rec = await publish(n, { fresh: !!s.id && !publishedIds.has(s.id) });
          // Something to fix (an unwired Weekend Output…) wins over the "done" note.
          if (rec.problems?.length) flash(rec.problems.join(" "), true);
          else flash(`“${rec.name}” is in Weekend Studio. Save the workflow to keep this choice in its file.`);
        } else {
          await unpublish(s.id);
          flash("Removed from Weekend Studio.");
        }
      } catch (e) { flash(e.message, true); }
      load();
    });
    out.push(el("label", { class: `wks-switch${!shown && nudge() ? " wks-next" : ""}` }, check, "Show in Weekend Studio"),
      el("div", { class: "wks-row" }, name));
    if (shown) {
      const auto = !!config?.settings?.autoSync;
      out.push(el("div", { class: "wks-row" },
        el("button", { onClick: async () => {
          s.name = name.value.trim() || s.name;
          try {
            const rec = await publish(s.name);
            if (rec.problems?.length) flash(rec.problems.join(" "), true); else flash("Updated in Weekend Studio.");
          } catch (e) { flash(e.message, true); }
          load();
        } }, "Update"),
        el("span", { class: "wks-dim" }, auto ? "Changes are also sent by themselves." : "Sends your latest changes.")));
      out.push(el("p", { class: auto ? "wks-ok" : "wks-dim" }, auto ? "● Automatic updates are on" : "Automatic updates are off"));
      const desc = published.find((w) => w.id === s.id)?.description;
      if (desc?.missing?.length) out.push(el("p", { class: "wks-err" }, `Needs nodes that aren't installed: ${desc.missing.join(", ")}`));
    }

    out.push(el("h3", {}, "Settings shown"));
    if (!s.marks.length) {
      out.push(el("p", { class: "wks-dim" }, "The usual ones: prompt, negative prompt, size, seed, steps, CFG, sampler. To choose your own, right-click a node → Weekend Studio and tick a setting."));
    } else {
      for (const m of s.marks) {
        const node = app.graph.getNodeById?.(Number(m.node)) ?? app.graph.getNodeById?.(m.node);
        const widget = node?.widgets?.find((w) => w.name === m.input);
        const numeric = typeof widget?.value === "number";
        const text = typeof widget?.value === "string" && !Array.isArray(widget?.options?.values);
        const refused = node ? widgetRefusal(nodeDefs.get(node.comfyClass || node.type), m.input) : null;
        const label = el("input", { type: "text", value: m.label || "", placeholder: widget?.label || m.input });
        const group = el("input", { type: "text", value: m.group || "", placeholder: "Group (optional)" });
        const save = async () => {
          m.label = label.value.trim() || undefined;
          m.group = group.value.trim() || undefined;
          if (numeric) { m.min = num(min.value); m.max = num(max.value); }
          markDirty();
          await republishIfShown();
        };
        const min = el("input", { type: "number", value: m.min ?? "", placeholder: "min", style: "width:70px" });
        const max = el("input", { type: "number", value: m.max ?? "", placeholder: "max", style: "width:70px" });
        for (const i of [label, group, min, max]) i.addEventListener("change", save);
        out.push(el("div", { class: "wks-mark" },
          el("div", {}, el("b", {}, node?.title || `Node ${m.node}`), el("span", { class: "wks-dim" }, ` · ${m.input}`)),
          el("button", { title: "Stop showing this setting", onClick: async () => { s.marks = s.marks.filter((x) => x !== m); markDirty(); await republishIfShown(); render(); } }, "×"),
          el("div", { class: "wks-sub" }, label, group),
          numeric ? el("div", { class: "wks-sub" }, min, max) : null,
          refused ? el("div", { class: "wks-sub wks-err" }, `Not shown: ${refused}.`) : null,
          text && !refused ? el("div", { class: "wks-sub wks-dim" }, "Can be set from the web: anyone signed in to your account can type into it.") : null,
          !node ? el("div", { class: "wks-sub wks-err" }, "This node is gone from the workflow.") : null));
      }
    }
    return out;
  }

  function publishedSection() {
    const out = [el("h3", {}, "In Weekend Studio")];
    const auto = !!config?.settings?.autoSync;
    out.push(el("label", { class: "wks-switch" },
      el("input", { type: "checkbox", ...(auto ? { checked: true } : {}), onChange: (e) => setSwitch("autoSync", e.target.checked) }),
      el("span", {}, "Update shown workflows automatically", el("br"),
        el("span", { class: "wks-dim" }, auto
          ? "On: while a workflow you showed from this ComfyUI is open, its changes are sent a few seconds after you stop editing."
          : "Off: changes reach Weekend Studio when you click Update."))));
    if (!published.length) {
      out.push(el("p", { class: "wks-dim" }, "Nothing yet. Open a workflow and tick “Show in Weekend Studio”."));
      return out;
    }
    for (const w of published) {
      const missing = w.description?.missing || [];
      out.push(el("div", { class: "wks-card" },
        el("div", { class: "wks-row", style: "justify-content:space-between" },
          el("b", {}, w.name),
          el("button", { onClick: async () => {
            if (!confirm(`Stop showing “${w.name}” in Weekend Studio?`)) return;
            try { await unpublish(w.id); } catch (e) { flash(e.message, true); }
            load();
          } }, "Remove")),
        el("div", { class: "wks-dim" }, `${(w.description?.inputs || []).length} settings`),
        missing.length ? el("div", { class: "wks-err" }, `Needs: ${missing.join(", ")}`) : null,
        showInRow(w)));
    }
    return out;
  }

  // SHOW IN (0.3.1): every app that can use a workflow shows it — a picture workflow everywhere, one
  // that needs a selection only in Images. Untick an app to keep this workflow out of it. Only that list
  // changes (not the workflow or its version), so forms open in Weekend Studio stay as they are.
  function showInRow(w) {
    const hidden = new Set(w.description?.hiddenIn || []);
    return el("div", { class: "wks-row wks-showin" },
      el("span", { class: "wks-dim" }, "Show in:"),
      ...STUDIO_APPS.map((a) => el("label", { class: "wks-check" },
        el("input", { type: "checkbox", ...(hidden.has(a.id) ? {} : { checked: true }), onChange: async (e) => {
          const next = new Set(hidden);
          if (e.target.checked) next.delete(a.id); else next.add(a.id);
          try {
            await call(`/weekend-studio/workflows/${encodeURIComponent(w.id)}/show-in`, { method: "POST", body: JSON.stringify({ hiddenIn: [...next] }) });
          } catch (err) { flash(err.message, true); }
          load();
        } }),
        a.name)));
  }

  // Exactly what leaves this computer, and when (security review B5). Same words as README.md.
  function networkSection() {
    return [el("details", {},
      el("summary", {}, "Network and data"),
      el("ul", {},
        el("li", {}, el("b", {}, "Until you click Connect, nothing leaves this computer. "), "Same-computer mode, when you turn it on, answers on 127.0.0.1 only: no picture, prompt or setting leaves. If you're signed in, Weekend Studio records that a run was made for the document you're in, so its AI label stays true."),
        el("li", {}, el("b", {}, "Connect: "), "the name you give this ComfyUI goes to Weekend Studio (weekendstudio.app's servers) with a one-time code. You approve it in your browser."),
        el("li", {}, el("b", {}, "While connected: "), "one connection out to comfy.weekendindustries.app. For each workflow you show: its name, the settings you picked with their labels and current values (prompt text included), dropdown lists (for a model setting, the file names of your installed models), node titles, and the names of missing nodes. Never the workflow itself, other workflows, your files or this computer's name."),
        el("li", {}, el("b", {}, "During a run: "), "progress, errors (folder paths under your home folder shown as ~), and the result pictures, with the workflow and other metadata removed. Pictures pass through Weekend Studio's storage and are deleted once your browser has them (at most a day)."),
        el("li", {}, el("b", {}, "Kept by Weekend Studio: "), "this computer's name and the list above, until you remove the computer, or 30 days after it was last connected."),
        el("li", {}, el("b", {}, "Stop: "), "Disconnect here, or remove the computer in Weekend Studio under Account → ComfyUI machines. Uninstalling the node and deleting its folder removes everything it keeps.")))];
  }

  function render() {
    box.replaceChildren(...[
      ...connectionSection(),
      ...(status?.state === "error" ? [] : [...thisWorkflowSection(), ...publishedSection()]),
      flashMsg ? el("p", { class: `wks-flash ${flashMsg.bad ? "wks-err" : "wks-ok"}` }, flashMsg.text) : null,
      ...networkSection(),
    ].filter(Boolean));
    if (!nudge()) nudgeShown = false;
    else if (!nudgeShown) {
      const next = box.querySelector(".wks-next");
      if (next) { next.scrollIntoView({ block: "nearest" }); nudgeShown = true; }
    }
  }

  panel = { refresh: load, flash };
  render();
  load();
  // Another workflow opened in ComfyUI: show ITS choices. (Watching the graph object is cheap;
  // ComfyUI's own change events fire on every edit.)
  let seen = app.graph;
  let seenStudio = studio();
  const watch = setInterval(() => {
    if (!root.isConnected) { clearInterval(watch); clearTimeout(timer); return; }
    if (app.graph !== seen || studio() !== seenStudio) { seen = app.graph; seenStudio = studio(); render(); }
  }, 1500);
}

const num = (v) => (v === "" || v === null || v === undefined || !Number.isFinite(Number(v)) ? undefined : Number(v));

// ── Registration ─────────────────────────────────────────────────────────────────────────────
app.registerExtension({
  name: "WeekendStudio",

  async setup() {
    // AUTOMATIC UPDATES (2026-10-14; a switch since 0.3.0, off until the user turns it on): when the
    // open workflow is one THIS ComfyUI shows in Weekend Studio and what it would RUN changes (the API
    // graph — moving nodes doesn't count), it's sent again after one quiet look. The first look at a
    // workflow just records it, so opening one never republishes it. A workflow set up elsewhere, or
    // removed from the list, is never sent by itself (security review B2).
    // Compared by runSignature(): re-rolled seeds and dynamic prompts don't count as changes.
    refreshConfig().catch(() => { /* the tab says why when it's opened; until then nothing is sent */ });
    let waiting = null;
    setInterval(async () => {
      if (!config?.settings?.autoSync) return;
      const s = studio();
      if (!s.id || recordState(s, config.installId) !== "ours" || !publishedIds.has(s.id)) return;
      let sig;
      try { sig = runSignature((await app.graphToPrompt()).output); } catch { return; }
      if (!sent.has(s)) { sent.set(s, sig); return; }
      if (sig === sent.get(s)) { waiting = null; return; }
      if (waiting !== sig) { waiting = sig; return; }   // still changing: wait for a quiet look
      sent.set(s, sig);
      waiting = null;
      await republishIfShown();
    }, 3000);

    const mgr = app.extensionManager;
    if (mgr?.registerSidebarTab) {
      mgr.registerSidebarTab({
        id: "weekend-studio",
        icon: "pi pi-images",
        title: "Weekend Studio",
        tooltip: "Weekend Studio",
        type: "custom",
        render: (root) => mountPanel(root),
      });
    } else {
      // An older frontend without sidebar tabs: a corner button opening the same panel.
      const holder = el("div", { style: "position:fixed;right:12px;bottom:12px;width:320px;max-height:70vh;overflow:auto;z-index:1000;display:none;background:var(--comfy-menu-bg,#222);border:1px solid var(--border-color,#444);border-radius:12px" });
      const btn = el("button", { style: "position:fixed;right:12px;bottom:12px;z-index:1001;padding:6px 12px;border-radius:999px", onClick: () => {
        const open = holder.style.display === "none";
        holder.style.display = open ? "block" : "none";
        btn.style.bottom = open ? "calc(70vh + 20px)" : "12px";
        if (open) mountPanel(holder);
      } }, "Weekend Studio");
      document.body.append(holder, btn);
    }
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (BRIDGE.has(nodeData.name)) return;   // their pictures come from the app; nothing to tick
    nodeDefs.set(nodeData.name, nodeData);
    const names = inputNames(nodeData);
    const orig = nodeType.prototype.getExtraMenuOptions;
    nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
      const r = orig?.apply(this, arguments);
      const widgets = (this.widgets || []).filter((w) => w?.name && names.has(w.name) && !SKIP_WIDGETS.has(w.name)
        && w.type !== "button" && w.type !== "converted-widget" && !w.hidden);
      if (widgets.length) {
        options.push(null, {
          content: "Weekend Studio",
          has_submenu: true,
          submenu: {
            title: "Show in Weekend Studio",
            options: widgets.map((w) => {
              const refused = widgetRefusal(nodeData, w.name);
              return refused
                ? { content: `${w.label || w.name} (can't be shown: ${refused})`, disabled: true }
                : { content: `${isMarked(this, w.name) ? "✓ " : ""}${w.label || w.name}`, callback: () => { toggleMark(this, w); } };
            }),
          },
        });
      }
      return r;
    };
  },

  nodeCreated(node) {
    // The bridge nodes' weekend_ref is filled per run by the node pack; nobody types into it.
    if (!BRIDGE.has(node.comfyClass)) return;
    const w = node.widgets?.find((x) => x.name === "weekend_ref");
    if (w) {
      w.hidden = true;
      w.type = "hidden";
      w.computeSize = () => [0, -4];
      node.setSize?.(node.computeSize?.() || node.size);
    }
  },
});
