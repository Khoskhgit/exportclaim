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
  // A category may bring its own amount (e.g. the paid card sums what the STM paid, not what was claimed).
  container.replaceChildren(...Object.entries(cats).map(([key, [label, test, tone, ownAmount]]) => {
    const hits = rows.filter(test);
    const amount = hits.reduce((sum, r) => sum + Number((ownAmount || amountOf)(r) || 0), 0);
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
setupDateInputs();
showUser();
