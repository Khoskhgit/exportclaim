"use strict";
const $ = (id) => document.getElementById(id);
const baht = new Intl.NumberFormat("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const count = new Intl.NumberFormat("th-TH");

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key in node) node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) if (child !== null && child !== undefined && child !== false) node.append(child);
  return node;
}

async function api(path, body) {
  const options = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && data.login) { window.location.assign("/login?error=expired"); throw new Error(data.detail); }
  if (!response.ok) { const err = new Error(data.detail || `ผิดพลาด (${response.status})`); err.data = data; throw err; }
  return data;
}

// ------------------------------------------------------------- messages
// Errors and lasting warnings stay at the top until closed; short messages are toasts that fade out.
function notice(message, kind = "error", lasting = false) {
  const close = el("button", { type: "button", class: "notice-close", "aria-label": "ปิดข้อความ" }, "×");
  const box = el("div", { class: `notice ${kind}${lasting ? " lasting" : ""}`, role: kind === "error" ? "alert" : "status" },
    el("span", { class: "notice-icon", "aria-hidden": "true" }, { error: "!", warn: "!", ok: "✓" }[kind] || "i"),
    el("div", { class: "notice-text" }, message), close);
  close.onclick = () => box.remove();
  if (kind === "error" || lasting) {
    $("notices").append(box);
  } else {
    let region = $("toasts");
    if (!region) { region = el("div", { id: "toasts", class: "toasts" }); document.body.append(region); }
    box.classList.add("toast");
    region.append(box);
    setTimeout(() => box.remove(), kind === "ok" ? 10000 : 8000);
  }
  return box;
}
function clearNotices() { $("notices").querySelectorAll(".notice:not(.lasting)").forEach((box) => box.remove()); }

function confirmDialog(title, content, okLabel) {
  return new Promise((resolve) => {
    const dialog = $("confirm");
    $("confirm-title").textContent = title;
    $("confirm-body").replaceChildren(...content);
    $("confirm-ok").textContent = okLabel;
    const done = (value) => { dialog.close(); $("confirm-ok").onclick = $("confirm-cancel").onclick = null; resolve(value); };
    $("confirm-ok").onclick = () => done(true);
    $("confirm-cancel").onclick = () => done(false);
    dialog.oncancel = (e) => { e.preventDefault(); done(false); };
    dialog.showModal();
  });
}

function setConnection(text, ok) {
  $("connection").replaceChildren(el("span", { class: `dot ${ok ? "ok" : "bad"}`, "aria-hidden": "true" }), el("span", {}, text));
}

// ---------------------------------------------------------------- cards
// Summary cards double as the status filter. cats: {key: [label, test, tone]}.
function renderCards(container, cats, rows, current, amountOf, pick) {
  container.replaceChildren(...Object.entries(cats).map(([key, [label, test, tone]]) => {
    const hits = rows.filter(test);
    const amount = hits.reduce((sum, r) => sum + Number(amountOf(r) || 0), 0);
    return el("button", { class: `card tone-${tone}`, type: "button", "aria-pressed": String(current === key), onclick: () => pick(key) },
      el("span", { class: "card-label" }, label),
      el("span", { class: "card-count" }, count.format(hits.length), el("small", {}, " คน")),
      el("span", { class: "card-amount" }, `${baht.format(amount)} บาท`));
  }));
}

// -------------------------------------------------------------- sorting
// Header cells with data-sort="field" become sort buttons. sort = {key, dir}.
function bindSort(thead, sort, onChange) {
  thead.querySelectorAll("th[data-sort]").forEach((th) => {
    const label = th.textContent;
    const button = el("button", { type: "button", class: "sort" }, label, el("span", { class: "sort-mark", "aria-hidden": "true" }));
    th.replaceChildren(button);
    button.onclick = () => {
      const key = th.dataset.sort;
      sort.dir = sort.key === key && sort.dir === "asc" ? "desc" : "asc";
      sort.key = key;
      onChange();
    };
  });
}
function showSort(thead, sort) {
  thead.querySelectorAll("th[data-sort]").forEach((th) => {
    th.setAttribute("aria-sort", th.dataset.sort === sort.key ? (sort.dir === "asc" ? "ascending" : "descending") : "none");
  });
}
function sortRows(list, sort, numeric = []) {
  if (!sort.key) return list;
  const sign = sort.dir === "desc" ? -1 : 1;
  const key = sort.key;
  return [...list].sort((a, b) => {
    if (numeric.includes(key)) return sign * (Number(a[key] || 0) - Number(b[key] || 0));
    return sign * String(a[key] ?? "").localeCompare(String(b[key] ?? ""), "th", { numeric: true });
  });
}

// ----------------------------------------------------------------- tabs
let lastTab = "";
function tabs(defs) {
  const chosen = defs.find((d) => d.id === lastTab) || defs[0];
  const bar = el("div", { class: "tabs", role: "tablist" });
  const panels = [];
  for (const d of defs) {
    const panel = el("div", { class: "tab-panel", role: "tabpanel", id: `tab-${d.id}`, hidden: d !== chosen }, ...d.content);
    const button = el("button", { type: "button", role: "tab", class: `tab${d.alert ? " alert" : ""}`, "aria-selected": String(d === chosen), "aria-controls": `tab-${d.id}` },
      d.label, d.badge !== undefined ? el("span", { class: "tab-badge" }, String(d.badge)) : null);
    button.onclick = () => {
      lastTab = d.id;
      bar.querySelectorAll(".tab").forEach((b) => b.setAttribute("aria-selected", String(b === button)));
      panels.forEach((p) => { p.hidden = p !== panel; });
    };
    bar.append(button);
    panels.push(panel);
  }
  return [bar, ...panels];
}

// --------------------------------------------------------------- search
function isoDate(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function setupPresets(form) {
  form.querySelectorAll("[data-preset]").forEach((button) => {
    button.onclick = () => {
      const today = new Date();
      const back = button.dataset.preset === "last" ? 1 : 0;
      const first = new Date(today.getFullYear(), today.getMonth() - back, 1);
      const last = back ? new Date(today.getFullYear(), today.getMonth(), 0) : today;
      form.elements.start.value = isoDate(first);
      form.elements.end.value = isoDate(last);
    };
  });
}
function updatePttypeLabel() {
  // Show which rights are picked (code: name), not just how many; the full list is in the tooltip.
  const boxes = [...document.querySelectorAll("#pttype-list input.one")];
  const chosen = boxes.filter((b) => b.checked).map((b) => b.parentElement.textContent.trim());
  const label = $("pttype-label");
  if (!chosen.length) label.textContent = "ยังไม่ได้เลือกสิทธิ";
  else if (chosen.length === boxes.length) label.textContent = `ทุกสิทธิ (${boxes.length})`;
  else label.textContent = `${chosen.length} สิทธิ: ${chosen.slice(0, 3).join(", ")}${chosen.length > 3 ? ` +${chosen.length - 3}` : ""}`;
  label.title = chosen.length && chosen.length < boxes.length ? chosen.join("\n") : "";
  const all = document.querySelector("#pttype-list label.all input");
  if (all) { all.checked = chosen.length === boxes.length; all.indeterminate = chosen.length > 0 && chosen.length < boxes.length; }
}
function fillPttypes(list) {
  const all = el("input", { type: "checkbox", checked: true, onchange: (e) => {
    document.querySelectorAll("#pttype-list .one").forEach((box) => { box.checked = e.target.checked; });
    updatePttypeLabel();
  } });
  $("pttype-list").replaceChildren(
    el("label", { class: "all" }, all, "เลือกทั้งหมด"),
    ...list.map((p) => el("label", {}, el("input", { type: "checkbox", class: "one", value: p.pttype, checked: true, onchange: updatePttypeLabel }), `${p.pttype}: ${p.name}`)));
  updatePttypeLabel();
}

function problemText(list) {
  if (!list.length) return el("span", { class: "muted" }, "—");
  const first = list[0];
  return el("span", { class: "problem-line", title: list.join("\n") }, first, list.length > 1 ? el("span", { class: "more" }, ` +${list.length - 1}`) : null);
}

function emptyState(text, icon = "○") {
  return [el("div", { class: "empty-icon", "aria-hidden": "true" }, icon), el("div", {}, text)];
}

// ----------------------------------------------------------------- user
async function showUser() {
  try {
    const me = await api("/api/me");
    if (!me.login_required || !me.name) return;
    const initials = me.name.replace(/^(นาย|นาง|นางสาว|น\.ส\.|นพ\.|พญ\.|ดร\.)\s*/, "").trim().slice(0, 1);
    $("user-slot").replaceChildren(el("div", { class: "user-box" },
      el("span", { class: "avatar", "aria-hidden": "true" }, initials),
      el("span", { class: "user-name" }, me.name, me.method === "moph" ? el("small", {}, "MOPH ID") : el("small", {}, "HOSxP")),
      el("button", { type: "button", class: "ghost small", onclick: async () => {
        await fetch("/auth/logout", { method: "POST" }).catch(() => null);
        window.location.assign("/login");
      } }, "ออกจากระบบ")));
  } catch (err) { /* the page shows its own errors */ }
}
showUser();
