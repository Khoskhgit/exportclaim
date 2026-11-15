"use strict";
// รายการ PPFS: pick a service type, check the visits, pick items into the list, build the 18-file ZIP for e-Claim.
const state = { rows: [], services: [], selected: new Set(), filter: "all", text: "", current: null, sort: { key: "date", dir: "asc" } };
const money = (v) => baht.format(Number(v || 0));
const dash = () => el("span", { class: "muted" }, "—");

const CATEGORIES = {
  all: ["ทั้งหมด", () => true, "all"],
  ready: ["พร้อมส่ง", (r) => !r.errors.length && !r.warnings.length && !r.sent, "ok"],
  warn: ["มีคำเตือน", (r) => !r.errors.length && r.warnings.length > 0, "warn"],
  error: ["Error", (r) => r.errors.length > 0, "error"],
  sent: ["ส่งแล้ว", (r) => Boolean(r.sent), "sent"],
};
const selectable = (r) => !r.errors.length;

// Columns that differ by service: [header, sort key, numeric, cell].
const uptBadge = (v) => !v ? dash() : el("span", { class: `badge ${/pos/i.test(v) ? "b-warn" : "b-ok"}` }, v);
const COLUMNS = {
  postnatal: [
    ["วันคลอด", "labor_date", false, (r) => (r.labor_date ? toDmy(r.labor_date) : dash())],
    ["หลังคลอด (วัน)", "days", true, (r) => (r.days === null ? dash() : String(r.days))],
  ],
  pregtest: [
    ["อายุ (ปี)", "age", true, (r) => (r.extra.age === null || r.extra.age === undefined ? dash() : String(r.extra.age))],
    ["ผล UPT", "upt", false, (r) => uptBadge(r.extra.upt)],
    ["Z32", "z32", false, (r) => (r.extra.z32 ? "✓" : dash())],
  ],
  fp: [
    ["อายุ (ปี)", "age", true, (r) => (r.extra.age === null || r.extra.age === undefined ? dash() : String(r.extra.age))],
    ["จำนวน", "fee_qty", true, (r) => `${r.extra.fee_qty} ${r.extra.unit}`],
    ["ยาที่จ่าย", "drug_qty", false, (r) => (r.extra.drug_name ? el("span", {}, `${r.extra.drug_qty}`, el("span", { class: "sub" }, r.extra.drug_name)) : dash())],
  ],
  ncd: [
    ["อายุ (ปี)", "age", true, (r) => (r.extra.age === null || r.extra.age === undefined ? dash() : String(r.extra.age))],
    ["ผล lab", "", false, (r) => labText(r.extra.labs)],
    ["ประวัติการรักษา", "", false, (r) => historyText(r.extra.history, r.date)],
    ["เบิกครั้งก่อน", "last", false, (r) => (r.extra.last ? el("span", {}, toDmy(r.extra.last), el("span", { class: "sub" }, `VN ${r.extra.last_vn}`)) : dash())],
  ],
  anemia: [
    ["อายุ (ปี)", "age", true, (r) => (r.extra.age === null || r.extra.age === undefined ? dash() : String(r.extra.age))],
    ["ผล Hb / Hct", "", false, (r) => labText(r.extra.labs)],
    ["เคยคัดกรองแล้ว", "first", false, (r) => (r.extra.first ? el("span", {}, toDmy(r.extra.first), el("span", { class: "sub" }, `VN ${r.extra.first_vn}`)) : dash())],
  ],
  iron: [
    ["อายุ (ปี)", "age", true, (r) => (r.extra.age === null || r.extra.age === undefined ? dash() : String(r.extra.age))],
    ["ยาที่จ่าย (เม็ด)", "drug_qty", false, (r) => (r.extra.drug_name ? el("span", {}, r.extra.drug_qty, el("span", { class: "sub" }, r.extra.drug_name)) : dash())],
    ["เบิกในปีงบนี้แล้ว", "first", false, (r) => (r.extra.first ? el("span", {}, toDmy(r.extra.first), el("span", { class: "sub" }, `VN ${r.extra.first_vn}`)) : dash())],
  ],
  fluoride: [
    ["อายุ (ปี)", "age", true, (r) => (r.extra.age === null || r.extra.age === undefined ? dash() : String(r.extra.age))],
    ["หัตถการทันตกรรม", "", false, (r) => (r.extra.dental.length ? el("span", {}, ...r.extra.dental.map((d) => el("span", { class: "sub" }, d))) : el("span", { class: "badge b-warn" }, "ไม่พบ"))],
  ],
  ironchild: [
    ["อายุ (เดือน)", "months", true, (r) => (r.extra.months === null || r.extra.months === undefined ? dash() : String(r.extra.months))],
    ["ยาที่จ่าย", "", false, (r) => (r.extra.drug_name ? el("span", { class: "sub" }, r.extra.drug_name) : dash())],
    ["เบิกในปีงบนี้แล้ว", "first", false, (r) => (r.extra.first ? el("span", {}, toDmy(r.extra.first), el("span", { class: "sub" }, `VN ${r.extra.first_vn}`)) : dash())],
    ["ADP ที่ส่ง", "", false, (r) => (r.extra.adp ? el("span", { class: "mono" }, r.extra.adp) : el("span", { class: "badge b-error" }, "ยังไม่ตั้ง"))],
  ],
};
const COUNT_LABEL = { postnatal: "ครั้งที่", pregtest: "ครั้งที่ในปีงบ", fp: "สะสมในปีงบ", ncd: "ครั้งที่", anemia: "ครั้งที่", iron: "ครั้งที่ในปีงบ", fluoride: "ครั้งที่ในปีงบ", ironchild: "ครั้งที่ในปีงบ" };
function labText(labs) {
  const parts = Object.entries(labs || {});
  if (!parts.length) return dash();
  return el("span", {}, ...parts.map(([k, v]) => el("span", { class: "lab-pair" }, `${k} `, v ? el("strong", {}, v) : el("span", { class: "badge b-warn" }, "ไม่มี"))));
}
// Treatment that began before the screening day (the reason a screening may not be claimed).
function historyText(h, day) {
  const before = [["เบาหวาน", h.dm], ["ไขมัน E78", h.lipid], ["ยาลดไขมัน", h.statin]].filter(([, d]) => d && d <= day);
  return before.length ? el("span", {}, ...before.map(([k, d]) => el("span", { class: "sub" }, `${k} ${toDmy(d)}`))) : dash();
}
const countText = (r) => (!r.count ? dash() : service() === "fluoride" ? `${r.count} / 2` : service() === "fp" && r.extra.cap ? `${r.extra.year_total} / ${r.extra.cap} ${r.extra.cap_unit}` : String(r.count));
const service = () => $("service").value;

function renderHead() {
  const th = (label, key, num) => el("th", { class: num ? "num" : "", ...(key ? { "data-sort": key } : {}) }, label);
  $("thead").replaceChildren(el("tr", {}, el("th", { class: "check" }, el("span", { class: "sr-only" }, "เลือก")),
    th("ผลตรวจ", "rank"), th("วันที่", "date"), th("VN", "vn"), th("HN", "hn"), th("ชื่อ", "ptname"),
    ...(COLUMNS[service()] || []).map(([label, key, num]) => th(label, key, num)),
    th("รายการ", "code"), th(COUNT_LABEL[service()] || "ครั้งที่", "count", true), th("ยอด", "amount", true), th("ปัญหาที่พบ")));
  bindSort($("thead"), state.sort, render);
}
const rank = (r) => (r.errors.length ? 2 : r.warnings.length ? 1 : 0);

function resultBadge(r) {
  if (r.errors.length) return el("span", { class: "badge b-error" }, "Error");
  if (r.warnings.length) return el("span", { class: "badge b-warn" }, "เตือน");
  return el("span", { class: "badge b-ok" }, "ผ่าน");
}
function itemLabel(r) {
  return el("span", {}, el("strong", { class: "mono" }, r.code), ` ${r.code_name}`,
    el("span", { class: "sub" }, r.window_label ? `ช่วงวันที่ ${r.window_label}` : r.item_name));
}

function visible() {
  const q = state.text.toLowerCase();
  const test = CATEGORIES[state.filter][1];
  const list = state.rows.filter((r) => test(r) && (!q || `${r.ptname} ${r.vn} ${r.hn}`.toLowerCase().includes(q)));
  return sortRows(list.map((r) => Object.assign(r, r.extra, { rank: rank(r) })), state.sort, ["amount", "rank", "days", "count", "age", "fee_qty", "months"]);
}

function render() {
  renderCards($("summary"), CATEGORIES, state.rows, state.filter, (r) => r.amount, (key) => { state.filter = key; render(); }, "รายการ");
  const list = visible();
  showSort($("thead"), state.sort);
  $("rows").replaceChildren(...list.map((r) => {
    const box = el("input", { type: "checkbox", checked: state.selected.has(r.key), disabled: !selectable(r),
      title: selectable(r) ? "เลือกส่ง" : "รายการนี้ส่งไม่ได้", "aria-label": `เลือก ${r.vn} ${r.code}`,
      onclick: (e) => e.stopPropagation(), onchange: (e) => { toggle(r.key, e.target.checked); } });
    const problems = [...r.errors, ...r.warnings].map((i) => i.message);
    const cls = [state.selected.has(r.key) ? "picked" : "", state.current === r.key ? "current" : ""].join(" ").trim();
    return el("tr", { class: cls, onclick: () => openDrawer(r), title: "คลิกเพื่อดูรายละเอียด" },
      el("td", { class: "check", onclick: (e) => e.stopPropagation() }, box),
      el("td", {}, resultBadge(r), r.sent ? el("span", { class: "badge b-sent sent-tag", title: `ส่งออกเมื่อ ${thaiDateTime(r.sent)}` }, "ส่งแล้ว") : null),
      el("td", { class: "mono" }, toDmy(r.date)), el("td", { class: "mono" }, r.vn), el("td", { class: "mono" }, r.hn),
      el("td", { class: "name-cell", title: r.ptname }, r.ptname, el("span", { class: "sub" }, r.pttype)),
      ...(COLUMNS[service()] || []).map(([, , num, cell]) => el("td", { class: num ? "num" : "mono" }, cell(r))),
      el("td", {}, itemLabel(r)),
      el("td", { class: "num" }, countText(r)),
      el("td", { class: "num" }, money(r.amount)),
      el("td", { class: "problems" }, problems.length ? problems.join(" · ") : dash()));
  }));
  $("empty").hidden = list.length > 0;
  if (!list.length && state.rows.length) $("empty").replaceChildren(...emptyState("ไม่มีรายการตามตัวกรองนี้"));
  $("result-count").textContent = state.rows.length ? `แสดง ${count.format(list.length)} จาก ${count.format(state.rows.length)} รายการ` : "";
  renderPicked();
}

function toggle(key, on) {
  if (on) state.selected.add(key); else state.selected.delete(key);
  render();
}
function pickedRows() { return state.rows.filter((r) => state.selected.has(r.key)); }

// The list of picked items, with a way to take each one out.
function renderPicked() {
  const list = pickedRows();
  const total = list.reduce((s, r) => s + Number(r.amount), 0);
  const byCode = {};
  list.forEach((r) => { byCode[r.code] = (byCode[r.code] || 0) + 1; });
  $("picked").replaceChildren(...list.map((r) => el("li", { class: "picked-item" },
    el("div", { class: "picked-main" },
      el("strong", {}, r.ptname), el("span", { class: "mono" }, ` VN ${r.vn} · HN ${r.hn} · ${toDmy(r.date)}`),
      el("span", { class: "sub" }, `${r.code} ${r.code_name}${r.window_label ? ` · ช่วงวันที่ ${r.window_label}` : ""}${r.count ? ` · ${COUNT_LABEL[service()] || "ครั้งที่"} ${r.count}` : ""}${r.warnings.length ? ` · ⚠ ${r.warnings.map((w) => w.message).join(", ")}` : ""}`)),
    el("span", { class: "num picked-amount" }, money(r.amount)),
    el("button", { type: "button", class: "ghost picked-remove", "aria-label": `เอา ${r.vn} ${r.code} ออก`, onclick: () => toggle(r.key, false) }, "เอาออก"))));
  $("picked-empty").hidden = list.length > 0;
  $("picked-sum").textContent = list.length
    ? `${count.format(list.length)} รายการ (${Object.entries(byCode).map(([c, n]) => `${c} × ${n}`).join(", ")}) · ${money(total)} บาท` : "";
  $("selected-count").textContent = `เลือก ${count.format(list.length)} รายการ`;
  $("selected-total").textContent = `ยอดเบิก ${money(total)} บาท`;
  $("export").disabled = !list.length;
}

function renderRules() {
  const service = state.services.find((s) => s.key === $("service").value);
  $("rules").replaceChildren(...(service ? service.rules : []).map((t) => el("li", {}, t)));
}

async function search() {
  const form = $("search");
  if (!form.reportValidity()) return;
  clearNotices();
  try {
    const data = await api("/api/ppfs/search", { service: form.elements.service.value, start: form.elements.start.value, end: form.elements.end.value });
    state.rows = data.rows;
    const keys = new Set(state.rows.filter(selectable).map((r) => r.key));
    [...state.selected].forEach((k) => { if (!keys.has(k)) state.selected.delete(k); });
  } catch (err) { notice(err.message); }
  render();
}

async function exportZip() {
  clearNotices();
  const list = pickedRows();
  if (!list.length) return;
  const summary = el("dl", { class: "sum" },
    el("dt", {}, "ประเภทบริการ"), el("dd", {}, $("service").selectedOptions[0].textContent),
    el("dt", {}, "รายการ"), el("dd", {}, `${list.length} รายการ จาก ${new Set(list.map((r) => r.vn)).size} visit`),
    el("dt", {}, "ยอดเบิก"), el("dd", {}, `${money(list.reduce((s, r) => s + Number(r.amount), 0))} บาท`),
    el("dt", {}, "มีคำเตือน"), el("dd", {}, `${list.filter((r) => r.warnings.length).length} รายการ`));
  const note = el("p", { class: "muted-note" }, "ZIP มีเฉพาะรายการ PPFS ที่เลือก ค่ารักษาอื่นของ visit ไม่ถูกส่ง");
  if (!await confirmDialog("ยืนยันการสร้าง ZIP 18 แฟ้ม", [summary, note], "สร้าง ZIP")) return;
  const payload = { service: $("service").value, keys: list.map((r) => r.key), dates: [...new Set(list.map((r) => r.date))] };
  $("export").disabled = true; $("export").textContent = "กำลังสร้าง…";
  try {
    let result;
    try { result = await api("/api/ppfs/export", payload); }
    catch (err) {
      if (!err.data || !err.data.needs_confirm) throw err;
      const again = el("p", {}, `มี ${err.data.resend.length} รายการที่เคยส่งออกแล้ว ต้องการส่งซ้ำใช่หรือไม่?`);
      if (!await confirmDialog("ยืนยันการส่งซ้ำ", [again], "ส่งซ้ำ")) return;
      result = await api("/api/ppfs/export", { ...payload, confirm_resend: true });
    }
    const box = notice(`สร้าง ${result.file} แล้ว: ${result.count} รายการ (${result.visits} visit) ยอด ${money(result.total)} บาท`, "ok", true);
    box.querySelector(".notice-text").append(" ", el("a", { href: `/api/ppfs/exports/${result.id}/download` }, "ดาวน์โหลด ZIP"));
    state.selected.clear();
    await Promise.all([search(), loadHistory()]);
  } catch (err) {
    const detail = err.data && err.data.blocked ? `: ${err.data.blocked.map((b) => `${b.key.replace("|", " ")} (${b.reason})`).join(", ")}` : "";
    notice(err.message + detail);
  } finally { $("export").textContent = "สร้าง ZIP 18 แฟ้ม (e-Claim)"; renderPicked(); }
}

// ------------------------------------------------------------- detail drawer (like the 18-file page)
function table(headers, rows) {
  return el("table", {}, el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", { class: h.num ? "num" : "" }, h.label || h)))), el("tbody", {}, ...rows));
}
const info = (pairs) => el("dl", { class: "info-grid" }, ...pairs.map(([k, v]) => el("div", {}, el("dt", {}, k), el("dd", {}, v || "—"))));

async function openDrawer(r) {
  state.current = r.key; render();
  $("drawer").hidden = false; $("drawer-backdrop").hidden = false;
  $("drawer-title").textContent = r.ptname || r.vn;
  $("drawer-sub").textContent = `VN ${r.vn} · HN ${r.hn} · ${toDmy(r.date)}${r.time ? ` ${r.time.slice(0, 5)}` : ""} · ${r.code} ${r.code_name}`;
  const body = $("drawer-body");
  body.replaceChildren(el("p", { class: "muted" }, "กำลังโหลด…"));
  try {
    const kind = service();
    const data = await api("/api/ppfs/detail", { vn: r.vn, service: kind });
    if (state.current !== r.key) return;
    const b = data.birth;
    const own = kind === "ironchild" ? [
      ["อายุ", r.extra.age_text ? `${r.extra.age_text} (${r.extra.months} เดือน)` : "—"],
      ["ยาที่จ่าย", r.extra.drug_name || "ไม่พบ"],
      [`เบิกในปีงบ ${r.extra.year ? r.extra.year + 543 : ""}`, r.extra.first ? `แล้วเมื่อ ${toDmy(r.extra.first)} (VN ${r.extra.first_vn})` : "ยังไม่เคย"],
      ["รหัส ADP ที่ส่ง", r.extra.adp ? `${r.extra.adp} (แทน 10000 ใน HOSxP)` : "ยังไม่ได้ตั้ง PPFS_IRON_CHILD_ADP"],
    ] : kind === "fluoride" ? [
      ["อายุ", r.extra.age === null || r.extra.age === undefined ? "—" : `${r.extra.age} ปี`],
      ["หัตถการทันตกรรม", r.extra.dental.join(", ") || "ไม่พบ"],
      [`ครั้งที่ในปีงบ ${r.extra.year ? r.extra.year + 543 : ""}`, r.count ? `${r.count} จาก 2` : "—"],
    ] : kind === "iron" ? [
      ["อายุ", r.extra.age === null || r.extra.age === undefined ? "—" : `${r.extra.age} ปี`],
      ["ยาที่จ่าย", r.extra.drug_name ? `${r.extra.drug_name} × ${r.extra.drug_qty} เม็ด` : "ไม่พบ"],
      [`เบิกในปีงบ ${r.extra.year ? r.extra.year + 543 : ""}`, r.extra.first ? `แล้วเมื่อ ${toDmy(r.extra.first)} (VN ${r.extra.first_vn})` : "ยังไม่เคย"],
    ] : kind === "anemia" ? [
      ["อายุ", r.extra.age === null || r.extra.age === undefined ? "—" : `${r.extra.age} ปี`],
      ["ผล lab", Object.entries(r.extra.labs).map(([k, v]) => `${k} ${v || "(ไม่มี)"}`).join(" · ") || "—"],
      ["เคยคัดกรองแล้ว", r.extra.first ? `${toDmy(r.extra.first)} (VN ${r.extra.first_vn})` : "ยังไม่เคย"],
    ] : kind === "ncd" ? [
      ["อายุ", r.extra.age === null || r.extra.age === undefined ? "—" : `${r.extra.age} ปี`],
      ["ผล lab", Object.entries(r.extra.labs).map(([k, v]) => `${k} ${v || "(ไม่มี)"}`).join(" · ") || "—"],
      ["เบิกครั้งก่อน", r.extra.last ? `${toDmy(r.extra.last)} (VN ${r.extra.last_vn})` : "ไม่เคย"],
    ] : kind === "fp" ? [
      ["อายุ", r.extra.age === null || r.extra.age === undefined ? "—" : `${r.extra.age} ปี`],
      ["จำนวนที่เบิก", `${r.extra.fee_qty} ${r.extra.unit}`], ["ยาที่จ่าย", r.extra.drug_name ? `${r.extra.drug_name} × ${r.extra.drug_qty}` : "—"],
      [`สะสมในปีงบ ${r.extra.year ? r.extra.year + 543 : ""}`, r.extra.cap ? `${r.extra.year_total || 0} จาก ${r.extra.cap} ${r.extra.cap_unit}` : "ไม่จำกัด"],
    ] : kind === "pregtest" ? [
      ["อายุ", r.extra.age === null || r.extra.age === undefined ? "—" : `${r.extra.age} ปี`], ["ผล UPT", r.extra.upt || "ไม่พบ"],
      ["รหัส Z32", r.extra.z32 ? "มี" : "ไม่มี"], [`ครั้งที่ในปีงบ ${r.extra.year ? r.extra.year + 543 : ""}`, r.count ? `${r.count} จาก 4` : "—"],
    ] : [
      ["วันคลอด", r.labor_date ? toDmy(r.labor_date) : "ไม่พบ"], ["หลังคลอด", r.days === null ? "—" : `${r.days} วัน${r.window_label ? ` (ช่วง ${r.window_label})` : ""}`],
      ["ครั้งที่ (ต่อการคลอด)", r.count ? String(r.count) : "—"],
      ["บันทึกตรวจหลังคลอด", r.care_no ? `ครั้งที่ ${r.care_no} · ${toDmy(r.care_date)}` : "ไม่มี"],
    ];
    const check = [info([
      ["เลขบัตร", r.cid], ["สิทธิ", r.pttype], ["สัญชาติ", data.nationality],
      ["รายการที่เบิก", `${r.code} ${r.code_name}`], ["ยอดเบิก", `${money(r.amount)} บาท (อัตรา ${money(r.rate)})`],
      ...own, ["สถานะ", r.sent ? `ส่งออกแล้ว ${thaiDateTime(r.sent)}` : "ยังไม่ส่ง"]])];
    const issues = [...r.errors.map((i) => ({ ...i, level: "error" })), ...r.warnings.map((i) => ({ ...i, level: "warn" }))];
    if (issues.length) {
      check.push(el("h3", {}, "ผลตรวจ"));
      check.push(el("ul", { class: "issues" }, ...issues.map((i) => el("li", { class: `issue ${i.level}` },
        el("div", { class: "issue-title" }, el("span", { class: `badge ${i.level === "error" ? "b-error" : "b-warn"}` }, i.level === "error" ? "Error" : "เตือน"), " ", i.message)))));
      check.push(el("p", { class: "hint" }, kind === "ironchild" ? "เบิกได้อายุ 6–12 เดือน ปีงบละ 1 ครั้ง ต้องตั้งรหัส ADP ของบริการนี้ใน .env ก่อนจึงจะส่งได้" : kind === "fluoride" ? "เบิกได้อายุ 25–59 ปี ไม่เกิน 2 ครั้งต่อปีงบ ถ้ายังไม่คีย์ 15001 ให้คีย์ใน HOSxP แล้วค้นหาใหม่" : kind === "iron" ? "เบิกได้คนละ 1 ครั้งต่อปีงบ อายุ 13–45 ปี เฉพาะหญิงไทย รายที่ไม่ตรงเงื่อนไขไม่ต้องส่ง" : kind === "anemia" ? "เบิกได้คนละ 1 ครั้ง อายุ 13–24 ปี เฉพาะหญิงไทย รายที่ไม่ตรงเงื่อนไขไม่ต้องส่ง" : kind === "ncd" ? "ผู้ที่มีประวัติรักษาโรคมาก่อน หรือเบิกไปแล้วในรอบ ไม่ใช่กลุ่มที่เบิกได้ ไม่ต้องส่ง" : kind === "fp" ? "แก้ข้อมูลใน HOSxP (คีย์ค่าบริการ 18… ให้ตรงกับยาที่จ่าย) แล้วกดค้นหาอีกครั้ง" : kind === "pregtest" ? "แก้ข้อมูลใน HOSxP (รายการค่าบริการ / รหัสวินิจฉัย / ผล lab) แล้วกดค้นหาอีกครั้ง"
        : "แก้ข้อมูลใน HOSxP (บันทึกหลังคลอด / วันคลอด / รายการค่าบริการ) แล้วกดค้นหาอีกครั้ง"));
    } else check.push(el("div", { class: "all-good" }, "✓ ผ่านการตรวจทั้งหมด พร้อมส่ง"));

    const post = [];
    if (kind === "ironchild") {
      post.push(el("h3", {}, "การรับยาน้ำเสริมธาตุเหล็กทั้งหมดของเด็ก"));
      post.push(data.claims.length ? table(["ปีงบ", "วันที่", "VN", { label: "อายุ (เดือน)", num: true }, "ยาที่จ่าย", { label: "ยอด", num: true }, "ส่งออก"],
        data.claims.map((c, i) => {
          const again = data.claims.slice(0, i).some((x) => x.year === c.year && Number(x.qty) > 0);
          return el("tr", { class: c.vn === r.vn ? "current" : "" }, el("td", {}, again ? el("span", { class: "badge b-error" }, String(c.year)) : String(c.year)),
            el("td", { class: "mono" }, toDmy(c.date)), el("td", { class: "mono" }, c.vn), el("td", { class: "num" }, c.months === null ? "—" : String(c.months)),
            el("td", {}, c.drug || "—"), el("td", { class: "num" }, money(c.amount)), el("td", {}, c.sent ? thaiDateTime(c.sent) : "—"));
        }))
        : el("p", { class: "muted" }, "ไม่มีรายการ"));
      post.push(el("p", { class: "muted-note" }, "เบิกได้ครั้งเดียวต่อปีงบประมาณ (ต.ค.–ก.ย.) ปีงบที่เป็นป้ายสีแดงคือเบิกซ้ำ"));
    } else if (kind === "fluoride") {
      post.push(el("h3", {}, "การเคลือบฟลูออไรด์ (15001) ทั้งหมดของผู้ป่วย"));
      post.push(data.claims.length ? table(["ปีงบ", "วันที่", "VN", "หัตถการทันตกรรม", { label: "ยอด", num: true }, "ส่งออก"],
        data.claims.map((c, i) => {
          const nth = data.claims.slice(0, i + 1).filter((x) => x.year === c.year && Number(x.qty) > 0).length;
          return el("tr", { class: c.vn === r.vn ? "current" : "" },
            el("td", {}, nth > 2 ? el("span", { class: "badge b-error" }, `${c.year} (ครั้งที่ ${nth})`) : `${c.year} (ครั้งที่ ${nth})`),
            el("td", { class: "mono" }, toDmy(c.date)), el("td", { class: "mono" }, c.vn), el("td", {}, c.dental.join(", ") || "—"),
            el("td", { class: "num" }, money(c.amount)), el("td", {}, c.sent ? thaiDateTime(c.sent) : "—"));
        }))
        : el("p", { class: "muted" }, "ยังไม่เคยคีย์ 15001"));
      post.push(el("p", { class: "muted-note" }, "เบิกได้ไม่เกิน 2 ครั้งต่อคนต่อปีงบประมาณ (ต.ค.–ก.ย.) ครั้งที่ 3 ขึ้นไปเป็นป้ายสีแดง"));
    } else if (kind === "iron") {
      post.push(el("h3", {}, "การรับยาเม็ดเสริมธาตุเหล็ก (14001) ทั้งหมดของผู้ป่วย"));
      post.push(data.claims.length ? table(["ปีงบ", "วันที่", "VN", "ยาที่จ่าย", { label: "เม็ด", num: true }, { label: "ยอด", num: true }, "ส่งออก"],
        data.claims.map((c, i) => {
          const again = data.claims.slice(0, i).some((x) => x.year === c.year);
          return el("tr", { class: c.vn === r.vn ? "current" : "" }, el("td", {}, again ? el("span", { class: "badge b-error" }, String(c.year)) : String(c.year)),
            el("td", { class: "mono" }, toDmy(c.date)), el("td", { class: "mono" }, c.vn), el("td", {}, c.drug || "—"),
            el("td", { class: "num" }, c.tablets || "—"), el("td", { class: "num" }, money(c.amount)), el("td", {}, c.sent ? thaiDateTime(c.sent) : "—"));
        }))
        : el("p", { class: "muted" }, "ไม่มีรายการ"));
      post.push(el("p", { class: "muted-note" }, "เบิกได้คนละ 1 ครั้งต่อปีงบประมาณ (ต.ค.–ก.ย.) ปีงบที่เป็นป้ายสีแดงคือเบิกซ้ำ"));
    } else if (kind === "anemia") {
      post.push(el("h3", {}, "การคัดกรองโลหิตจาง (13001) ทั้งหมดของผู้ป่วย"));
      post.push(data.claims.length ? table(["#", "วันที่", "VN", "ผล Hb / Hct", { label: "ยอด", num: true }, "ส่งออก"],
        data.claims.map((c, i) => el("tr", { class: c.vn === r.vn ? "current" : "" }, el("td", {}, i ? el("span", { class: "badge b-error" }, String(i + 1)) : "1"),
          el("td", { class: "mono" }, toDmy(c.date)), el("td", { class: "mono" }, c.vn), el("td", {}, labText(c.labs)),
          el("td", { class: "num" }, money(c.amount)), el("td", {}, c.sent ? thaiDateTime(c.sent) : "—"))))
        : el("p", { class: "muted" }, "ไม่มีรายการ"));
      post.push(el("p", { class: "muted-note" }, "เบิกได้คนละ 1 ครั้ง ครั้งที่ 2 ขึ้นไปส่งไม่ได้"));
    } else if (kind === "ncd") {
      const f = data.first;
      post.push(el("h3", {}, "เริ่มรักษาเมื่อ (จาก HOSxP)"));
      post.push(info([["เบาหวาน (E10–E14)", f.dm ? toDmy(f.dm) : "ไม่พบ"], ["ไขมันในเลือดสูง (E78)", f.lipid ? toDmy(f.lipid) : "ไม่พบ"],
        ["ยาลดไขมันครั้งแรก", data.first_drug ? `${toDmy(data.first_drug.date)} · ${data.first_drug.name}` : "ไม่พบ"]]));
      post.push(el("h3", {}, "การคัดกรองที่เบิกทั้งหมด (12003 / 12004)"));
      post.push(data.claims.length ? table(["วันที่", "VN", "บริการ", "ผล lab", { label: "ยอด", num: true }, "ส่งออก"],
        data.claims.map((c) => el("tr", { class: c.vn === r.vn && c.code === r.code ? "current" : "" }, el("td", { class: "mono" }, toDmy(c.date)),
          el("td", { class: "mono" }, c.vn), el("td", {}, `${c.code} ${c.name}`), el("td", {}, labText(c.labs)),
          el("td", { class: "num" }, money(c.amount)), el("td", {}, c.sent ? thaiDateTime(c.sent) : "—"))))
        : el("p", { class: "muted" }, "ไม่มีรายการ"));
      post.push(el("p", { class: "muted-note" }, "เบาหวานเบิกได้ปีงบละ 1 ครั้ง (ต.ค.–ก.ย.) · ไขมันเบิกได้ 1 ครั้งทุก 5 ปี"));
    } else if (kind === "fp") {
      const year = data.year + 543;
      post.push(el("h3", {}, `เพดานต่อปีงบประมาณ ${year}`));
      post.push(info(data.caps.map((c) => {
        const used = data.history.filter((h) => c.codes.includes(h.code) && Number(h.fee_qty) > 0)
          .reduce((s, h) => s + (c.unit === "แผง" ? Number(h.fee_qty) : 1), 0);
        return [c.names.join(" + "), `${used} / ${c.cap} ${c.unit}${used > c.cap ? " ⚠ เกิน" : ""}`];
      })));
      post.push(el("h3", {}, `รายการวางแผนครอบครัวของปีงบ ${year}`));
      post.push(data.history.length ? table(["วันที่", "VN", "บริการ", { label: "ค่าบริการ", num: true }, { label: "ยา", num: true }, { label: "ยอด", num: true }, "ส่งออก"],
        data.history.map((h) => el("tr", { class: h.vn === r.vn && h.code === r.code ? "current" : "" }, el("td", { class: "mono" }, toDmy(h.date)),
          el("td", { class: "mono" }, h.vn), el("td", {}, `${h.code} ${h.name}`),
          el("td", { class: "num" }, Number(h.fee_qty) ? `${h.fee_qty} ${h.unit}` : el("span", { class: "badge b-error" }, "ไม่คีย์")),
          el("td", { class: "num" }, Number(h.drug_qty) ? h.drug_qty : "—"), el("td", { class: "num" }, money(h.amount)),
          el("td", {}, h.sent ? thaiDateTime(h.sent) : "—"))))
        : el("p", { class: "muted" }, "ไม่มีรายการ"));
    } else if (kind === "pregtest") {
      post.push(el("h3", {}, `ทดสอบการตั้งครรภ์ (30014) ของปีงบ ${data.year + 543}`));
      post.push(data.tests.length ? table(["#", "วันที่", "VN", "ผล UPT", { label: "จำนวน", num: true }, { label: "ยอด", num: true }, "ส่งออก"],
        data.tests.map((t, i) => el("tr", { class: t.vn === r.vn ? "current" : "" }, el("td", {}, i < 4 ? String(i + 1) : el("span", { class: "badge b-error" }, String(i + 1))),
          el("td", { class: "mono" }, toDmy(t.date)), el("td", { class: "mono" }, t.vn), el("td", {}, uptBadge(t.upt)),
          el("td", { class: "num" }, t.qty), el("td", { class: "num" }, money(t.amount)), el("td", {}, t.sent ? thaiDateTime(t.sent) : "—"))))
        : el("p", { class: "muted" }, "ยังไม่มีการคีย์ 30014 ในปีนี้"));
      post.push(el("p", { class: "muted-note" }, "เบิกได้ไม่เกิน 4 ครั้งต่อปีงบประมาณ (ต.ค.–ก.ย.) ครั้งที่ 5 ขึ้นไปส่งไม่ได้"));
    } else post.push(b ? info([["ครรภ์ที่", b.preg_no], ["LMP", b.lmp ? toDmy(b.lmp) : ""], ["วันคลอด", toDmy(b.labor_date)],
      ["วิธีคลอด", b.labour_type], ["สถานที่คลอด", b.hospcode]])
      : el("p", { class: "hint" }, "ไม่พบวันคลอดของผู้ป่วยนี้ใน HOSxP (person_anc.labor_date) ให้บันทึกการคลอดในทะเบียนฝากครรภ์ก่อน"));
    if (kind === "postnatal") {
    post.push(el("h3", {}, "บันทึกตรวจหลังคลอด (person_anc_preg_care)"));
    post.push(data.cares.length ? table(["วันที่", { label: "วันที่หลังคลอด", num: true }, { label: "ครั้งที่", num: true }, "สถานที่", "VN"],
      data.cares.map((c) => el("tr", { class: c.vn === r.vn ? "current" : "" }, el("td", { class: "mono" }, toDmy(c.date)),
        el("td", { class: "num" }, c.days === null ? "—" : String(c.days)), el("td", { class: "num" }, c.care_no ? String(c.care_no) : "—"),
        el("td", {}, c.place || "—"), el("td", { class: "mono" }, c.vn || el("span", { class: "muted" }, "ไม่มี VN (ไม่เบิก)")))))
      : el("p", { class: "muted" }, "ไม่มีบันทึก"));
    post.push(el("h3", {}, "รายการ 30015 / 30016 ที่คีย์ใน HOSxP ของการคลอดนี้"));
    post.push(data.claims.length ? table(["วันที่", "VN", "รายการ", { label: "หลังคลอด", num: true }, "ช่วง", { label: "ยอด", num: true }, "ส่งออก"],
      data.claims.map((c) => el("tr", { class: c.vn === r.vn && c.code === r.code ? "current" : "" }, el("td", { class: "mono" }, toDmy(c.date)),
        el("td", { class: "mono" }, c.vn), el("td", {}, `${c.code} ${c.code === "30015" ? "ตรวจหลังคลอด" : "ยาธาตุเหล็ก"}`),
        el("td", { class: "num" }, c.days === null ? "—" : `${c.days} วัน`), el("td", {}, c.window || (c.code === "30015" ? "เกิน 42 วัน" : "—")),
        el("td", { class: "num" }, money(c.amount)), el("td", {}, c.sent ? thaiDateTime(c.sent) : "—"))))
      : el("p", { class: "muted" }, "ไม่มีรายการ"));
    post.push(el("h3", {}, "ยาธาตุเหล็ก/โฟลิก/ไอโอดีน ที่จ่ายใน visit นี้"));
    post.push(data.iron.length ? table(["ยา", { label: "จำนวน", num: true }], data.iron.map((i) => el("tr", {}, el("td", {}, i.name), el("td", { class: "num" }, i.qty))))
      : el("p", { class: "muted" }, "ไม่พบ"));
    }

    const dx = [data.dx.length ? table(["ประเภท", "รหัส", "ชื่อโรค"], data.dx.map((d) => el("tr", {}, el("td", {}, d.type), el("td", { class: "mono" }, d.code), el("td", {}, d.name)))) : el("p", { class: "muted" }, "ไม่มีข้อมูล")];
    const claimed = new Set(state.rows.filter((x) => x.vn === r.vn).map((x) => x.code));
    const costs = [el("p", { class: "muted-note" }, `ZIP PPFS ส่งเฉพาะรายการ ${[...claimed].join(" / ")} รายการอื่นของ visit ไม่ถูกส่ง`),
      table(["ส่ง", "รายการ", "ADP", { label: "จำนวน", num: true }, { label: "รวม", num: true }], data.items.map((i) => {
        const sent = claimed.has(i.adp) && !(data.drug_icodes || []).includes(i.icode);
        return el("tr", { class: sent ? "" : "excluded" }, el("td", {}, sent ? el("span", { class: "badge b-ok" }, "ส่ง") : el("span", { class: "muted" }, "ไม่ส่ง")),
          el("td", {}, i.name), el("td", { class: "mono" }, i.adp || "—"), el("td", { class: "num" }, i.qty), el("td", { class: "num" }, money(i.amount)));
      }))];
    body.replaceChildren(...tabs([
      { id: "check", label: "สรุปและผลตรวจ", badge: issues.length || undefined, alert: r.errors.length > 0, content: check },
      kind === "ironchild" ? { id: "ironchild", label: "ประวัติรับยา", badge: data.claims.length || undefined, content: post }
        : kind === "fluoride" ? { id: "fluoride", label: "ประวัติเคลือบฟลูออไรด์", badge: data.claims.length || undefined, content: post }
        : kind === "iron" ? { id: "iron", label: "ประวัติรับยา", badge: data.claims.length || undefined, content: post }
        : kind === "anemia" ? { id: "anemia", label: "ประวัติคัดกรอง", badge: data.claims.length || undefined, content: post }
        : kind === "ncd" ? { id: "ncd", label: "ประวัติคัดกรอง", badge: data.claims.length || undefined, content: post }
        : kind === "fp" ? { id: "fp", label: "ประวัติวางแผนครอบครัว", badge: data.history.length || undefined, content: post }
        : kind === "pregtest" ? { id: "tests", label: "ประวัติทดสอบ", badge: data.tests.length || undefined, content: post }
        : { id: "postnatal", label: "หลังคลอด", badge: data.claims.length || undefined, content: post },
      { id: "dx", label: "การวินิจฉัย", badge: data.dx.length, content: dx },
      { id: "cost", label: "ค่าใช้จ่าย", badge: data.items.length, content: costs },
    ]));
  } catch (err) { body.replaceChildren(el("p", {}, err.message)); }
}
function closeDrawer() { $("drawer").hidden = true; $("drawer-backdrop").hidden = true; state.current = null; render(); }

async function loadHistory() {
  try {
    const rows = await api("/api/ppfs/exports");
    $("history").replaceChildren(rows.length ? el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ...["เวลา", "ประเภทบริการ", "รายการ", "ยอดเบิก", "ไฟล์", "ผู้ส่งออก"].map((h) => el("th", {}, h)))),
      el("tbody", {}, ...rows.map((r) => el("tr", {},
        el("td", {}, thaiDateTime(r.created_at)), el("td", {}, r.service), el("td", { class: "num" }, String(r.item_count)),
        el("td", { class: "num" }, money(r.total)),
        el("td", {}, el("a", { href: `/api/ppfs/exports/${r.id}/download` }, r.file)), el("td", {}, r.created_by || "—")))))) :
      el("p", { class: "hint" }, "ยังไม่มีการส่งออก"));
  } catch (err) { notice(err.message); }
}

async function loadServices() {
  try {
    state.services = await api("/api/ppfs/services");
    const current = $("service").value;
    $("service").replaceChildren(...state.services.map((s) => el("option", { value: s.key, selected: s.key === current }, s.label)));
  } catch (err) { notice(err.message); }
  renderRules();
}

async function connection() {
  try {
    const status = await api("/api/status");
    if (status.database) setConnection(`HOSxP · ${status.db_host}`, true);
    else setConnection(status.db_error ? `เชื่อมต่อไม่ได้ (${status.db_host})` : "ยังไม่ได้ตั้งค่าฐานข้อมูล", false);
  } catch (err) { setConnection("เชื่อมต่อไม่ได้", false); }
}

$("search").addEventListener("submit", (e) => { e.preventDefault(); search(); });
$("service").addEventListener("change", () => { state.rows = []; state.selected.clear(); state.sort.key = "date"; state.sort.dir = "asc"; renderRules(); renderHead(); render(); });
$("filter").addEventListener("input", (e) => { state.text = e.target.value; render(); });
$("select-ready").onclick = () => { visible().filter((r) => selectable(r) && !r.warnings.length && !r.sent).forEach((r) => state.selected.add(r.key)); render(); };
$("select-none").onclick = () => { state.selected.clear(); render(); };
$("export").onclick = exportZip;
$("drawer-close").onclick = closeDrawer;
$("drawer-backdrop").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden && !$("confirm").open) closeDrawer(); });
renderHead();
setupPresets($("search"));
(() => { const today = new Date(); $("search").elements.start.value = isoDate(new Date(today.getFullYear(), today.getMonth(), 1)); $("search").elements.end.value = isoDate(today); })();
loadServices();
loadHistory();
connection();
render();
