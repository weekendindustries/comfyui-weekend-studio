// Weekend Studio inside ComfyUI: the sidebar tab and the right-click setting picker.
// Plan: reviews/comfyui-plan-2026-10-14.md ("Where the user turns it on", "The node pack").
//
//   Tab       Connect / Disconnect; "Show in Weekend Studio" for the open workflow; the settings it
//             shows (rename, group, limits); every published workflow.
//   Picker    right-click a node → Weekend Studio → tick a setting to show it in Weekend Studio.
//
// The choices live in the workflow itself (workflow.extra.weekendStudio = { id, marks }), so they
// travel with the .json; publishing sends the API-format graph + marks to this ComfyUI's own
// /weekend-studio routes (routes.py). Nothing here talks to Weekend Studio directly.

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

async function call(path, init = {}) {
  const r = await api.fetchApi(path, { ...init, headers: { "content-type": "application/json", ...(init.headers || {}) } });
  let body = {};
  try { body = await r.json(); } catch { /* empty */ }
  if (!r.ok) throw new Error(body.message || body.error || `HTTP ${r.status}`);
  return body;
}

// The open workflow's choices. Reading never writes: a workflow nobody ticked anything in stays
// byte-for-byte as it was (no "modified" dot). `write` attaches the record when there's a choice.
const EMPTY = Object.freeze({ id: null, marks: Object.freeze([]) });
function studio(write = false) {
  const extra = app.graph?.extra;
  if (extra?.weekendStudio) return extra.weekendStudio;
  if (!write) return EMPTY;
  app.graph.extra ||= {};
  return (app.graph.extra.weekendStudio = { id: null, marks: [] });
}

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

// ── Marks: which settings show in Weekend Studio ─────────────────────────────────────────────
const isMarked = (node, name) => studio().marks.some((m) => m.node === String(node.id) && m.input === name);

async function toggleMark(node, widget) {
  const s = studio(true);
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
async function publish(name) {
  const s = studio(true);
  const prompt = (await app.graphToPrompt()).output;
  const body = { name, prompt, marks: s.marks.map(clean) };
  if (s.id) body.id = s.id;
  let rec;
  try {
    rec = await call("/weekend-studio/workflows", { method: "POST", body: JSON.stringify(body) });
  } catch (e) {
    if (s.id && /no such workflow/i.test(e.message)) {   // removed elsewhere: publish it afresh
      delete body.id;
      rec = await call("/weekend-studio/workflows", { method: "POST", body: JSON.stringify(body) });
    } else throw e;
  }
  s.id = rec.id;
  s.name = rec.name;
  markDirty();
  return rec;
}

async function unpublish(id) {
  try { await call(`/weekend-studio/workflows/${encodeURIComponent(id)}`, { method: "DELETE" }); }
  catch (e) { if (!/no such workflow/i.test(e.message)) throw e; }
  const s = studio();
  if (s.id === id) { s.id = null; markDirty(); }
}

async function republishIfShown() {
  const s = studio();
  if (!s.id) return;
  try { await publish(s.name || workflowName() || "Untitled"); } catch (e) { panel?.flash(e.message, true); }
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
.wks .wks-row { display: flex; align-items: center; gap: 8px; margin: 6px 0; }
.wks .wks-card { border: 1px solid var(--border-color, #444); border-radius: 10px; padding: 10px; margin: 6px 0; }
.wks .wks-code { font-family: ui-monospace, Menlo, monospace; font-size: 20px; letter-spacing: .12em; margin: 4px 0 8px; }
.wks .wks-mark { display: grid; grid-template-columns: 1fr auto; gap: 4px 6px; padding: 6px 0; border-top: 1px solid var(--border-color, #333); }
.wks .wks-mark .wks-sub { grid-column: 1 / -1; display: flex; gap: 6px; }
.wks .wks-mark .wks-sub input { flex: 1; }
.wks .wks-switch { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.wks .wks-err { color: #ff8a80; }
.wks .wks-ok { color: #8fd694; }
.wks .wks-flash { margin-top: 10px; font-size: 12px; }
`;

function mountPanel(root) {
  if (!document.getElementById("wks-css")) document.head.append(el("style", { id: "wks-css" }, CSS));
  const box = el("div", { class: "wks" });
  root.replaceChildren(box);
  let status = null;
  let published = [];
  let timer = null;
  let flashMsg = null;

  const flash = (text, bad = false) => { flashMsg = { text, bad }; render(); };

  async function load() {
    const before = status?.state;
    try {
      [status, published] = await Promise.all([
        call("/weekend-studio/status"),
        call("/weekend-studio/workflows").then((r) => r.workflows || []),
      ]);
    } catch (e) {
      status = { state: "error", detail: e.message };
    }
    // A note about connecting (a blocked tab, an error) is stale once the connection changes.
    if (before && before !== status.state && status.state === "connected") flashMsg = null;
    render();
    clearTimeout(timer);
    if (root.isConnected && ["pairing", "connecting"].includes(status?.state)) timer = setTimeout(load, POLL_MS);
  }

  async function connect() {
    // Opened inside the click so no popup blocker gets in the way; pointed at the page once known.
    const tab = window.open("about:blank", "_blank");
    try {
      const p = await call("/weekend-studio/connect", { method: "POST", body: JSON.stringify({}) });
      if (tab) tab.location.href = p.approveUrl; else flash("Your browser blocked the new tab. Use “Open the connect page” above.");
    } catch (e) {
      tab?.close();
      flash(e.message, true);
    }
    load();
  }

  async function disconnect() {
    if (!confirm("Disconnect this ComfyUI from your Weekend Studio account? Its workflows stop showing there.")) return;
    try { await call("/weekend-studio/disconnect", { method: "POST", body: "{}" }); } catch (e) { flash(e.message, true); }
    load();
  }

  function connectionSection() {
    const st = status?.state;
    const out = [el("h3", {}, "Connection")];
    if (st === "connected") {
      out.push(el("p", {}, el("span", { class: "wks-ok" }, "● "), "Connected as ", el("b", {}, status.machineName || "this ComfyUI"), "."),
        el("p", { class: "wks-dim" }, "The workflows you show below appear in Weekend Studio's generator list on any device you're signed in on."),
        el("button", { onClick: disconnect }, "Disconnect"));
    } else if (st === "pairing" && status.pairing) {
      out.push(el("p", {}, "Finish in the browser tab that opened: click ", el("b", {}, "Connect"), ". Your code:"),
        el("div", { class: "wks-code" }, status.pairing.code),
        el("p", { class: "wks-dim" }, `Valid for ${Math.max(1, Math.ceil(status.pairing.expiresIn / 60))} more minute(s). `,
          el("a", { href: status.pairing.approveUrl, target: "_blank", rel: "noopener" }, "Open the connect page")));
    } else if (st === "connecting") {
      out.push(el("p", {}, "Connecting…"), status.detail ? el("p", { class: "wks-dim" }, status.detail) : null);
    } else if (st === "refused") {
      out.push(el("p", { class: "wks-err" }, status.detail || "This account can't connect right now."));
    } else if (st === "error") {
      out.push(el("p", { class: "wks-err" }, `The Weekend Studio node isn't answering: ${status.detail}`));
    } else {
      out.push(el("p", {}, "Use your workflows as generators in Weekend Studio. You pick which settings show; results land straight in your document."),
        status?.detail ? el("p", { class: "wks-dim" }, status.detail) : null,
        el("button", { class: "wks-primary", onClick: connect }, "Connect"));
    }
    return out;
  }

  function thisWorkflowSection() {
    const s = studio();
    const shown = !!s.id && published.some((w) => w.id === s.id);
    const out = [el("h3", {}, "This workflow")];
    const name = el("input", { type: "text", value: s.name || workflowName() || "", placeholder: "Name in Weekend Studio", style: "flex:1" });
    const check = el("input", { type: "checkbox", ...(shown ? { checked: true } : {}) });
    check.addEventListener("change", async () => {
      check.disabled = true;
      try {
        if (check.checked) {
          const n = name.value.trim() || workflowName() || "Untitled";
          const rec = await publish(n);
          flash(`“${rec.name}” is in Weekend Studio. Save the workflow to keep this choice in its file.`);
        } else {
          await unpublish(s.id);
          flash("Removed from Weekend Studio.");
        }
      } catch (e) { flash(e.message, true); }
      load();
    });
    out.push(el("label", { class: "wks-switch" }, check, "Show in Weekend Studio"),
      el("div", { class: "wks-row" }, name));
    if (shown) {
      out.push(el("div", { class: "wks-row" },
        el("button", { onClick: async () => { s.name = name.value.trim() || s.name; try { await publish(s.name); flash("Updated in Weekend Studio."); } catch (e) { flash(e.message, true); } load(); } }, "Update"),
        el("span", { class: "wks-dim" }, "Sends your latest changes.")));
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
          !node ? el("div", { class: "wks-sub wks-err" }, "This node is gone from the workflow.") : null));
      }
    }
    return out;
  }

  function publishedSection() {
    const out = [el("h3", {}, "In Weekend Studio")];
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
        missing.length ? el("div", { class: "wks-err" }, `Needs: ${missing.join(", ")}`) : null));
    }
    return out;
  }

  function render() {
    box.replaceChildren(...[
      ...connectionSection(),
      ...thisWorkflowSection(),
      ...publishedSection(),
      flashMsg ? el("p", { class: `wks-flash ${flashMsg.bad ? "wks-err" : "wks-ok"}` }, flashMsg.text) : null,
    ].filter(Boolean));
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
            options: widgets.map((w) => ({
              content: `${isMarked(this, w.name) ? "✓ " : ""}${w.label || w.name}`,
              callback: () => { toggleMark(this, w); },
            })),
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
