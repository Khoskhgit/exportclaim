"use strict";
const state = { reps: [], stms: [], uploads: [] };

const money = (v) => baht.format(Number(v || 0));
const when = (iso) => {
  if (!iso) return "";
  const [d, t] = String(iso).split(/[T ]/);
  return t ? `${toDmy(d)} ${t.slice(0, 5)}` : toDmy(d);
};
const statBadge = (stat) => stat === "A"
  ? el("span", { class: "badge b-ok" }, "A ผ่าน")
  : el("span", { class: "badge b-error" }, `${stat || "?"} ไม่ผ่าน`);

// e-Claim REP codes (UCS / OFC / LGO) are explained by the สปสช. C ERROR document (cc = c_codes from the server);
// SSOP and AIPN codes by the names their own REP files carry.
const codeName = (code, names, cc) => (cc && cc[code] && cc[code].found ? cc[code].detail : names[code]) || "";

function codeList(codes, names, cc, detailed = false) {
  if (!codes) return el("span", { class: "muted" }, "-");
  const list = codes.split(",").filter(Boolean);
  return el("span", { class: "code-list" }, ...list.map((code) => el("span", {
    class: `badge ${code.startsWith("W") ? "b-warn" : "b-error"}`, title: codeName(code, names, cc),
  }, code)), ...(detailed ? list.filter((code) => codeName(code, names, cc)).map((code) => el("span", { class: "code-detail" }, `${code}: ${codeName(code, names, cc)}`)) : []));
}

function codeLegend(codes, names, cc) {
  const used = [...new Set(codes.flatMap((c) => (c || "").split(",")).filter(Boolean))].sort();
  if (!used.length) return null;
  const fromDoc = used.some((code) => cc && cc[code] && cc[code].found);
  return el("div", {}, el("ul", { class: "issues" }, ...used.map((code) => {
    const doc = cc && cc[code];
    return el("li", { class: `issue ${code.startsWith("W") ? "warn" : "error"}` },
      el("div", { class: "issue-title" }, el("span", { class: `badge ${code.startsWith("W") ? "b-warn" : "b-error"}` }, code), " ",
        codeName(code, names, cc) || (doc ? "ไม่พบรหัสนี้ในเอกสาร C ERROR E-CLAIM" : "ไม่มีคำอธิบายในไฟล์ REP")),
      doc && doc.found ? fixBlock(doc.fix || "เอกสารไม่ได้ระบุแนวทางแก้ไขของรหัสนี้") : null);
  })), fromDoc ? el("p", { class: "muted-note" }, "รายละเอียดและแนวทางแก้ไขจากเอกสาร C ERROR E-CLAIM NHSO (สปสช. 06-01-2565)") : null);
}

// FDH claim status: paid or capitation in green, refused or failed in red, anything else (waiting) grey-blue.
function fdhBadge(status) {
  const tone = /ไม่ผ่าน|ปฏิเสธ|ไม่อนุมัติ|error|fail|reject/i.test(status || "") ? "b-error"
    : /จ่าย|เหมาจ่าย|อนุมัติ|ผ่าน|success/i.test(status || "") ? "b-ok" : "b-sent";
  return el("span", { class: `badge ${tone}` }, status || "—");
}
function info(pairs) {
  return el("dl", { class: "info-grid" }, ...pairs.map(([k, v]) => el("div", {}, el("dt", {}, k), el("dd", {}, String(v ?? "—")))));
}

function table(headers, rows) {
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...headers.map(([label, cls]) => el("th", { class: cls || "" }, label)))),
    el("tbody", {}, ...rows)));
}

// ------------------------------------------------------------- import
async function importFiles(files) {
  const log = $("import-log");
  for (const file of files) {
    const line = el("li", { class: "busy" }, `${file.name} · กำลังนำเข้า…`);
    log.prepend(line);
    try {
      const response = await fetch(`/api/repstm/import?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `ผิดพลาด (${response.status})`);
      line.className = "ok";
      line.replaceChildren(el("strong", {}, `✓ ${file.name}`), ` · ${data.summary}`,
        data.again ? el("span", { class: "sub" }, data.again) : null,
        data.skipped.length ? el("span", { class: "sub" }, `ไม่รู้จักไฟล์: ${data.skipped.join(", ")}`) : null);
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

// -------------------------------------------------------------- lists
async function load() {
  try {
    Object.assign(state, await api("/api/repstm/list"));
  } catch (err) { notice(err.message); }
  renderLists();
}

function renderLists() {
  const repRows = state.reps.map((r) => el("tr", { onclick: () => openRep(r.rep_no) },
    el("td", { class: "mono" }, r.rep_no), el("td", {}, when(r.issued_at)), el("td", { class: "mono" }, r.session),
    el("td", { class: "num" }, String(r.sent || r.lines)), el("td", { class: "num" }, String(r.passed)),
    el("td", { class: "num" }, r.failed ? el("span", { class: "badge b-error" }, String(r.failed)) : "0"),
    el("td", { class: "num" }, money(r.passed_amount)), el("td", { class: "num" }, money(r.failed_amount)),
    el("td", {}, r.account === "OFC" ? "e-Claim ข้าราชการ" : r.account === "UCS" ? "e-Claim UC" : r.account === "LGO" ? "e-Claim อปท."
      : r.account === "SIGN" ? `AIPN ผู้ป่วยใน ปกส. (${[r.has_bil ? "REP" : "", r.has_dis ? "SUP" : ""].filter(Boolean).join(" + ")})`
      : [r.has_bil ? "BIL" : "", r.has_dis ? "DIS" : ""].filter(Boolean).join(" + "))));
  const stmRows = state.stms.map((s) => el("tr", { onclick: () => openStm(s.stm_no) },
    el("td", { class: "mono" }, s.stm_no), el("td", {}, s.account === "SIGN" ? "AIPN ผู้ป่วยใน ปกส." : s.account), el("td", { class: "mono" }, s.period),
    el("td", {}, `${toDmy(s.date_start)} – ${toDmy(s.date_end)}`), el("td", {}, toDmy(s.date_issue)), el("td", {}, toDmy(s.date_due)),
    el("td", { class: "num" }, count.format(s.count)), el("td", { class: "num" }, money(s.amount))));
  const uploadRows = state.uploads.map((u) => el("tr", {},
    el("td", {}, when(u.at)), el("td", {}, u.name), el("td", {}, u.summary), el("td", {}, u.created_by)));
  const fdhRows = (state.fdh || []).map((f) => el("tr", {},
    el("td", {}, f.fund || "—"), el("td", {}, fdhBadge(f.status)), el("td", { class: "num" }, count.format(f.n)),
    el("td", {}, `${toDmy((f.first || "").slice(0, 10))} – ${toDmy((f.last || "").slice(0, 10))}`), el("td", {}, when(f.imported_at))));
  const fdhCount = (state.fdh || []).reduce((s, f) => s + f.n, 0);
  const empty = (text) => el("div", { class: "empty" }, ...emptyState(text));
  $("lists").replaceChildren(...tabs([
    { id: "rep", label: "REP ผลตอบรับ", badge: state.reps.length, content: [state.reps.length
      ? table([["เลข REP"], ["วันที่ออก"], ["งวดส่ง (Session)"], ["ส่ง", "num"], ["ผ่าน", "num"], ["ไม่ผ่าน", "num"], ["ยอดผ่าน", "num"], ["ยอดไม่ผ่าน", "num"], ["ไฟล์"]], repRows)
      : empty("ยังไม่ได้นำเข้า REP")] },
    { id: "stm", label: "STM ชดเชย", badge: state.stms.length, content: [state.stms.length
      ? table([["เลข STM"], ["บัญชี"], ["งวด"], ["ช่วงวันที่"], ["วันที่ออก"], ["ครบกำหนด"], ["รายการ", "num"], ["ยอดชดเชย", "num"]], stmRows)
      : empty("ยังไม่ได้นำเข้า STM")] },
    { id: "fdh", label: "FDH รายการเคลม", badge: fdhCount, content: [fdhRows.length
      ? table([["สิทธิ (กองทุน)"], ["สถานะรายการเคลม"], ["รายการ", "num"], ["วันเข้ารับบริการ"], ["นำเข้าล่าสุด"]], fdhRows)
      : empty("ยังไม่ได้นำเข้ารายละเอียดรายการเคลม FDH (…-ClaimDetail.xlsx)")] },
    { id: "uploads", label: "ประวัติการนำเข้า", content: [state.uploads.length
      ? table([["เวลา"], ["ไฟล์"], ["ข้อมูลที่ได้"], ["ผู้นำเข้า"]], uploadRows)
      : empty("ยังไม่มีการนำเข้า")] },
  ]));
}

// ------------------------------------------------------------- drawer
function openDrawer(title, sub, content) {
  $("drawer-title").textContent = title;
  $("drawer-sub").textContent = sub;
  $("drawer-body").replaceChildren(...content);
  $("drawer").hidden = false; $("drawer-backdrop").hidden = false;
}
function closeDrawer() { $("drawer").hidden = true; $("drawer-backdrop").hidden = true; }

// AIPN (account SIGN): the SIGNSUP lines to fix for each failed AN (dispid = AN).
function supBlock(disps, names) {
  return [el("h3", {}, "รายการที่ต้องแก้ไข AIPN (SIGNSUP)"), ...disps.map((d) => el("div", { class: "disp" },
    el("div", { class: "disp-head" }, el("strong", {}, `AN ${d.invno}`), ` · ${d.item_count} รายการ · ยอด ${money(d.charge)} บาท `, codeList(d.codes, names)),
    table([["กลุ่ม"], ["รหัส (LCCode / ICD)"], ["รายการ"], ["จำนวน", "num"], ["ยอด", "num"], ["STDCode"], ["รหัสที่ไม่ผ่าน"]], d.drugs.map((g) => el("tr", {},
      el("td", { class: "mono" }, g.prdcat), el("td", { class: "mono" }, g.hospdrgid), el("td", {}, g.name), el("td", { class: "num" }, g.qty || "—"),
      el("td", { class: "num" }, Number(g.chargeamt) ? money(g.chargeamt) : "—"), el("td", { class: "mono" }, g.drgid || "—"), el("td", {}, codeList(g.codes, names)))))))];
}

function dispBlock(disps, names) {
  if (!disps.length) return [];
  if (disps.every((d) => d.account === "SIGN")) return supBlock(disps, names);
  return [el("h3", {}, "ใบสั่งยาที่ตรวจไม่ผ่าน (SOCDDIS)"), ...disps.map((d) => el("div", { class: "disp" },
    el("div", { class: "disp-head" }, el("strong", {}, `VN ${d.invno}`), ` · Dispense ID ${d.dispid} · ยา ${d.item_count} รายการ · ${money(d.charge)} บาท `, codeList(d.codes, names)),
    table([["รหัสยา"], ["ชื่อยา"], ["จำนวน", "num"], ["ราคา/หน่วย", "num"], ["ยอด", "num"], ["TMT"], ["ผล"]], d.drugs.map((g) => el("tr", {},
      el("td", { class: "mono" }, g.hospdrgid), el("td", {}, g.name), el("td", { class: "num" }, g.qty), el("td", { class: "num" }, money(g.unitprice)),
      el("td", { class: "num" }, money(g.chargeamt)), el("td", { class: "mono" }, g.drgid), el("td", {}, codeList(g.codes, names)))))))];
}

async function openRep(repNo) {
  try {
    const r = await api("/api/repstm/rep", { rep_no: repNo });
    const failed = r.bills.filter((b) => b.stat !== "A");
    const aipn = r.account === "SIGN";
    r.disps.forEach((d) => { d.account = r.account; });
    const rows = [...failed, ...r.bills.filter((b) => b.stat === "A")].map((b) => el("tr", { onclick: () => openVn(b.invno) },
      el("td", { class: "num" }, String(b.line)), el("td", {}, statBadge(b.stat)), el("td", { class: "mono" }, b.invno),
      ...(aipn ? [el("td", { class: "mono" }, b.authcode || "—"), el("td", { class: "num" }, b.amount || "—"), el("td", {}, b.bp), el("td", { class: "mono" }, b.hmain)]
        : [el("td", {}, when(b.dttran)), el("td", {}, b.bp), el("td", { class: "num" }, money(b.claimamt))]),
      el("td", {}, codeList(b.codes, r.code_names, r.c_codes, b.stat !== "A"))));
    openDrawer(`REP ${r.rep_no}`, `ออก ${when(r.issued_at)} · งวดส่ง ${r.session} · สถานี ${r.station}`, [
      info([["ส่ง", r.sent], ["ผ่าน", r.passed], ["ไม่ผ่าน", r.failed]]),
      r.bills.length ? table(aipn ? [["บรรทัด", "num"], ["ผล"], ["AN"], ["DRG"], ["AdjRW", "num"], ["CareAs"], ["HMain"], ["รหัส"]]
        : [["บรรทัด", "num"], ["ผล"], ["VN (InvNo)"], ["วันเวลาบริการ"], ["BP"], ["ยอดเบิก", "num"], ["รหัส"]], rows)
        : el("p", { class: "hint" }, aipn ? "นำเข้าเฉพาะ SIGNSUP ยังไม่มีไฟล์ SIGNREP" : "นำเข้าเฉพาะไฟล์ยา (DIS) ยังไม่มีไฟล์ BIL"),
      ...dispBlock(r.disps, r.code_names),
      el("h3", {}, "ความหมายของรหัสและแนวทางแก้ไข"), codeLegend([...r.bills.map((b) => b.codes), ...r.disps.flatMap((d) => [d.codes, ...d.drugs.map((g) => g.codes)])], r.code_names, r.c_codes) || el("p", { class: "hint" }, "ไม่มีรหัส"),
    ]);
  } catch (err) { notice(err.message); }
}

async function openStm(stmNo) {
  try {
    const s = await api("/api/repstm/stm", { stm_no: stmNo });
    const aipn = s.account === "SIGN";
    const rows = s.bills.map((b) => el("tr", { onclick: () => openVn(b.invno) },
      el("td", { class: "num" }, String(b.seq)), el("td", { class: "mono" }, b.invno), el("td", { class: "mono" }, b.hn),
      ...(aipn ? [el("td", {}, toDmy(b.dttran)), el("td", {}, b.hmain), el("td", { class: "mono" }, b.payplan), el("td", { class: "num" }, b.cfh), el("td", {}, b.care)]
        : [el("td", {}, when(b.dttran)), el("td", {}, b.hmain), el("td", {}, b.bp), el("td", {}, b.care)]),
      el("td", { class: "mono" }, b.rid), el("td", { class: "num" }, money(b.total))));
    const total = s.bills.reduce((sum, b) => sum + Number(b.total || 0), 0);
    openDrawer(`STM ${s.stm_no}`, `งวด ${s.period} · ${toDmy(s.date_start)} – ${toDmy(s.date_end)} · ออก ${toDmy(s.date_issue)} · ครบกำหนด ${toDmy(s.date_due)}`, [
      info([["จำนวนรายการ", count.format(s.count)], ["ยอดชดเชย", `${money(s.amount)} บาท`], ["รวมจากรายการ", `${money(total)} บาท`]]),
      el("div", { class: "search-box stm-filter" }, el("input", { type: "search", placeholder: "กรองจาก VN หรือ HN", "aria-label": "กรองรายการ", oninput: (e) => {
        const q = e.target.value.trim();
        rows.forEach((tr, i) => { tr.hidden = q && !`${s.bills[i].invno} ${s.bills[i].hn}`.includes(q); });
      } })),
      table(aipn ? [["ลำดับ", "num"], ["AN"], ["HN"], ["วันจำหน่าย"], ["Hmain"], ["DRG"], ["AdjRW", "num"], ["CareAs"], ["REP (rid)"], ["ยอดชดเชย", "num"]]
        : [["ลำดับ", "num"], ["VN (InvNo)"], ["HN"], ["วันเวลาบริการ"], ["Hmain"], ["BP"], ["Care"], ["REP (rid)"], ["ยอดชดเชย", "num"]], rows),
    ]);
  } catch (err) { notice(err.message); }
}

async function openVn(vn) {
  try {
    const h = await api("/api/repstm/vn", { vn });
    const repRows = h.reps.map((b) => el("tr", { onclick: () => openRep(b.rep_no) },
      el("td", { class: "mono" }, b.rep_no), el("td", {}, when(b.issued_at)), el("td", { class: "mono" }, b.session),
      el("td", {}, statBadge(b.stat), b.duplicate ? el("span", { class: "sub" }, "ส่งซ้ำ รายการเดิมผ่านแล้ว") : null),
      el("td", { class: "num" }, money(b.claimamt)), el("td", {}, codeList(b.codes, h.code_names, h.c_codes, b.stat !== "A"))));
    const stmRows = h.stms.map((b) => el("tr", { onclick: () => openStm(b.stm_no) },
      el("td", { class: "mono" }, b.stm_no), el("td", { class: "mono" }, b.period), el("td", {}, toDmy(b.date_issue)),
      el("td", { class: "mono" }, b.rid), el("td", { class: "num" }, money(b.total))));
    const status = h.stms.length ? el("span", { class: "badge b-ok" }, `ชดเชยแล้ว ${money(h.stms.reduce((s, b) => s + Number(b.total || 0), 0))} บาท`)
      : h.status_stat ? statBadge(h.status_stat) : el("span", { class: "badge b-sent" }, "ยังไม่มีผลตอบกลับ");
    const isAn = [...h.reps, ...h.stms].some((b) => b.account === "SIGN");
    openDrawer(`${isAn ? "AN" : "VN"} ${vn}`, "ประวัติผลตอบรับและการชดเชย", [
      el("p", {}, "สถานะล่าสุด: ", status),
      el("h3", {}, `REP (${h.reps.length})`),
      h.reps.length ? table([["เลข REP"], ["วันที่ออก"], ["งวดส่ง"], ["ผล"], ["ยอดเบิก", "num"], ["รหัส"]], repRows) : el("p", { class: "hint" }, "ไม่พบใน REP ที่นำเข้า"),
      ...dispBlock(h.disps, h.code_names),
      el("h3", {}, `STM (${h.stms.length})`),
      h.stms.length ? table([["เลข STM"], ["งวด"], ["วันที่ออก"], ["REP (rid)"], ["ยอดชดเชย", "num"]], stmRows) : el("p", { class: "hint" }, "ยังไม่พบใน STM ที่นำเข้า"),
      el("h3", {}, `FDH รายการเคลม (${(h.fdh || []).length})`),
      (h.fdh || []).length ? table([["รหัสการเคลม"], ["สิทธิ"], ["การใช้สิทธิ"], ["วันที่ส่งเคลม"], ["สถานะ"]], h.fdh.map((f) => el("tr", {},
        el("td", { class: "mono" }, f.claim_id), el("td", {}, f.fund), el("td", {}, f.uuc === "2" ? "2 ไม่ใช้สิทธิ" : f.uuc ? "1 ใช้สิทธิ" : "—"),
        el("td", {}, when(f.sent_at)), el("td", {}, fdhBadge(f.status)))))
        : el("p", { class: "hint" }, "ไม่พบในรายละเอียดรายการเคลม FDH ที่นำเข้า"),
      el("h3", {}, "ความหมายของรหัสและแนวทางแก้ไข"), codeLegend([...h.reps.map((b) => b.codes), ...h.disps.flatMap((d) => [d.codes, ...d.drugs.map((g) => g.codes)])], h.code_names, h.c_codes) || el("p", { class: "hint" }, "ไม่มีรหัส"),
    ]);
  } catch (err) { notice(err.message); }
}

// The e-Claim C code lookup (สปสช. C ERROR E-CLAIM NHSO): detail and fix, by code or by a word in either.
async function openCodes(q) {
  try {
    const data = await api(`/api/repstm/c-codes?q=${encodeURIComponent(q)}&limit=600`);
    const rows = data.rows.map((c) => el("li", { class: "issue error" },
      el("div", { class: "issue-title" }, el("span", { class: "badge b-error" }, c.code), " ", c.detail),
      fixBlock(c.fix || "เอกสารไม่ได้ระบุแนวทางแก้ไขของรหัสนี้")));
    openDrawer(q ? `รหัส C: ${q}` : "รหัส C e-Claim ทั้งหมด", `${data.source} · พบ ${count.format(data.rows.length)} จาก ${count.format(data.total)} รหัส`, [
      rows.length ? el("ul", { class: "issues" }, ...rows) : el("p", { class: "hint" }, "ไม่พบรหัสหรือคำค้นนี้ในเอกสาร"),
    ]);
  } catch (err) { notice(err.message); }
}
$("c-search").addEventListener("submit", (e) => { e.preventDefault(); openCodes(new FormData(e.target).get("q").trim()); });
$("c-all").addEventListener("click", () => openCodes(""));

$("vn-search").addEventListener("submit", (e) => {
  e.preventDefault();
  const vn = new FormData(e.target).get("vn").trim();
  if (vn) openVn(vn);
});
$("drawer-close").onclick = closeDrawer;
$("drawer-backdrop").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden) closeDrawer(); });
async function connection() {
  try {
    const status = await api("/api/status");
    if (status.database) setConnection(`HOSxP · ${status.db_host}`, true);
    else setConnection(status.db_error ? `เชื่อมต่อไม่ได้ (${status.db_host})` : "ยังไม่ได้ตั้งค่าฐานข้อมูล", false);
  } catch (err) { setConnection("เชื่อมต่อไม่ได้", false); }
}
setupDrop();
load();
connection();
