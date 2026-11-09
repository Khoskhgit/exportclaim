"use strict";
const state = { reps: [], stms: [], uploads: [] };

const money = (v) => baht.format(Number(v || 0));
const when = (iso) => {
  if (!iso) return "";
  const [d, t] = String(iso).split("T");
  return t ? `${toDmy(d)} ${t.slice(0, 5)}` : toDmy(d);
};
const statBadge = (stat) => stat === "A"
  ? el("span", { class: "badge b-ok" }, "A ผ่าน")
  : el("span", { class: "badge b-error" }, `${stat || "?"} ไม่ผ่าน`);

function codeList(codes, names) {
  if (!codes) return el("span", { class: "muted" }, "-");
  return el("span", { class: "code-list" }, ...codes.split(",").map((code) => el("span", {
    class: `badge ${code.startsWith("W") ? "b-warn" : "b-error"}`, title: names[code] || "",
  }, code)));
}

function codeLegend(codes, names) {
  const used = [...new Set(codes.flatMap((c) => (c || "").split(",")).filter(Boolean))].sort();
  if (!used.length) return null;
  return el("ul", { class: "issues" }, ...used.map((code) => el("li", { class: `issue ${code.startsWith("W") ? "warn" : "error"}` },
    el("div", { class: "issue-title" }, el("span", { class: `badge ${code.startsWith("W") ? "b-warn" : "b-error"}` }, code), " ", names[code] || "ไม่มีคำอธิบายในไฟล์ REP"))));
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
    el("td", {}, [r.has_bil ? "BIL" : "", r.has_dis ? "DIS" : ""].filter(Boolean).join(" + "))));
  const stmRows = state.stms.map((s) => el("tr", { onclick: () => openStm(s.stm_no) },
    el("td", { class: "mono" }, s.stm_no), el("td", {}, s.account), el("td", { class: "mono" }, s.period),
    el("td", {}, `${toDmy(s.date_start)} – ${toDmy(s.date_end)}`), el("td", {}, toDmy(s.date_issue)), el("td", {}, toDmy(s.date_due)),
    el("td", { class: "num" }, count.format(s.count)), el("td", { class: "num" }, money(s.amount))));
  const uploadRows = state.uploads.map((u) => el("tr", {},
    el("td", {}, when(u.at)), el("td", {}, u.name), el("td", {}, u.summary), el("td", {}, u.created_by)));
  const empty = (text) => el("div", { class: "empty" }, ...emptyState(text));
  $("lists").replaceChildren(...tabs([
    { id: "rep", label: "REP ผลตอบรับ", badge: state.reps.length, content: [state.reps.length
      ? table([["เลข REP"], ["วันที่ออก"], ["งวดส่ง (Session)"], ["ส่ง", "num"], ["ผ่าน", "num"], ["ไม่ผ่าน", "num"], ["ยอดผ่าน", "num"], ["ยอดไม่ผ่าน", "num"], ["ไฟล์"]], repRows)
      : empty("ยังไม่ได้นำเข้า REP")] },
    { id: "stm", label: "STM ชดเชย", badge: state.stms.length, content: [state.stms.length
      ? table([["เลข STM"], ["บัญชี"], ["งวด"], ["ช่วงวันที่"], ["วันที่ออก"], ["ครบกำหนด"], ["รายการ", "num"], ["ยอดชดเชย", "num"]], stmRows)
      : empty("ยังไม่ได้นำเข้า STM")] },
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

function dispBlock(disps, names) {
  if (!disps.length) return [];
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
    const rows = [...failed, ...r.bills.filter((b) => b.stat === "A")].map((b) => el("tr", { onclick: () => openVn(b.invno) },
      el("td", { class: "num" }, String(b.line)), el("td", {}, statBadge(b.stat)), el("td", { class: "mono" }, b.invno),
      el("td", {}, when(b.dttran)), el("td", {}, b.bp), el("td", { class: "num" }, money(b.claimamt)), el("td", {}, codeList(b.codes, r.code_names))));
    openDrawer(`REP ${r.rep_no}`, `ออก ${when(r.issued_at)} · งวดส่ง ${r.session} · สถานี ${r.station}`, [
      info([["ส่ง", r.sent], ["ผ่าน", r.passed], ["ไม่ผ่าน", r.failed]]),
      r.bills.length ? table([["บรรทัด", "num"], ["ผล"], ["VN (InvNo)"], ["วันเวลาบริการ"], ["BP"], ["ยอดเบิก", "num"], ["รหัส"]], rows) : el("p", { class: "hint" }, "นำเข้าเฉพาะไฟล์ยา (DIS) ยังไม่มีไฟล์ BIL"),
      ...dispBlock(r.disps, r.code_names),
      el("h3", {}, "ความหมายของรหัส"), codeLegend([...r.bills.map((b) => b.codes), ...r.disps.flatMap((d) => [d.codes, ...d.drugs.map((g) => g.codes)])], r.code_names) || el("p", { class: "hint" }, "ไม่มีรหัส"),
    ]);
  } catch (err) { notice(err.message); }
}

async function openStm(stmNo) {
  try {
    const s = await api("/api/repstm/stm", { stm_no: stmNo });
    const rows = s.bills.map((b) => el("tr", { onclick: () => openVn(b.invno) },
      el("td", { class: "num" }, String(b.seq)), el("td", { class: "mono" }, b.invno), el("td", { class: "mono" }, b.hn),
      el("td", {}, when(b.dttran)), el("td", {}, b.hmain), el("td", {}, b.bp), el("td", {}, b.care),
      el("td", { class: "mono" }, b.rid), el("td", { class: "num" }, money(b.total))));
    const total = s.bills.reduce((sum, b) => sum + Number(b.total || 0), 0);
    openDrawer(`STM ${s.stm_no}`, `งวด ${s.period} · ${toDmy(s.date_start)} – ${toDmy(s.date_end)} · ออก ${toDmy(s.date_issue)} · ครบกำหนด ${toDmy(s.date_due)}`, [
      info([["จำนวนรายการ", count.format(s.count)], ["ยอดชดเชย", `${money(s.amount)} บาท`], ["รวมจากรายการ", `${money(total)} บาท`]]),
      el("div", { class: "search-box stm-filter" }, el("input", { type: "search", placeholder: "กรองจาก VN หรือ HN", "aria-label": "กรองรายการ", oninput: (e) => {
        const q = e.target.value.trim();
        rows.forEach((tr, i) => { tr.hidden = q && !`${s.bills[i].invno} ${s.bills[i].hn}`.includes(q); });
      } })),
      table([["ลำดับ", "num"], ["VN (InvNo)"], ["HN"], ["วันเวลาบริการ"], ["Hmain"], ["BP"], ["Care"], ["REP (rid)"], ["ยอดชดเชย", "num"]], rows),
    ]);
  } catch (err) { notice(err.message); }
}

async function openVn(vn) {
  try {
    const h = await api("/api/repstm/vn", { vn });
    const repRows = h.reps.map((b) => el("tr", { onclick: () => openRep(b.rep_no) },
      el("td", { class: "mono" }, b.rep_no), el("td", {}, when(b.issued_at)), el("td", { class: "mono" }, b.session),
      el("td", {}, statBadge(b.stat)), el("td", { class: "num" }, money(b.claimamt)), el("td", {}, codeList(b.codes, h.code_names))));
    const stmRows = h.stms.map((b) => el("tr", { onclick: () => openStm(b.stm_no) },
      el("td", { class: "mono" }, b.stm_no), el("td", { class: "mono" }, b.period), el("td", {}, toDmy(b.date_issue)),
      el("td", { class: "mono" }, b.rid), el("td", { class: "num" }, money(b.total))));
    const last = h.reps[h.reps.length - 1];
    const status = h.stms.length ? el("span", { class: "badge b-ok" }, `ชดเชยแล้ว ${money(h.stms.reduce((s, b) => s + Number(b.total || 0), 0))} บาท`)
      : last ? statBadge(last.stat) : el("span", { class: "badge b-sent" }, "ยังไม่มีผลตอบกลับ");
    openDrawer(`VN ${vn}`, "ประวัติผลตอบรับและการชดเชย", [
      el("p", {}, "สถานะล่าสุด: ", status),
      el("h3", {}, `REP (${h.reps.length})`),
      h.reps.length ? table([["เลข REP"], ["วันที่ออก"], ["งวดส่ง"], ["ผล"], ["ยอดเบิก", "num"], ["รหัส"]], repRows) : el("p", { class: "hint" }, "ไม่พบใน REP ที่นำเข้า"),
      ...dispBlock(h.disps, h.code_names),
      el("h3", {}, `STM (${h.stms.length})`),
      h.stms.length ? table([["เลข STM"], ["งวด"], ["วันที่ออก"], ["REP (rid)"], ["ยอดชดเชย", "num"]], stmRows) : el("p", { class: "hint" }, "ยังไม่พบใน STM ที่นำเข้า"),
      el("h3", {}, "ความหมายของรหัส"), codeLegend([...h.reps.map((b) => b.codes), ...h.disps.flatMap((d) => [d.codes, ...d.drugs.map((g) => g.codes)])], h.code_names) || el("p", { class: "hint" }, "ไม่มีรหัส"),
    ]);
  } catch (err) { notice(err.message); }
}

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
