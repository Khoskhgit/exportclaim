"use strict";
const state = { claims: [], filter: "all", text: "", pttypes: ["29"], current: null, sort: { key: "service_date", dir: "desc" } };
const money = (v) => (v === "" || v === null || v === undefined ? "" : baht.format(Number(v)));
const dash = () => el("span", { class: "muted" }, "—");
const mismatch = (c) => c.paid_amount !== "" && Number(c.paid_amount) !== Number(c.claim_amount);

const STATES = {
  paid: ["จ่ายแล้ว", "b-ok"],
  paid_no_detail: ["จ่ายแล้ว (ยังไม่มีรายงานจ่าย)", "b-sent"],
  waiting: ["รอจ่าย", "b-warn"],
  rejected: ["ไม่อนุมัติ", "b-error"],
  unknown: ["มีแต่รายงานจ่าย", "b-sent"],
  unclaimed: ["ยังไม่ส่งเบิก", "b-c"],
};
const claimed = (c) => c.state !== "unclaimed";
const CATEGORIES = {
  all: ["ส่งเบิกทั้งหมด", claimed, "all"],
  paid: ["จ่ายแล้ว", (c) => c.state === "paid" || c.state === "paid_no_detail", "ok"],
  waiting: ["รอจ่าย", (c) => c.state === "waiting", "warn"],
  rejected: ["ไม่อนุมัติ", (c) => c.state === "rejected", "error"],
  nomatch: ["ไม่พบ visit / ไม่ใช่สิทธิทันตกรรม", (c) => claimed(c) && (!c.vn || !c.right_ok), "special"],
  mismatch: ["ยอดจ่ายไม่ตรง", mismatch, "sent"],
  unclaimed: ["ใช้สิทธิทันตกรรม ยังไม่ส่งเบิก", (c) => c.state === "unclaimed", "error"],
};

function notes(c) {
  const list = [];
  if (!c.in_hosxp) list.push("ไม่พบเลขบัตรนี้ใน HOSxP");
  else if (!c.vn) list.push("ไม่พบ visit วันที่ใช้สิทธิ");
  else if (!c.has_dental) list.push("visit นี้ไม่มีบันทึกทันตกรรม");
  if (c.vn && !c.right_ok) list.push(`visit ไม่ได้ใช้สิทธิ ${state.pttypes.join(", ")}`);
  if (c.visits_that_day > 1) list.push(`วันนั้นมี ${c.visits_that_day} visit`);
  if (mismatch(c)) list.push(`จ่ายต่างจากขอเบิก ${baht.format(Number(c.paid_amount) - Number(c.claim_amount))}`);
  if (c.state === "waiting" && c.status !== "รอจ่าย") list.push(c.status);
  return list;
}

function visible() {
  const q = state.text.toLowerCase();
  const test = CATEGORIES[state.filter][1];
  const list = state.claims.filter((c) => test(c) && (!q || `${c.vn} ${c.hn} ${c.ptname} ${c.voucher}`.toLowerCase().includes(q)));
  return sortRows(list, state.sort, ["claim_amount", "paid_amount"]);
}

function render() {
  renderCards($("summary"), CATEGORIES, state.claims, state.filter, (c) => c.claim_amount,
    (key) => { state.filter = key; render(); });
  const list = visible();
  showSort($("thead"), state.sort);
  $("rows").replaceChildren(...list.map((c) => {
    const [label, tone] = STATES[c.state] || [c.state, "b-sent"];
    const remarks = notes(c);
    const id = rowId(c);
    return el("tr", { class: `${mismatch(c) ? "flag-row" : ""} ${state.current === id ? "current" : ""}`.trim(), onclick: () => openDrawer(c) },
      el("td", {}, el("span", { class: `badge ${tone}` }, label)),
      el("td", { class: "mono" }, toDmy(c.service_date)),
      el("td", { class: "mono" }, c.vn || dash()), el("td", { class: "mono" }, c.hn || dash()),
      el("td", { class: "name-cell", title: c.ptname }, c.ptname || dash(), c.pttype ? el("span", { class: "sub" }, c.pttype) : null),
      el("td", { class: "mono" }, c.cid), el("td", {}, c.service_type),
      el("td", { class: "num" }, c.claim_amount !== "" ? money(c.claim_amount) : dash()),
      el("td", { class: "num paid" }, c.paid_amount !== "" ? money(c.paid_amount) : dash()),
      el("td", { class: "mono doc" }, c.voucher || dash()), el("td", { class: "mono" }, c.transfer_date ? toDmy(c.transfer_date) : dash()),
      el("td", { class: "problems" }, remarks.length ? remarks.join(" · ") : dash()));
  }));
  $("empty").hidden = list.length > 0;
  if (!list.length && state.claims.length) $("empty").replaceChildren(...emptyState("ไม่มีรายการตามตัวกรองนี้"));
  $("result-count").textContent = state.claims.length ? `แสดง ${count.format(list.length)} จาก ${count.format(state.claims.length)} รายการ` : "";
}

// ------------------------------------------------------------- drawer
const rowId = (c) => `${c.vn}|${c.cid}|${c.service_date}|${c.claim_amount}|${c.state}`;
function info(pairs) {
  return el("dl", { class: "info-grid" }, ...pairs.map(([k, v]) => el("div", {}, el("dt", {}, k), el("dd", {}, v || "—"))));
}
function table(headers, rows) {
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", { class: h.num ? "num" : "" }, h.label || h)))), el("tbody", {}, ...rows)));
}
function closeDrawer() { $("drawer").hidden = true; $("drawer-backdrop").hidden = true; state.current = null; render(); }

async function openDrawer(c) {
  const id = rowId(c);
  state.current = id; render();
  $("drawer").hidden = false; $("drawer-backdrop").hidden = false;
  $("drawer-title").textContent = c.ptname || (c.vn ? `VN ${c.vn}` : "รายการเบิกทันตกรรม");
  $("drawer-sub").textContent = [c.vn && `VN ${c.vn}`, c.hn && `HN ${c.hn}`, `ใช้สิทธิ ${toDmy(c.service_date)}`].filter(Boolean).join(" · ");
  const [label, tone] = STATES[c.state] || [c.state, "b-sent"];
  const summary = [
    el("p", {}, el("span", { class: `badge ${tone}` }, label)),
    info([["เลขบัตร", c.cid], ["สิทธิ (HOSxP)", c.pttype], ["ประเภทค่าบริการ", c.service_type], ["วันที่ส่งเบิก", c.claim_date && toDmy(c.claim_date)],
      ["ยอดขอเบิก", c.claim_amount !== "" ? `${money(c.claim_amount)} บาท` : ""], ["ยอดจ่าย", c.paid_amount !== "" ? `${money(c.paid_amount)} บาท` : ""],
      ["เลขที่ใบสำคัญรับเงิน", c.voucher], ["วันที่โอน", c.transfer_date && toDmy(c.transfer_date)], ["สถานะรับแจ้ง", c.status]]),
  ];
  const remarks = notes(c);
  if (remarks.length) summary.push(el("ul", { class: "issues" }, ...remarks.map((r) => el("li", { class: "issue warn" }, el("div", { class: "issue-title" }, r)))));
  const body = $("drawer-body");
  if (!c.vn) {
    body.replaceChildren(...summary, el("p", { class: "hint" }, "ยังไม่พบ visit ใน HOSxP ตรวจเลขบัตรและวันที่ใช้สิทธิ"));
    return;
  }
  body.replaceChildren(...summary, el("p", { class: "muted" }, "กำลังโหลด…"));
  try {
    const [visit, dental] = await Promise.all([api("/api/detail", { vn: c.vn }), api("/api/dental/detail", { vn: c.vn })]);
    if (state.current !== id) return;
    const procedures = dental.procedures.length
      ? table(["ซี่ฟัน", "รหัส", "หัตถการ", "ICD-10", "ICD-9", { label: "ค่าบริการ", num: true }], dental.procedures.map((p) => el("tr", {},
        el("td", { class: "mono" }, p.tooth), el("td", { class: "mono" }, p.tmcode), el("td", {}, p.name || "—", p.note ? el("span", { class: "sub" }, p.note) : null),
        el("td", { class: "mono" }, p.icd10), el("td", { class: "mono" }, p.icd9), el("td", { class: "num" }, money(p.fee)))))
      : el("p", { class: "hint" }, "visit นี้ไม่มีบันทึกทันตกรรม (dtmain)");
    const fee = dental.procedures.reduce((sum, p) => sum + Number(p.fee || 0), 0);
    const claims = dental.claims.length
      ? table(["วันที่ใช้สิทธิ", "ประเภท", { label: "ขอเบิก", num: true }, { label: "จ่าย", num: true }, "สถานะ", "เลขใบสำคัญ"], dental.claims.map((k) => {
        const [l, t] = STATES[k.state] || [k.state, "b-sent"];
        return el("tr", {}, el("td", {}, toDmy(k.service_date)), el("td", {}, k.service_type), el("td", { class: "num" }, money(k.claim_amount)),
          el("td", { class: "num" }, k.paid_amount !== "" ? money(k.paid_amount) : "—"), el("td", {}, el("span", { class: `badge ${t}` }, l)), el("td", { class: "mono doc" }, k.voucher || "—"));
      }))
      : el("p", { class: "hint" }, "visit นี้ยังไม่มีรายการเบิกทันตกรรมที่นำเข้า");
    const total = visit.items.reduce((sum, i) => sum + Number(i.sum_price || 0), 0);
    const items = visit.items.length
      ? table(["หมวด", "รายการ", { label: "จำนวน", num: true }, { label: "ราคา/หน่วย", num: true }, { label: "รวม", num: true }], visit.items.map((i) => el("tr", {},
        el("td", {}, i.group), el("td", {}, i.name), el("td", { class: "num" }, i.qty), el("td", { class: "num" }, money(i.unitprice)), el("td", { class: "num" }, money(i.sum_price)))))
      : el("p", { class: "muted" }, "ไม่มีรายการค่าใช้จ่าย");
    const dx = visit.dx.length
      ? table(["ประเภท", "รหัส", "ชื่อโรค"], visit.dx.map((d) => el("tr", {}, el("td", {}, d.type), el("td", { class: "mono" }, d.code), el("td", {}, d.name))))
      : el("p", { class: "muted" }, "ไม่มีข้อมูลการวินิจฉัย");
    body.replaceChildren(...tabs([
      { id: "claim", label: "รายการเบิก", badge: dental.claims.length, content: [...summary, el("h3", {}, "รายการเบิกทันตกรรมของ visit นี้"), claims] },
      { id: "dental", label: "หัตถการทันตกรรม", badge: dental.procedures.length, alert: !dental.procedures.length,
        content: [procedures, dental.procedures.length ? el("p", { class: "muted-note" }, `รวมค่าบริการทันตกรรม ${money(fee)} บาท`) : null] },
      { id: "cost", label: "ค่าใช้จ่าย", badge: visit.items.length, content: [el("div", { class: "amount-bar" }, el("div", {}, el("span", {}, "รวมทั้ง visit "), el("b", {}, money(total)))), items] },
      { id: "dx", label: "วินิจฉัย", badge: visit.dx.length, content: [dx] },
    ]));
  } catch (err) {
    if (state.current === id) body.replaceChildren(...summary, el("p", { class: "danger-text" }, err.message));
  }
}

function renderHistory(uploads) {
  $("history").replaceChildren(uploads.length ? el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...["เวลา", "ไฟล์", "ชนิด", "ช่วงวันที่", "รายการ", "ยอดรวม", "ผู้นำเข้า"].map((h) => el("th", {}, h)))),
    el("tbody", {}, ...uploads.map((u) => el("tr", {},
      el("td", {}, thaiDateTime(u.at)), el("td", {}, u.name), el("td", {}, u.kind === "payment" ? "รายงานการจ่าย" : "รายงานการส่งเบิก"),
      el("td", {}, `${toDmy(u.period_start)} – ${toDmy(u.period_end)}`), el("td", { class: "num" }, String(u.row_count)),
      el("td", { class: "num" }, money(u.total)), el("td", {}, u.created_by)))))) : el("p", { class: "hint" }, "ยังไม่มีการนำเข้า"));
}

async function load() {
  const form = $("search");
  try {
    const data = await api("/api/dental/list", { start: form.elements.start.value, end: form.elements.end.value });
    state.claims = data.claims;
    state.pttypes = data.dental_pttypes;
    if (data.db_error) notice(data.db_error, "warn");
    renderHistory(data.uploads);
  } catch (err) { notice(err.message); }
  render();
}

async function importFiles(files) {
  const log = $("import-log");
  for (const file of files) {
    const line = el("li", { class: "busy" }, `${file.name} · กำลังนำเข้า…`);
    log.prepend(line);
    try {
      const response = await fetch(`/api/dental/import?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `ผิดพลาด (${response.status})`);
      line.className = "ok";
      line.replaceChildren(el("strong", {}, `✓ ${file.name}`),
        ` · ${data.kind} ${toDmy(data.period_start)} – ${toDmy(data.period_end)} · ${data.rows} รายการ ${money(data.total)} บาท (ใหม่ ${data.added}, อัปเดต ${data.updated})`,
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

$("drawer-close").onclick = closeDrawer;
$("drawer-backdrop").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden) closeDrawer(); });
$("search").addEventListener("submit", (e) => { e.preventDefault(); clearNotices(); load(); });
$("show-all").onclick = () => { $("search").elements.start.value = ""; $("search").elements.end.value = ""; load(); };
$("filter").addEventListener("input", (e) => { state.text = e.target.value; render(); });
bindSort($("thead"), state.sort, render);
setupPresets($("search"));
setupDrop();
load();
connection();
