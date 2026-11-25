"use strict";
// NTIP: the Z11.1 screening visits (exported for NTIP Plus) and the TB labs checked against the payment rules.
const state = { view: "screen", period: null, screen: [], labs: [], filter: "all", text: "", opts: null, current: null, selected: new Set(),
  sort: { key: "", dir: "asc" }, loaded: { screen: false, labs: false } };

// A screening row already put in an NTIP Plus file shows "ส่งแล้ว" (unless it now has an Error); EXPORTED is that
// fact, not a problem, so it is left out of the warnings shown.
const otherWarnings = (r) => r.warnings.filter((w) => w.code !== "EXPORTED");
const status = (r) => (r.errors.length ? "error" : r.exported ? "sent" : otherWarnings(r).length ? "warn" : "ok");
const STATUS_BADGE = { error: ["b-error", "Error"], sent: ["b-sent", "ส่งแล้ว"], warn: ["b-warn", "เตือน"], ok: ["b-ok", "พร้อม"] };
const problems = (r) => [...r.errors, ...otherWarnings(r)].map((p) => p.message).join(" · ");
const dash = () => el("span", { class: "muted" }, "—");

const CATS = {
  screen: () => ({
    all: ["ทั้งหมด", () => true, "all"],
    ok: ["พร้อมส่งออก (ยังไม่ส่ง)", (r) => !r.errors.length && !r.exported, "ok"],
    warn: ["มีคำเตือน", (r) => !r.errors.length && !r.exported && otherWarnings(r).length > 0, "warn"],
    error: ["Error", (r) => r.errors.length > 0, "error"],
    sent: ["ส่งแล้ว", (r) => !!r.exported, "sent"],
  }),
  labs: () => ({
    all: ["ทั้งหมด", () => true, "all"],
    ok: ["เบิกได้", (r) => r.claimable, "ok"],
    error: ["เบิกไม่ได้ (เกินสิทธิ)", (r) => !r.claimable, "error"],
    molecular: ["Molecular", (r) => r.group === "molecular", "sent"],
    afb: ["AFB", (r) => r.group === "afb", "sent"],
    culture: ["Culture", (r) => r.group === "culture", "sent"],
  }),
};

// ----------------------------------------------------------- selects saved per visit
function pick(r, name, list, opts = {}) {
  const value = r.picked[name] ?? "";
  const select = el("select", { class: `ntip-pick${value !== (r.guess[name] ?? "") ? " changed" : ""}`, "aria-label": `${opts.label || name} ของ VN ${r.vn}`,
    onclick: (e) => e.stopPropagation(), onchange: (e) => saveChoice(r.vn, name, e.target.value) },
    ...(opts.blank !== undefined ? [el("option", { value: "" }, opts.blank)] : []),
    ...list.map((o) => el("option", { value: o.code, selected: o.code === value }, opts.codes ? `${o.code} ${o.name}` : o.name)));
  return select;
}

async function saveChoice(vn, name, value, reset = false) {
  try {
    await api("/api/ntip/choice", reset ? { vn, name, reset: true } : { vn, name, value });
    await load(true);
    if (state.current === vn) { const r = state.screen.find((x) => x.vn === vn); if (r) openScreen(r); }
  } catch (err) { notice(err.message); }
}

async function saveTechnique(key, technique) {
  try { await api("/api/ntip/lab-choice", { key, technique }); await load(true); } catch (err) { notice(err.message); }
}

// ------------------------------------------------------------------ table
const HEADS = {
  screen: [["", "", "check"], ["ผลตรวจ", ""], ["วันที่", "date"], ["VN", "vn"], ["HN", "hn"], ["ชื่อ", "ptname"], ["สิทธิ", "pttype"], ["กลุ่มเสี่ยง", ""],
    ["ซักประวัติ", ""], ["CXR", ""], ["ผลพิจารณา (DX)", ""], ["ครั้งที่", "nth"], ["ปัญหาที่พบ", ""]],
  labs: [["ผลตรวจ", ""], ["วันที่", "date"], ["VN / AN", "vn"], ["HN", "hn"], ["ชื่อ", "ptname"], ["สิทธิ", "pttype"], ["รายการ LAB", "name"],
    ["ผล", "result"], ["วิธี / อัตรา", ""], ["ครั้งที่ในปีงบ", "nth"], ["ปัญหาที่พบ", ""]],
};

// A row can be picked for the NTIP Plus file when it has no Error; the header box picks every such row shown.
const pickable = (r) => !r.errors.length;
function selectAll() {
  // Ticking it takes the rows not sent yet; a sent row is only sent again when ticked on its own.
  return el("input", { type: "checkbox", id: "select-all", "aria-label": "เลือกทั้งหมดในหน้านี้", title: "เลือกทั้งหมดที่แสดงอยู่ ส่งออกได้ และยังไม่ส่ง",
    onchange: (e) => {
      for (const r of visible().filter((x) => pickable(x) && (!x.exported || !e.target.checked))) e.target.checked ? state.selected.add(r.vn) : state.selected.delete(r.vn);
      render();
    } });
}
function head() {
  $("thead").replaceChildren(el("tr", {}, ...HEADS[state.view].map(([label, key, cls]) =>
    cls === "check" ? el("th", { class: "check" }, selectAll()) : el("th", key ? { "data-sort": key } : {}, label))));
  bindSort($("thead"), state.sort, render);
}

function screenRow(r) {
  const o = state.opts;
  const [cls, label] = STATUS_BADGE[status(r)];
  const cxr = el("span", { class: "parts" }, pick(r, "cxr_result", o.cxr_results, { blank: "ไม่ได้ถ่าย", label: "ผล CXR" }),
    r.picked.cxr_result === "02" ? pick(r, "cxr_abnormal", o.cxr_abnormal, { blank: "เลือกผล Abnormal", label: "ผล Abnormal" }) : null,
    r.cxr.date ? el("span", { class: "sub" }, `${r.cxr.name.trim()} ${toDmy(r.cxr.date)}`) : null);
  const box = el("input", { type: "checkbox", checked: state.selected.has(r.vn), disabled: !pickable(r),
    title: pickable(r) ? "เลือกส่งออก" : "มี Error ส่งออกไม่ได้", "aria-label": `เลือก VN ${r.vn}`, onclick: (e) => e.stopPropagation(),
    onchange: (e) => { e.target.checked ? state.selected.add(r.vn) : state.selected.delete(r.vn); render(); } });
  return el("tr", { class: state.current === r.vn ? "current" : "", onclick: () => openScreen(r) },
    el("td", { class: "check" }, box),
    el("td", {}, el("span", { class: `badge ${cls}`, title: r.exported ? `ส่งออกไฟล์ NTIP Plus เมื่อ ${thaiDateTime(r.exported)}` : "" }, label),
      r.exported ? el("span", { class: "sub" }, toDmy(r.exported.slice(0, 10))) : null),
    el("td", { class: "mono" }, toDmy(r.date)), el("td", { class: "mono" }, r.vn), el("td", { class: "mono" }, r.hn),
    el("td", { class: "name-cell", title: r.ptname }, r.ptname), el("td", { class: "name-cell", title: r.pttype }, r.pttype),
    el("td", {}, pick(r, "risk", o.risks, { blank: "— เลือกกลุ่มเสี่ยง —", codes: true, label: "กลุ่มเสี่ยง" })),
    el("td", {}, pick(r, "symptom", o.symptoms, { label: "ซักประวัติ" })), el("td", {}, cxr),
    el("td", {}, pick(r, "dx", o.dx, { label: "ผลพิจารณา" })),
    el("td", { class: "num" }, String(r.nth)), el("td", { class: "problems" }, problems(r) || dash()));
}

function labRow(r) {
  const [cls, label] = r.claimable ? (r.errors.length ? STATUS_BADGE.error : r.warnings.length ? STATUS_BADGE.warn : STATUS_BADGE.ok) : ["b-error", "เบิกไม่ได้"];
  const method = r.group === "culture"
    ? el("span", { class: "parts" }, el("select", { class: "ntip-pick", "aria-label": `เทคนิค Culture ${r.key}`, onclick: (e) => e.stopPropagation(),
      onchange: (e) => saveTechnique(r.key, e.target.value) }, el("option", { value: "" }, "— เลือกเทคนิค —"),
      ...state.opts.culture.map((c) => el("option", { value: c.code, selected: c.code === r.technique }, c.name))),
      el("span", { class: "sub" }, r.rate ? `${baht.format(Number(r.rate))} บาท` : "ยังไม่รู้อัตรา"))
    : el("span", {}, r.label, el("span", { class: "sub" }, `${r.group_label} · ${baht.format(Number(r.rate))} บาท`));
  return el("tr", {},
    el("td", {}, el("span", { class: `badge ${cls}` }, label)), el("td", { class: "mono" }, toDmy(r.date)),
    el("td", { class: "mono" }, r.vn || "—", r.kind === "IPD" ? el("span", { class: "sub" }, "AN ผู้ป่วยใน") : null), el("td", { class: "mono" }, r.hn), el("td", { class: "name-cell", title: r.ptname }, r.ptname),
    el("td", { class: "name-cell", title: r.pttype }, r.pttype || "—"), el("td", {}, r.name, el("span", { class: "sub" }, `lab ${r.code}`)),
    el("td", {}, r.result || dash()), el("td", {}, method), el("td", { class: "num" }, String(r.nth)),
    el("td", { class: "problems" }, problems(r) || dash()));
}

function visible() {
  const q = state.text.toLowerCase();
  const test = (CATS[state.view]()[state.filter] || CATS[state.view]().all)[1];
  const list = state[state.view].filter((r) => test(r) && (!q || `${r.vn} ${r.hn} ${r.ptname}`.toLowerCase().includes(q)));
  return sortRows(list, state.sort, ["nth"]);
}

function render() {
  const rows = state[state.view];
  const amount = state.view === "labs" ? (r) => (r.claimable ? r.rate : 0) : () => 0;
  renderCards($("summary"), CATS[state.view](), rows, state.filter, amount, (key) => { state.filter = key; render(); }, "รายการ");
  if (state.view === "screen") $("summary").querySelectorAll(".card-amount").forEach((x) => { x.hidden = true; });
  showSort($("thead"), state.sort);
  const list = visible();
  $("rows").replaceChildren(...list.map(state.view === "screen" ? screenRow : labRow));
  $("empty").hidden = list.length > 0;
  if (!list.length && state.loaded[state.view]) $("empty").replaceChildren(...emptyState(rows.length ? "ไม่มีรายการตามตัวกรองนี้" : "ไม่พบรายการในช่วงวันที่นี้"));
  $("result-count").textContent = state.loaded[state.view] ? `แสดง ${count.format(list.length)} จาก ${count.format(rows.length)} รายการ` : "";
  const picked = state.screen.filter((r) => state.selected.has(r.vn) && pickable(r)).length;
  $("excel").textContent = state.view === "screen" ? `ส่งออก NTIP Plus (เลือก ${count.format(picked)})` : "ส่งออก Excel ตรวจ Lab";
  const why = !state.loaded[state.view] ? "ค้นหาก่อน แล้วจึงส่งออก" : state.view === "screen" && !picked ? "เลือกรายการในตารางก่อน" : "";
  $("excel").disabled = Boolean(why);
  $("excel").title = why;
  const all = $("select-all");
  if (all) {
    const shown = list.filter((r) => pickable(r) && !r.exported);
    const on = shown.filter((r) => state.selected.has(r.vn)).length;
    all.checked = shown.length > 0 && on === shown.length;
    all.indeterminate = on > 0 && on < shown.length;
    all.disabled = !shown.length;
  }
  for (const v of ["screen", "labs"]) {
    const badge = $(`badge-${v}`);
    badge.hidden = !state.loaded[v];
    badge.textContent = String(state[v].length);
  }
}

function switchView(view) {
  state.view = view; state.filter = "all"; state.sort = { key: "", dir: "asc" };
  document.querySelectorAll(".ntip-views .tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === view)));
  $("rules-screen").hidden = view !== "screen";
  $("rules-labs").hidden = view !== "labs";
  $("rules-open").textContent = view === "labs" ? "เงื่อนไข Lab TB" : "กลุ่มเสี่ยง TB";
  $("rules-title").textContent = view === "labs" ? "Lab วัณโรค · เงื่อนไขการเบิก" : "กลุ่มเสี่ยง TB และเงื่อนไขการคัดกรอง";
  head();
  render();
  if (state.period && !state.loaded[view]) load();
}

// ------------------------------------------------------------------ drawer
function table(headers, rows) {
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", {}, h)))), el("tbody", {}, ...rows)));
}

function field(r, name, label, attrs = {}) {
  const input = el("input", { value: r.picked[name] || "", ...attrs });
  input.onchange = () => saveChoice(r.vn, name, input.value.trim());
  input.onkeydown = (e) => { if (e.key === "Enter") input.blur(); };
  return el("label", {}, label, input);
}

async function openScreen(r) {
  state.current = r.vn; render();
  $("drawer").hidden = false; $("drawer-backdrop").hidden = false;
  $("drawer-title").textContent = r.ptname || r.vn;
  $("drawer-sub").textContent = `VN ${r.vn} · HN ${r.hn} · ${toDmy(r.date)} · คัดกรองครั้งที่ ${r.nth} ในปีงบ`;
  const v = r.values;
  const risk = state.opts.risks.find((x) => x.code === r.picked.risk);
  const info = el("dl", { class: "info-grid" }, ...[
    ["กลุ่มเสี่ยง", risk ? `${risk.code} ${risk.name}${r.picked.risk === r.guess.risk ? " (เดาจากข้อมูล HOSxP)" : " (เลือกเอง)"}` : "ยังไม่ได้เลือก"],
    ["เลขบัตร", r.cid], ["สิทธิ (INSCL_ID)", v.INSCL_ID || "—"], ["เพศ / วันเกิด", `${v.GENDER || "—"} · ${v.BORN || "—"} (${v.AGE_Y} ปี ${v.AGE_M} เดือน)`],
    ["ที่อยู่ (รหัส)", `หมู่ ${v.MU || "—"} · ${v.TAMBOL_ID || "—"}`], ["ประชากร / สัญชาติ", `${v.PEOPLE_TYPE} · ${v.RACE_ID}`],
    ["CXR", r.cxr.date ? `${r.cxr.name.trim()} ${toDmy(r.cxr.date)}` : "ไม่พบใน visit"], ["ส่งออก", r.exported ? thaiDateTime(r.exported) : "ยังไม่ส่งออก"],
  ].map(([k, val]) => el("div", {}, el("dt", {}, k), el("dd", {}, val))),
    el("div", { class: "info-wide" }, el("dt", {}, "CC (อาการสำคัญ)"), el("dd", { class: "cc-text" }, r.cc || "ไม่ได้บันทึก")));
  const extra = el("div", { class: "ntip-form" },
    field(r, "index_cid", "เลขบัตร INDEX (ผู้สัมผัส)", { inputmode: "numeric", maxLength: 13, placeholder: "13 หลัก" }),
    field(r, "icd10", "ICD-10 (เบาหวาน/สุรา)", { maxLength: 6, placeholder: "เช่น E119" }),
    field(r, "hba1c", "HbA1C", { inputmode: "decimal", placeholder: r.guess.hba1c ? `จาก lab ${r.guess.hba1c}` : "เช่น 8.5" }),
    field(r, "cd4", "CD4 (B24)", { inputmode: "numeric", placeholder: r.guess.cd4 ? `จาก lab ${r.guess.cd4}` : "จำนวนเต็ม" }),
    field(r, "immuno", "โรคที่ได้รับยากดภูมิคุ้มกัน", { maxLength: 50 }),
    field(r, "prison_hosp", "รหัสหน่วยบริการเรือนจำ", { inputmode: "numeric", maxLength: 5 }),
    field(r, "prison_start", "วันที่เข้าเรือนจำ", { type: "date" }), field(r, "prison_end", "วันที่พ้นโทษ", { type: "date" }));
  const check = [info,
    r.errors.length || r.warnings.length ? el("ul", { class: "service-rules" }, ...[...r.errors, ...r.warnings].map((p) => el("li", {}, p.message))) : null,
    el("h3", {}, "ข้อมูลเพิ่มตามกลุ่มเสี่ยง"),
    el("p", { class: "muted-note" }, "ใส่เฉพาะช่องที่กลุ่มเสี่ยงต้องใช้ ระบบบันทึกเมื่อออกจากช่องหรือกด Enter (ไฟล์ส่งเฉพาะช่องของกลุ่มเสี่ยงที่เลือก)"), extra,
    el("h3", {}, "ค่าที่จะส่งในไฟล์"),
    table(["คอลัมน์", "ค่า"], Object.entries(v).map(([k, val]) => el("tr", {}, el("td", { class: "mono" }, k), el("td", {}, val || "—"))))].filter(Boolean);
  const body = $("drawer-body");
  body.replaceChildren(...tabs([{ id: "check", label: "ข้อมูลส่งออก", content: check }, { id: "dx", label: "วินิจฉัย / Lab", content: [loadingBlock()] }]));
  try {
    const data = await api("/api/ntip/detail", { vn: r.vn });
    if (state.current !== r.vn) return;
    const panel = body.querySelector("#tab-dx");
    panel.replaceChildren(el("h3", {}, "การวินิจฉัย"),
      data.dx.length ? table(["ประเภท", "รหัส", "ชื่อโรค"], data.dx.map((d) => el("tr", {}, el("td", {}, d.type), el("td", { class: "mono" }, d.code), el("td", {}, d.name)))) : el("p", { class: "muted" }, "ไม่มี"),
      el("h3", {}, "CXR"), r.cxr.report ? el("p", { class: "cc-text" }, r.cxr.report) : el("p", { class: "muted" }, r.cxr.date ? "ไม่มีข้อความรายงานใน HOSxP" : "ไม่พบการถ่าย Chest"),
      el("h3", {}, "Lab วัณโรคในปีงบนี้"),
      data.labs.length ? table(["วันที่", "รายการ", "ผล", "ครั้งที่", "เบิก"], data.labs.map((x) => el("tr", {},
        el("td", { class: "mono" }, toDmy(x.date)), el("td", {}, x.name, el("span", { class: "sub" }, x.label)), el("td", {}, x.result || "—"),
        el("td", { class: "num" }, String(x.nth)), el("td", {}, x.claimable ? "ได้" : "ไม่ได้")))) : el("p", { class: "muted" }, "ไม่มี"));
  } catch (err) { notice(err.message); }
}
function closeDrawer() { $("drawer").hidden = true; $("drawer-backdrop").hidden = true; state.current = null; render(); }

// ------------------------------------------------------------------ load / export
function period() {
  const form = $("search");
  return { start: form.elements.start.value, end: form.elements.end.value };
}

async function load(quiet = false) {
  const body = quiet && state.period ? state.period : period();
  if (!body.start || !body.end) { notice("กรุณาเลือกช่วงวันที่"); return; }
  if (dbBlocked()) return;
  const button = $("search").querySelector("button[type=submit]");
  button.disabled = true;
  try {
    if (!state.opts) state.opts = await api("/api/ntip/options");
    if (!quiet || JSON.stringify(body) !== JSON.stringify(state.period)) {
      state.loaded = { screen: false, labs: false };
      state.selected.clear();
      if (!quiet) state.filter = "all";
    }
    state.period = body;
    const view = state.view;
    if (!quiet) showLoading("กำลังค้นหา…");
    const data = await api(view === "screen" ? "/api/ntip/screen" : "/api/ntip/labs", body);
    state[view] = data.rows;
    state.loaded[view] = true;
    render();
  } catch (err) { notice(err.message); } finally { button.disabled = false; }
}

async function exportFile() {
  const button = $("excel");
  const screen = state.view === "screen";
  const picked = state.screen.filter((r) => state.selected.has(r.vn) && pickable(r));
  if (screen) {
    const again = picked.filter((r) => r.exported).length;
    if (again && !(await confirmDialog("ส่งออกไฟล์ NTIP Plus", [
      el("p", {}, `ส่งออก ${count.format(picked.length)} รายการที่เลือก`),
      el("p", {}, `มี ${count.format(again)} รายการที่เคยส่งออกแล้ว จะอยู่ในไฟล์ด้วย`)], "ส่งออก"))) return;
  }
  button.disabled = true;
  try {
    const response = await fetch(screen ? "/api/ntip/export" : "/api/ntip/labs/excel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(screen ? { ...state.period, vns: picked.map((r) => r.vn) } : state.period) });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      if (response.status === 401 && data.login) { window.location.assign("/login?error=expired"); return; }
      throw new Error(data.detail || `ผิดพลาด (${response.status})`);
    }
    const blob = await response.blob();
    const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "")?.[1] || "NTIP.xlsx";
    const link = el("a", { href: URL.createObjectURL(blob), download: name });
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    if (screen) { notice(`ส่งออก ${name} แล้ว (${count.format(picked.length)} รายการ)`, "ok"); state.selected.clear(); await load(true); }
  } catch (err) { notice(err.message); } finally { render(); }
}

async function connection() {
  try {
    const s = await api("/api/status");
    if (s.database) setConnection(`HOSxP · ${s.db_host}`, true);
    else setConnection(s.db_error ? `เชื่อมต่อไม่ได้ (${s.db_host})` : "ยังไม่ได้ตั้งค่าฐานข้อมูล", false);
  } catch (err) { setConnection("เชื่อมต่อไม่ได้", false); }
}

$("search").addEventListener("submit", (e) => { e.preventDefault(); clearNotices(); load(); });
$("fiscal").onclick = () => {
  const today = new Date();
  const year = today.getMonth() >= 9 ? today.getFullYear() : today.getFullYear() - 1;
  $("search").elements.start.value = `${year}-10-01`;
  $("search").elements.end.value = isoDate(today);
};
document.querySelectorAll(".ntip-views .tab").forEach((b) => { b.onclick = () => switchView(b.dataset.view); });
$("excel").onclick = exportFile;
$("drawer-close").onclick = closeDrawer;
$("drawer-backdrop").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden) closeDrawer(); });
$("filter").addEventListener("input", (e) => { state.text = e.target.value; render(); });
setupPresets($("search"));
$("search").querySelector('[data-preset="last"]').click();
head();
render();
connection();

// The rules for the open tab live in a popup behind the "กลุ่มเสี่ยง TB" button.
$("rules-open").onclick = () => $("rules").showModal();
$("rules-close").onclick = () => $("rules").close();
$("rules").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });
