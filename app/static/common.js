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
  // A table row that opens details is reachable with Tab and opens with Enter / Space (see the keydown below).
  if (tag === "tr" && typeof props.onclick === "function") { node.tabIndex = 0; node.classList.add("row-link"); }
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

// A main button that cannot be pressed yet says why, in a line just before it (read out with the button).
// Pass "" to enable it. Buttons turned off only while they work ("กำลังสร้าง…") keep using .disabled.
function explainDisabled(button, reason) {
  button.disabled = Boolean(reason);
  button.title = reason || "";
  const id = `${button.id}-why`;
  let hint = document.getElementById(id);
  if (!hint) {
    hint = el("span", { id, class: "why-hint", "aria-live": "polite" });
    button.before(hint);
    button.setAttribute("aria-describedby", id);
  }
  hint.textContent = reason || "";
  hint.hidden = !reason;
}
// Buttons that start off say why from the first paint (the page script updates the reason after a search).
document.querySelectorAll("button[data-why]:disabled").forEach((button) => explainDisabled(button, button.dataset.why));

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

// blocking: HOSxP cannot be searched (false for a reachable database that only misses some columns).
function setConnection(text, ok, blocking = !ok) {
  $("connection").replaceChildren(el("span", { class: `dot ${ok ? "ok" : "bad"}`, "aria-hidden": "true" }), el("span", {}, text));
  $("connection").title = text;
  dbProblem = blocking ? text : "";
  if (blocking) markDbDown();
}

// ------------------------------------------------------- HOSxP down
// When HOSxP cannot be reached the pages that search it say so where the user looks (the rights box and the
// table), and a search shows the same state instead of a misleading "pick the rights" error.
let dbProblem = "";
const DB_ERROR = /\((2003|2005|2006|2013)\)|Can't connect|Lost connection|เชื่อมต่อฐาน|ต่อฐาน HOSxP/i;
function dbDownState() {
  const retry = el("button", { type: "button", class: "primary", onclick: async () => {
    retry.disabled = true; retry.textContent = "กำลังตรวจ…";
    try {
      const status = await api("/api/status");
      if (status.database && !status.db_error) { window.location.reload(); return; }
      notice(status.db_error || "ยังเชื่อมต่อ HOSxP ไม่ได้");
    } catch (err) { notice(err.message); }
    retry.disabled = false; retry.textContent = "ลองเชื่อมต่อใหม่";
  } }, "ลองเชื่อมต่อใหม่");
  return [el("div", { class: "empty-icon", "data-icon": "error", "aria-hidden": "true" }),
    el("div", { class: "empty-title" }, "ต่อฐานข้อมูล HOSxP ไม่ได้ จึงยังค้นหาไม่ได้"),
    el("div", { class: "empty-hint" }, `${dbProblem || "เชื่อมต่อไม่ได้"} · ตรวจว่าเครื่อง server HOSxP เปิดอยู่และเครือข่ายใช้ได้ แล้วกดลองใหม่`),
    el("div", { class: "empty-action" }, retry)];
}
function markDbDown() {
  const label = $("pttype-label");
  if (label && !document.querySelector("#pttype-list input")) {
    label.textContent = "โหลดสิทธิไม่ได้ (ต่อ HOSxP ไม่ได้)";
    $("pttypes")?.classList.add("is-error");
  }
  const empty = $("empty"), rows = $("rows");
  if (empty && document.body.dataset.hosxp === "search" && (!rows || !rows.children.length)) { empty.hidden = false; empty.replaceChildren(...dbDownState()); }
}
// Call at the start of a HOSxP search: true (and the state shown) when it cannot run.
function dbBlocked() {
  if (!dbProblem) return false;
  const empty = $("empty");
  if (empty) { $("rows")?.replaceChildren(); empty.hidden = false; empty.replaceChildren(...dbDownState()); }
  else notice(`ต่อฐานข้อมูล HOSxP ไม่ได้: ${dbProblem}`);
  return true;
}
// The empty-state for a failed search: the HOSxP-down state for a connection error, else the message.
function searchFailed(err) {
  if (DB_ERROR.test(err.message)) { dbProblem = dbProblem || err.message; return dbDownState(); }
  return emptyState(err.message, "!");
}

// ---------------------------------------------------------------- cards
// Summary cards double as the status filter. cats: {key: [label, test, tone]}.
function renderCards(container, cats, rows, current, amountOf, pick, unit = "คน") {
  // A category may bring its own amount (e.g. the paid card sums what the STM paid, not what was claimed).
  container.replaceChildren(...Object.entries(cats).map(([key, [label, test, tone, ownAmount]]) => {
    const hits = rows.filter(test);
    const amount = hits.reduce((sum, r) => sum + Number((ownAmount || amountOf)(r) || 0), 0);
    return el("button", { class: `card tone-${tone}`, type: "button", "aria-pressed": String(current === key), onclick: () => pick(key) },
      el("span", { class: "card-label" }, label),
      el("span", { class: "card-count" }, count.format(hits.length), el("small", {}, ` ${unit}`)),
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
// Date boxes: dd/mm/yyyy in the Buddhist year, with a Thai calendar of our own (the browser's picker follows the OS
// language and the Christian year). The date input stays in the form (hidden) with the ISO value the pages read and write.
const TH_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"];
const TH_DAYS = ["อา", "จ", "อ", "พ", "พฤ", "ศ", "ส"];
function parseDmy(textValue) {
  const m = String(textValue).trim().match(/^(\d{1,2})\s*[\/.\-]\s*(\d{1,2})\s*[\/.\-]\s*(\d{4})$/) || String(textValue).trim().match(/^(\d{2})(\d{2})(\d{4})$/);
  if (!m) return null;
  let [d, mo, y] = [Number(m[1]), Number(m[2]), Number(m[3])];
  if (y > 2400) y -= 543;  // Buddhist year
  const date = new Date(y, mo - 1, d);
  return date.getFullYear() === y && date.getMonth() === mo - 1 && date.getDate() === d ? isoDate(date) : null;
}
// Every date on screen is dd/mm/yyyy in the Buddhist year; the API keeps ISO (Christian) dates.
function toDmy(iso) {
  const m = String(iso || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? `${m[3]}/${m[2]}/${Number(m[1]) + 543}` : "";
}
const toThaiDmy = toDmy;
// "2026-10-08 06:01:30" or "2026-10-08T06:01" -> "08/10/2569 06:01:30"; anything else is shown as it is.
function thaiDateTime(value) {
  const textValue = String(value ?? "").replace("T", " ");
  const m = textValue.match(/^(\d{4})-(\d{2})-(\d{2})(.*)$/);
  return m ? `${m[3]}/${m[2]}/${Number(m[1]) + 543}${m[4]}` : textValue;
}
function fromIso(iso) {
  const m = String(iso || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
}

let openCalendar = null;
function closeCalendar() {
  if (!openCalendar) return;
  openCalendar.popup.remove();
  openCalendar.pick.setAttribute("aria-expanded", "false");
  openCalendar = null;
}
document.addEventListener("mousedown", (e) => { if (openCalendar && !openCalendar.wrap.contains(e.target)) closeCalendar(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && openCalendar) { const { box } = openCalendar; closeCalendar(); box.focus(); } });

function showCalendar(wrap, input, box, pick, nativeGet) {
  if (openCalendar && openCalendar.wrap === wrap) { closeCalendar(); return; }
  closeCalendar();
  const popup = el("div", { class: "cal", role: "dialog", "aria-label": "ปฏิทิน" });
  const chosen = fromIso(nativeGet.call(input));
  let view = chosen || new Date();
  view = new Date(view.getFullYear(), view.getMonth(), 1);
  // On a start/end pair, shade the days between the two.
  const form = input.form;
  const other = form && (input.name === "start" ? form.elements.end : input.name === "end" ? form.elements.start : null);
  const choose = (iso) => {
    input.value = iso;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    closeCalendar();
    box.focus();
  };
  const draw = () => {
    const today = isoDate(new Date());
    const value = nativeGet.call(input);
    const otherValue = other ? other.value : "";
    const [lo, hi] = [value, otherValue].filter(Boolean).sort();
    const year = view.getFullYear(), month = view.getMonth();
    const first = new Date(year, month, 1).getDay();
    const days = new Date(year, month + 1, 0).getDate();
    const monthSelect = el("select", { class: "cal-month", "aria-label": "เดือน", onchange: (e) => { view = new Date(year, Number(e.target.value), 1); draw(); } },
      ...TH_MONTHS.map((name, i) => el("option", { value: String(i), selected: i === month }, name)));
    const nowYear = new Date().getFullYear();
    const years = [];
    for (let y = Math.min(year, nowYear) - 5; y <= Math.max(year, nowYear) + 1; y++) years.push(y);
    const yearSelect = el("select", { class: "cal-year", "aria-label": "ปี พ.ศ.", onchange: (e) => { view = new Date(Number(e.target.value), month, 1); draw(); } },
      ...years.map((y) => el("option", { value: String(y), selected: y === year }, `พ.ศ. ${y + 543}`)));
    const cells = [];
    for (let i = 0; i < first; i++) cells.push(el("span", { class: "cal-blank" }));
    for (let d = 1; d <= days; d++) {
      const iso = isoDate(new Date(year, month, d));
      const cls = ["cal-day"];
      if (iso === today) cls.push("today");
      if (iso === value) cls.push("chosen");
      else if (iso === otherValue) cls.push("edge");
      if (lo && hi && lo !== hi && iso > lo && iso < hi) cls.push("between");
      if ((first + d - 1) % 7 === 0 || (first + d - 1) % 7 === 6) cls.push("weekend");
      cells.push(el("button", { type: "button", class: cls.join(" "), "data-iso": iso, "aria-label": `${d} ${TH_MONTHS[month]} ${year + 543}`,
        "aria-pressed": String(iso === value), onclick: () => choose(iso) }, String(d)));
    }
    popup.replaceChildren(
      el("div", { class: "cal-head" },
        el("button", { type: "button", class: "cal-nav", "aria-label": "เดือนก่อน", onclick: () => { view = new Date(year, month - 1, 1); draw(); } }, "‹"),
        monthSelect, yearSelect,
        el("button", { type: "button", class: "cal-nav", "aria-label": "เดือนถัดไป", onclick: () => { view = new Date(year, month + 1, 1); draw(); } }, "›")),
      el("div", { class: "cal-grid cal-week" }, ...TH_DAYS.map((d, i) => el("span", { class: i === 0 || i === 6 ? "weekend" : "" }, d))),
      el("div", { class: "cal-grid" }, ...cells),
      el("div", { class: "cal-foot" },
        el("button", { type: "button", class: "cal-link", onclick: () => choose(today) }, `วันนี้ ${toThaiDmy(today)}`),
        el("button", { type: "button", class: "cal-link muted-link", onclick: () => choose("") }, "ล้าง")));
  };
  draw();
  wrap.append(popup);
  // Keep it on screen: open to the left when there is no room on the right.
  const rect = popup.getBoundingClientRect();
  if (rect.right > window.innerWidth - 8) popup.classList.add("cal-right");
  pick.setAttribute("aria-expanded", "true");
  openCalendar = { wrap, popup, box, pick };
  (popup.querySelector(".cal-day.chosen") || popup.querySelector(".cal-day.today") || popup.querySelector(".cal-day")).focus();
}

function setupDateInputs(root = document) {
  const native = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
  root.querySelectorAll('input[type="date"]:not(.dmy-native)').forEach((input) => {
    const box = el("input", { type: "text", class: "dmy-text", inputMode: "numeric", autocomplete: "off", placeholder: "วว/ดด/ปปปป", title: "วัน/เดือน/ปี พ.ศ." });
    const pick = el("button", { type: "button", class: "dmy-pick", "aria-label": "เลือกจากปฏิทิน", title: "เลือกจากปฏิทิน", "aria-haspopup": "dialog", "aria-expanded": "false" });
    const wrap = el("span", { class: "dmy" });
    input.replaceWith(wrap);
    input.classList.add("dmy-native");
    input.tabIndex = -1;
    input.setAttribute("aria-hidden", "true");
    wrap.append(box, pick, input);
    const show = () => { box.value = toThaiDmy(native.get.call(input)); box.setCustomValidity(""); };
    // Pages set .value directly (presets, defaults); keep the text box in step.
    Object.defineProperty(input, "value", { configurable: true, get() { return native.get.call(this); }, set(v) { native.set.call(this, v); show(); } });
    input.addEventListener("change", show);
    box.addEventListener("input", () => {
      const iso = box.value.trim() ? parseDmy(box.value) : "";
      if (iso === null) { box.setCustomValidity("วันที่ไม่ถูกต้อง ใช้รูปแบบ วว/ดด/ปปปป (พ.ศ.)"); return; }
      box.setCustomValidity("");
      native.set.call(input, iso);
    });
    box.addEventListener("change", () => { if (!box.validationMessage) show(); });
    box.addEventListener("keydown", (e) => { if (e.key === "ArrowDown" && e.altKey) { e.preventDefault(); showCalendar(wrap, input, box, pick, native.get); } });
    pick.onclick = () => showCalendar(wrap, input, box, pick, native.get);
    show();
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

// The e-Claim C code fix (แนวทางแก้ไข), one line per step as in the สปสช. document.
function fixBlock(fix) {
  if (!fix) return null;
  return el("div", { class: "fix-block" }, el("span", { class: "fix-label" }, "แนวทางแก้ไข"),
    ...fix.split("\n").map((line) => el("div", {}, line)));
}

// Empty, error and loading states of a list. The kind picks the icon (CSS draws it) and a next step under the text;
// old calls pass a glyph ("○", "!", "…") and get the matching kind.
const EMPTY_HINTS = {
  filter: "ลองล้างคำค้น หรือกดการ์ด \"ทั้งหมด\" เพื่อดูทุกรายการ",
  none: "ลองขยายช่วงวันที่ หรือเปลี่ยนเงื่อนไขแล้วค้นหาอีกครั้ง",
  error: "กดค้นหาอีกครั้ง ถ้ายังไม่ได้ ดูสถานะการเชื่อมต่อ HOSxP ที่มุมขวาบน",
};
function emptyState(text, kind = "") {
  kind = { "○": "", "!": "error", "…": "loading", "⌕": "search" }[kind] ?? kind;
  if (kind === "loading") return loadingState(text);
  if (!kind) kind = /ตัวกรอง/.test(text) ? "filter" : /ไม่พบ|ไม่มี/.test(text) ? "none" : "search";
  const hint = EMPTY_HINTS[kind];
  return [el("div", { class: "empty-icon", "data-icon": kind, "aria-hidden": "true" }),
    el("div", { class: "empty-title" }, text), hint ? el("div", { class: "empty-hint" }, hint) : null].filter(Boolean);
}

// A skeleton shaped like table rows while a list loads, with the text read out to screen readers.
function loadingState(text = "กำลังโหลด…", rows = 6) {
  const bars = Array.from({ length: rows }, (_, i) => el("div", { class: "skeleton-row" },
    el("span", { class: "skeleton-bar w1" }), el("span", { class: "skeleton-bar w2" }),
    el("span", { class: "skeleton-bar w3" }), el("span", { class: `skeleton-bar w${(i % 3) + 1}` })));
  return [el("div", { class: "skeleton", "aria-hidden": "true" }, ...bars),
    el("div", { class: "empty-title skeleton-text", role: "status" }, text)];
}

// Clear the table (tbody#rows) and show the skeleton in #empty until render() runs.
function showLoading(text = "กำลังค้นหา…") {
  const rows = $("rows"), empty = $("empty");
  if (rows) rows.replaceChildren();
  if (empty) { empty.hidden = false; empty.replaceChildren(...loadingState(text)); }
}

// A few skeleton lines inside a drawer or panel while its details load.
function loadingBlock(text = "กำลังโหลด…") {
  return el("div", { class: "skeleton-block", role: "status" },
    el("span", { class: "skeleton-bar w1" }), el("span", { class: "skeleton-bar w3" }), el("span", { class: "skeleton-bar w2" }),
    el("span", { class: "sr-only" }, text));
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
setupDateInputs();
showUser();
// The drop-down menus (SSO, KHH LINK) close on a click elsewhere, Escape, or after a link is opened; one is open at a time.
document.querySelectorAll(".menu .menu-links").forEach((menu) => {
  document.addEventListener("click", (e) => { if (menu.open && !menu.contains(e.target)) menu.open = false; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && menu.open) { menu.open = false; menu.querySelector("summary").focus(); } });
  menu.querySelectorAll("a").forEach((a) => a.addEventListener("click", () => { menu.open = false; }));
});

// ------------------------------------------------- keyboard: rows and the drawer
document.addEventListener("keydown", (e) => {
  const row = e.target;
  if (row instanceof HTMLTableRowElement && row.classList.contains("row-link") && (e.key === "Enter" || e.key === " ")) {
    e.preventDefault(); row.click();
  }
});
// When the drawer opens, focus moves into it and Tab stays inside; when it closes, focus goes back to the row
// it came from (found again by position, as the table is drawn anew on open and close).
(() => {
  const drawer = $("drawer");
  if (!drawer) return;
  let back = null;
  const rowPlace = (node) => {
    const row = node?.closest?.("tr.row-link");
    const body = row?.parentElement;
    return row && body?.id ? { body: body.id, index: [...body.rows].indexOf(row) } : null;
  };
  document.addEventListener("pointerdown", (e) => { if (drawer.hidden) back = rowPlace(e.target); }, true);
  document.addEventListener("keydown", (e) => { if (drawer.hidden && (e.key === "Enter" || e.key === " ")) back = rowPlace(e.target); }, true);
  new MutationObserver(() => {
    if (!drawer.hidden) {
      if (!drawer.contains(document.activeElement)) $("drawer-close")?.focus({ preventScroll: true });
      return;
    }
    const place = back; back = null;
    const row = place && $(place.body)?.rows[place.index];
    if (row && (!document.activeElement || document.activeElement === document.body || drawer.contains(document.activeElement))) row.focus();
  }).observe(drawer, { attributes: true, attributeFilter: ["hidden"] });
  drawer.setAttribute("role", "dialog");
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !drawer.hidden && !document.querySelector("dialog[open]")) $("drawer-close")?.click();
  });
  drawer.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const items = [...drawer.querySelectorAll("button, a[href], input, select, textarea, summary, [tabindex]:not([tabindex='-1'])")]
      .filter((n) => !n.disabled && n.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
})();

// ------------------------------------------- footer send panels (FDH / New e-Claim)
// One open at a time; they close on a click outside (not while a confirm dialog is up), or Escape.
document.querySelectorAll(".fdh-pop").forEach((pop) => {
  const zone = pop.querySelector("select[id$='-zone']"), tag = pop.querySelector("[data-zone-tag]");
  const mark = () => {
    const prod = zone?.value === "prod";
    pop.classList.toggle("prod", prod);
    if (tag) tag.textContent = prod ? "PROD ส่งจริง" : "UAT ทดสอบ";
  };
  zone?.addEventListener("change", mark); mark();
  pop.addEventListener("toggle", () => {
    if (pop.open) document.querySelectorAll(".fdh-pop[open]").forEach((other) => { if (other !== pop) other.open = false; });
  });
  document.addEventListener("click", (e) => {
    if (pop.open && !pop.contains(e.target) && !e.target.closest("dialog") && !document.querySelector("dialog[open]")) pop.open = false;
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && pop.open && !document.querySelector("dialog[open]")) { pop.open = false; pop.querySelector("summary").focus(); }
  });
});

// ------------------------------------------------ main menu on narrow screens
// Below 900px the menu folds behind one button that names the current page; it closes after a link or on Escape.
(() => {
  const toggle = $("menu-toggle"), top = toggle?.closest(".top");
  if (!toggle) return;
  const current = document.querySelector("#main-menu a[aria-current=page]");
  if (current) toggle.querySelector(".menu-toggle-text").textContent = current.textContent.replace("↗", "").trim();
  const set = (open) => { top.classList.toggle("menu-open", open); toggle.setAttribute("aria-expanded", String(open)); };
  toggle.onclick = () => set(!top.classList.contains("menu-open"));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && top.classList.contains("menu-open")) { set(false); toggle.focus(); } });
  document.addEventListener("click", (e) => { if (top.classList.contains("menu-open") && !top.contains(e.target)) set(false); });
  $("main-menu").querySelectorAll("a").forEach((a) => a.addEventListener("click", () => set(false)));
})();
