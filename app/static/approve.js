"use strict";
const state = { rows: [], filter: "all", text: "", sort: { key: "txn_date", dir: "desc" } };
const money = (v) => (v === "" || v === null || v === undefined ? "" : baht.format(Number(v)));
const dash = () => el("span", { class: "muted" }, "—");
const mismatch = (r) => r.state === "matched" && r.income !== "" && Math.abs(Number(r.income) - Number(r.amount)) >= 0.01;

const STATES = {
  matched: ["ใช้แทน ClaimCode", "b-ok"],
  wrong_right: ["visit ไม่ใช่สิทธิเบิกจ่ายตรง", "b-warn"],
  no_visit: ["ไม่พบ visit", "b-error"],
  refunded: ["ถูกคืนเงิน/ยกเลิก", "b-sent"],
  refund: ["คืนเงิน/Void", "b-sent"],
  missing: ["ยังไม่มี Approve Code", "b-c"],
};
const payment = (r) => r.kind === "PAYMENT";
const CATEGORIES = {
  all: ["รายการชำระทั้งหมด", payment, "all"],
  matched: ["ใช้แทน ClaimCode", (r) => r.state === "matched", "ok"],
  nomatch: ["ไม่พบ visit / ไม่ใช่สิทธิ", (r) => r.state === "no_visit" || r.state === "wrong_right", "error"],
  mismatch: ["ยอดรูดไม่ตรงค่าใช้จ่าย", mismatch, "warn"],
  refund: ["คืนเงิน / ยกเลิก", (r) => r.state === "refunded" || r.state === "refund", "sent"],
  missing: ["visit สิทธิเบิกจ่ายตรง ยังไม่มี Approve Code", (r) => r.state === "missing", "special"],
};

function notes(r) {
  const list = [];
  if (r.state === "no_visit") list.push(r.in_hosxp ? "ไม่พบ visit ในวันที่รูดบัตร" : "ไม่พบเลขบัตรนี้ใน HOSxP");
  if (r.state === "wrong_right") list.push(`visit ใช้สิทธิ ${r.pttype}`);
  if (mismatch(r)) list.push(`ยอดรูดต่างจากค่าใช้จ่าย ${baht.format(Number(r.amount) - Number(r.income))}`);
  if (r.refunded_by) list.push(`ถูกคืนเงิน/ยกเลิกด้วย ${r.refunded_by}`);
  if (r.kind === "VOID") list.push(r.refund_of ? `ยกเลิก (Void) รายการ ${r.refund_of}` : "ยกเลิก (Void) แต่ยังไม่พบรายการชำระ");
  if (r.kind === "REFUND") list.push(r.refund_of ? `คืนเงินรายการ ${r.refund_of}${r.refund_of_date ? ` (${toDmy(r.refund_of_date)})` : ""}` : "ยังไม่พบรายการชำระที่ถูกคืน (อาจอยู่ในไฟล์วันอื่น)");
  if (r.channel === "CB-IBANK") list.push(`คีย์มือ${r.reason ? `: ${r.reason}` : ""}`);
  if (r.state === "matched" && r.visit_date && r.visit_date !== r.txn_date) list.push(`visit วันที่ ${toDmy(r.visit_date)}`);
  return list;
}

function visible() {
  const q = state.text.toLowerCase();
  const test = CATEGORIES[state.filter][1];
  const list = state.rows.filter((r) => test(r) && (!q || `${r.vn} ${r.hn} ${r.ptname} ${r.appr_code} ${r.trans_ref}`.toLowerCase().includes(q)));
  return sortRows(list, state.sort, ["amount", "income"]);
}

function render() {
  renderCards($("summary"), CATEGORIES, state.rows, state.filter, (r) => r.amount || r.income, (key) => { state.filter = key; render(); });
  const list = visible();
  showSort($("thead"), state.sort);
  $("rows").replaceChildren(...list.map((r) => {
    const [label, tone] = STATES[r.state] || [r.state, "b-sent"];
    const remarks = notes(r);
    return el("tr", { class: mismatch(r) ? "flag-row" : "" },
      el("td", {}, el("span", { class: `badge ${tone}` }, label)),
      el("td", { class: "mono" }, toDmy(r.txn_date), r.txn_time ? el("span", { class: "sub" }, r.txn_time) : null),
      el("td", { class: "mono" }, r.vn || dash()), el("td", { class: "mono" }, r.hn || dash()),
      el("td", { class: "name-cell", title: r.ptname }, r.ptname || dash(), r.pttype ? el("span", { class: "sub" }, r.pttype) : null),
      el("td", { class: "mono" }, r.cid),
      el("td", { class: "num" }, r.amount !== "" ? money(r.amount) : dash()),
      el("td", { class: "num" }, r.income !== "" ? money(r.income) : dash()),
      el("td", { class: "mono doc" }, r.appr_code ? el("strong", {}, r.appr_code) : dash(), r.trans_ref && r.trans_ref !== r.appr_code ? el("span", { class: "sub" }, `Ref ${r.trans_ref}`) : null),
      el("td", { class: "mono" }, r.claim_code || dash()),
      el("td", {}, r.channel === "TMS" ? "EDC" : r.channel === "CB-IBANK" ? "คีย์มือ" : r.channel || dash()),
      el("td", { class: "problems" }, remarks.length ? remarks.join(" · ") : dash()));
  }));
  $("empty").hidden = list.length > 0;
  if (!list.length && state.rows.length) $("empty").replaceChildren(...emptyState("ไม่มีรายการตามตัวกรองนี้"));
  $("result-count").textContent = state.rows.length ? `แสดง ${count.format(list.length)} จาก ${count.format(state.rows.length)} รายการ` : "";
}

function renderHistory(uploads) {
  $("history").replaceChildren(uploads.length ? el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...["เวลา", "ไฟล์", "ข้อมูลวันที่", "รายการ", "ยอดรวม", "ผู้นำเข้า"].map((h) => el("th", {}, h)))),
    el("tbody", {}, ...uploads.map((u) => el("tr", {},
      el("td", {}, thaiDateTime(u.at)), el("td", {}, u.name), el("td", {}, u.file_date ? toDmy(u.file_date) : "—"),
      el("td", { class: "num" }, String(u.row_count)), el("td", { class: "num" }, money(u.total)), el("td", {}, u.created_by)))))) : el("p", { class: "hint" }, "ยังไม่มีการนำเข้า"));
}

async function load() {
  const form = $("search");
  try {
    const data = await api("/api/approve/list", { start: form.elements.start.value, end: form.elements.end.value });
    state.rows = data.rows;
    $("pttypes").textContent = data.pttypes.join(", ");
    if (data.db_error) notice(data.db_error, "warn");
    renderHistory(data.uploads);
  } catch (err) { notice(err.message); }
  render();
}

async function importFiles(files) {
  const log = $("import-log");
  for (const file of files) {
    if (/\.pdf$/i.test(file.name)) {
      log.prepend(el("li", { class: "skip" }, `${file.name} · ข้าม (PDF เป็นรายงานชุดเดียวกับ TXT)`));
      continue;
    }
    const line = el("li", { class: "busy" }, `${file.name} · กำลังนำเข้า…`);
    log.prepend(line);
    try {
      const response = await fetch(`/api/approve/import?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `ผิดพลาด (${response.status})`);
      line.className = "ok";
      line.replaceChildren(el("strong", {}, `✓ ${file.name}`),
        ...data.files.map((f) => ` · ${f.file_date ? toDmy(f.file_date) : f.name}: ชำระ ${f.payments} คืนเงิน ${f.refunds} รวม ${money(f.total)} บาท (ใหม่ ${f.added}, อัปเดต ${f.updated})`),
        data.skipped.length ? el("span", { class: "sub" }, `ข้ามไฟล์: ${data.skipped.join(", ")}`) : null,
        data.again ? el("span", { class: "sub" }, data.again) : null);
    } catch (err) {
      line.className = "error";
      line.replaceChildren(el("strong", {}, `✕ ${file.name}`), ` · ${err.message}`);
    }
  }
  $("files").value = "";
  await load();
}

function setupDrop() {
  const drop = $("drop");
  $("files").addEventListener("change", (e) => importFiles([...e.target.files]));
  ["dragenter", "dragover"].forEach((type) => drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((type) => drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => importFiles([...e.dataTransfer.files]));
}

async function connection() {
  try {
    const status = await api("/api/status");
    if (status.database) setConnection(`HOSxP · ${status.db_host}`, true);
    else setConnection(status.db_error ? `เชื่อมต่อไม่ได้ (${status.db_host})` : "ยังไม่ได้ตั้งค่าฐานข้อมูล", false);
  } catch (err) { setConnection("เชื่อมต่อไม่ได้", false); }
}

$("search").addEventListener("submit", (e) => { e.preventDefault(); clearNotices(); load(); });
$("show-all").onclick = () => { $("search").elements.start.value = ""; $("search").elements.end.value = ""; load(); };
$("filter").addEventListener("input", (e) => { state.text = e.target.value; render(); });
bindSort($("thead"), state.sort, render);
setupPresets($("search"));
setupDrop();
load();
connection();
