"use strict";
const state = { visits: [], selected: new Set(), filter: "all", text: "", current: null, fileDate: null, sort: { key: "", dir: "asc" } };

const CATEGORIES = {
  all: ["ทั้งหมด", () => true, "all"],
  ready: ["พร้อมส่ง", (v) => v.selectable && !v.has_warn && !v.has_warn_c && !v.status, "ok"],
  warn: ["มีคำเตือน", (v) => v.selectable && (v.has_warn || v.has_warn_c), "warn"],
  error: ["Error", (v) => v.has_error, "error"],
  special: ["เคสพิเศษ", (v) => v.special, "special"],
  sent: ["ส่งแล้ว", (v) => v.already_sent, "sent"],
};
const LEVEL_CLASS = { error: "b-error", warn: "b-warn", warn_c: "b-c" };

function rank(v) { return v.special ? 4 : v.has_error ? 3 : v.has_warn_c ? 2 : v.has_warn ? 1 : 0; }
function resultBadge(v) {
  if (v.special) return el("span", { class: "badge b-special", title: "ต้องส่งด้วยโปรแกรมเดิม" }, v.special);
  if (v.has_error) return el("span", { class: "badge b-error" }, "Error");
  if (v.has_warn_c) return el("span", { class: "badge b-c" }, "เบิกไม่ได้?");
  if (v.has_warn) return el("span", { class: "badge b-warn" }, "เตือน");
  return el("span", { class: "badge b-ok" }, "ผ่าน");
}
function problemsOf(v) {
  if (v.special) return ["ต้องส่งด้วยโปรแกรมเดิม"];
  return [...(v.result_codes.length ? [`ติด C: ${v.result_codes.map((r) => r.code).join(", ")}`] : []), ...v.issues.map((i) => i.message)];
}

function visible() {
  const q = state.text.toLowerCase();
  const test = CATEGORIES[state.filter][1];
  const list = state.visits.filter((v) => test(v) && (!q || `${v.ptname} ${v.vn} ${v.hn} ${v.pdx}`.toLowerCase().includes(q)));
  return sortRows(list.map((v) => Object.assign(v, { rank: rank(v) })), state.sort, ["send_amount", "rank", "stm_paid"]);
}

function renderChips() {
  renderCards($("summary"), CATEGORIES, state.visits, state.filter, (v) => v.send_amount, (key) => { state.filter = key; render(); });
}

function renderRows() {
  const list = visible();
  showSort($("thead"), state.sort);
  $("rows").replaceChildren(...list.map((v) => {
    const box = el("input", {
      type: "checkbox", checked: state.selected.has(v.vn), disabled: !v.selectable,
      title: v.selectable ? "เลือกส่ง" : "รายการนี้ส่งไม่ได้", "aria-label": `เลือก VN ${v.vn}`,
      onclick: (e) => e.stopPropagation(),
      onchange: (e) => { e.target.checked ? state.selected.add(v.vn) : state.selected.delete(v.vn); renderTotals(); },
    });
    return el("tr", { class: state.current === v.vn ? "current" : "", onclick: () => openDrawer(v) },
      el("td", { class: "check" }, box), el("td", {}, resultBadge(v)),
      el("td", {}, statusCell(v)),
      el("td", { class: "mono" }, v.vn), el("td", { class: "mono" }, v.hn),
      el("td", { class: "name-cell", title: v.ptname }, v.ptname, el("span", { class: "sub" }, v.pttype)),
      el("td", { class: "mono" }, v.visit_at), el("td", {}, v.pdx || el("span", { class: "muted" }, "—")),
      el("td", { class: "num" }, baht.format(Number(v.send_amount)), v.excluded_count ? el("span", { class: "excluded-note" }, `ไม่ส่ง ${v.excluded_count} รายการ`) : ""),
      el("td", { class: "num paid" }, v.stm_paid ? baht.format(Number(v.stm_paid)) : dash()),
      el("td", { class: "problems" }, problemText(problemsOf(v))),
      el("td", { class: "mono doc" }, v.rep_file || dash()),
      el("td", { class: "mono doc" }, v.stm_no || dash()));
  }));
  const empty = $("empty");
  empty.hidden = list.length > 0;
  if (!list.length && state.visits.length) empty.replaceChildren(...emptyState("ไม่มีรายการตามตัวกรองนี้"));
  $("result-count").textContent = state.visits.length ? `แสดง ${count.format(list.length)} จาก ${count.format(state.visits.length)} รายการ` : "";
}

function selectedVisits() { return state.visits.filter((v) => state.selected.has(v.vn)); }
function renderTotals() {
  const list = selectedVisits();
  const total = list.reduce((sum, v) => sum + Number(v.send_amount), 0);
  $("selected-count").textContent = `เลือก ${count.format(list.length)} คน`;
  $("selected-total").textContent = `ค่าใช้จ่าย ${baht.format(total)} บาท`;
  $("export").disabled = list.length === 0;
}
function render() { renderChips(); renderRows(); renderTotals(); }

function statusCell(v) {
  if (!v.status) return el("span", { class: "muted" }, "—");
  const tone = v.status === "ชดเชยแล้ว" ? "b-ok" : v.status === "ติด C" ? "b-c" : "b-sent";
  return el("span", { class: `badge ${tone}` }, v.status);
}
const dash = () => el("span", { class: "muted" }, "—");

function info(pairs) {
  return el("dl", { class: "info-grid" }, ...pairs.map(([k, v]) => el("div", {}, el("dt", {}, k), el("dd", {}, v || "—"))));
}
function simpleTable(headers, rows) {
  return el("table", {}, el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", { class: h.num ? "num" : "" }, h.label || h)))), el("tbody", {}, ...rows));
}

async function openDrawer(v) {
  state.current = v.vn; renderRows();
  $("drawer").hidden = false; $("drawer-backdrop").hidden = false;
  $("drawer-title").textContent = v.ptname || `VN ${v.vn}`;
  $("drawer-sub").textContent = `VN ${v.vn} · HN ${v.hn} · ${v.visit_at}`;
  const body = $("drawer-body");
  body.replaceChildren(el("p", { class: "muted" }, "กำลังโหลด…"));
  try {
    const data = await api("/api/detail", { vn: v.vn });
    if (state.current !== v.vn) return;

    const check = [info([["เลขบัตร", v.cid], ["สิทธิ", v.pttype], ["HospMain", v.hospmain], ["คลินิก", v.clinic], ["PDx", v.pdx], ["เวลาสิ้นสุด", data.end_time]])];
    if (v.result_codes.length) {
      check.push(el("h3", {}, "ผลตอบกลับ: ติด C"));
      check.push(el("ul", { class: "issues" }, ...v.result_codes.map((r) => {
        const related = data.items.filter((item) => r.groups.includes(item.group));
        return el("li", { class: `issue ${r.warning ? "warn" : "error"}` },
          el("div", { class: "issue-title" }, el("span", { class: `badge ${r.warning ? "b-warn" : "b-error"}` }, r.code), " ", r.name),
          el("ul", {}, el("li", {}, `วิธีแก้: ${r.hint}`), ...related.map((item) => el("li", {}, `เกี่ยวข้อง: ${item.icode} ${item.name} ${baht.format(Number(item.sum_price))} บาท`))));
      })));
      check.push(el("p", { class: "hint" }, `แก้แล้วกดค้นหาอีกครั้ง จากนั้นเลือก visit นี้และส่งใหม่ด้วยประเภท ${v.resend_flag === "E" ? "E: แก้ไข (สกส. รับ ID นี้แล้ว)" : "A: ขอเบิก (สกส. ยังไม่เคยรับรายการนี้ ห้ามใช้ E)"} โดยใช้ Session No เลขใหม่`));
    }
    if (v.special || v.issues.length) {
      check.push(el("h3", {}, "ผลตรวจ"));
      const unmapped = data.items.filter((item) => item.tmlt_problem);
      const rows = v.issues.map((i) => el("li", { class: `issue ${i.level}` },
        el("div", { class: "issue-title" }, el("span", { class: `badge ${LEVEL_CLASS[i.level]}` }, { error: "Error", warn: "เตือน", warn_c: "เบิกไม่ได้?" }[i.level]), " ", i.message),
        i.code === "TMLT_INCOMPLETE" && unmapped.length ? el("ul", {}, ...unmapped.map((item) => el("li", {}, `${item.icode} ${item.name}: ${item.tmlt_problem}`))) : null));
      if (v.special) rows.unshift(el("li", { class: "issue special" }, el("div", { class: "issue-title" }, el("span", { class: "badge b-special" }, v.special), " ต้องส่งด้วยโปรแกรมเดิม")));
      check.push(el("ul", { class: "issues" }, ...rows));
      if (v.issues.length) check.push(el("p", { class: "hint" }, "แก้ข้อมูลใน HOSxP แล้วกดค้นหาอีกครั้ง"));
    } else if (!v.result_codes.length) {
      check.push(el("div", { class: "all-good" }, "✓ ผ่านการตรวจทั้งหมด พร้อมส่ง"));
    }

    const dx = [data.dx.length ? simpleTable(["ประเภท", "รหัส", "ชื่อโรค"], data.dx.map((d) => el("tr", {}, el("td", {}, d.type), el("td", { class: "mono" }, d.code), el("td", {}, d.name)))) : el("p", { class: "muted" }, "ไม่มีข้อมูลการวินิจฉัย")];

    const amount = el("div", { class: "amount-bar" });
    const showAmount = () => amount.replaceChildren(
      el("div", {}, el("span", {}, "ยอดที่จะส่ง "), el("b", {}, baht.format(Number(v.send_amount)))),
      el("div", {}, el("span", {}, "ยอดเบิก "), el("b", {}, baht.format(Number(v.claim_amount)))),
      el("div", {}, el("span", {}, `ไม่ส่ง ${v.excluded_count} รายการ`)));
    showAmount();
    const costs = [amount, el("p", { class: "muted-note" }, "ติ๊กช่อง \"ไม่ส่ง\" เพื่อเอารายการออกจากไฟล์ ยอดจะคำนวณใหม่ทันที (ไม่แก้ข้อมูลใน HOSxP)"),
      simpleTable(["ไม่ส่ง", "หมวด", "รายการ", { label: "จำนวน", num: true }, { label: "รวม", num: true }], data.items.map((item) => {
        const row = el("tr", { class: `${item.excluded ? "excluded" : ""} ${item.tmlt_problem ? "flagged" : ""}`.trim() });
        const override = el("div", { class: "muted-note", hidden: !item.hosxp_excluded || item.excluded }, "HOSxP กำหนดไม่ส่ง แต่โปรแกรมนี้จะส่งรายการนี้");
        row.append(
          el("td", { class: "check" }, el("input", { type: "checkbox", checked: item.excluded, "aria-label": `ไม่ส่ง ${item.name}`,
            onchange: async (e) => {
              try {
                Object.assign(v, await api("/api/exclusion", { vn: v.vn, icode: item.icode, excluded: e.target.checked }));
                row.classList.toggle("excluded", e.target.checked); override.hidden = !item.hosxp_excluded || e.target.checked;
                showAmount(); render();
              } catch (err) { e.target.checked = !e.target.checked; notice(err.message); }
            } })),
          el("td", {}, item.group),
          el("td", {}, item.name, override, item.tmlt_problem ? el("div", { class: "flag-note" }, `Map TMLT: ${item.tmlt_problem}`) : "",
            item.default_usage ? el("div", { class: "muted-note" }, `ไม่มีวิธีใช้ใน HOSxP จะส่งเป็น "${item.default_usage}"`) : ""),
          el("td", { class: "num" }, item.qty), el("td", { class: "num" }, baht.format(Number(item.sum_price))));
        return row;
      }))];

    const problems = v.issues.length + v.result_codes.length + (v.special ? 1 : 0);
    body.replaceChildren(...tabs([
      { id: "check", label: "สรุปและผลตรวจ", badge: problems || undefined, alert: v.has_error || v.special || v.result_codes.length > 0, content: check },
      { id: "dx", label: "การวินิจฉัย", badge: data.dx.length, content: dx },
      { id: "cost", label: "ค่าใช้จ่าย", badge: data.items.length, content: costs },
    ]));
  } catch (err) { body.replaceChildren(el("p", {}, err.message)); }
}
function closeDrawer() { $("drawer").hidden = true; $("drawer-backdrop").hidden = true; state.current = null; renderRows(); }

async function runExport() {
  clearNotices();
  const station = $("station").value.trim();
  if (!/^\d{1,2}$/.test(station) || Number(station) === 0) { notice("กรุณาระบุ Station เป็นตัวเลข 01-99"); $("station").focus(); return; }
  try { localStorage.setItem("ssop.station", station.padStart(2, "0")); } catch (e) { /* not saved: fine */ }
  const session = $("session").value.trim();
  if (!/^\d{1,4}$/.test(session) || Number(session) === 0) { notice("กรุณาระบุ Session No เป็นตัวเลข 1-4 หลัก"); $("session").focus(); return; }
  const list = selectedVisits();
  const flag = $("tflag").value;
  const warned = list.filter((v) => v.has_warn || v.has_warn_c).length;
  const total = list.reduce((sum, v) => sum + Number(v.send_amount), 0);
  const summary = el("dl", { class: "sum" },
    el("dt", {}, "จำนวน"), el("dd", {}, `${list.length} คน`), el("dt", {}, "ค่าใช้จ่ายรวม"), el("dd", {}, `${baht.format(total)} บาท`),
    el("dt", {}, "มีคำเตือน"), el("dd", {}, `${warned} คน`), el("dt", {}, "Station"), el("dd", {}, station.padStart(2, "0")), el("dt", {}, "Session"), el("dd", {}, session.padStart(4, "0")),
    el("dt", {}, "ประเภท"), el("dd", {}, $("tflag").selectedOptions[0].textContent));
  if (!await confirmDialog("ยืนยันการสร้าง ZIP", [summary], "สร้าง ZIP")) return;
  const payload = { vns: list.map((v) => v.vn), session, station, tflag: flag, file_date: state.fileDate };
  try {
    let result;
    try { result = await api("/api/export", payload); }
    catch (err) {
      if (!err.data || !err.data.needs_confirm) throw err;
      const again = el("p", {}, `มี ${err.data.resend.length} รายการที่เคยส่งแล้ว ต้องการส่งซ้ำแบบ ${flag} ใช่หรือไม่?`);
      if (!await confirmDialog("ยืนยันการส่งซ้ำ", [again], "ส่งซ้ำ")) return;
      result = await api("/api/export", { ...payload, confirm_resend: true });
    }
    const box = notice(`สร้าง ${result.file} แล้ว: ${result.bill_count} คน ยอดเบิก ${baht.format(Number(result.claim_total))} บาท`, "ok", true);
    box.append(el("a", { href: `/api/exports/${result.id}/download` }, "ดาวน์โหลด ZIP"));
    state.selected.clear();
    await Promise.all([search(), loadHistory()]);
  } catch (err) {
    const detail = err.data && err.data.blocked ? `: ${err.data.blocked.map((b) => `${b.vn} (${b.reason})`).join(", ")}` : (err.data && err.data.resend ? `: ${err.data.resend.join(", ")}` : "");
    notice(err.message + detail);
  }
}

async function search() {
  const form = new FormData($("search"));
  const pttypes = [...document.querySelectorAll("#pttype-list input.one:checked")].map((i) => i.value);
  const body = { start: form.get("start"), end: form.get("end"), vn: form.get("vn").trim(), hn: form.get("hn").trim(), pttypes };
  $("empty").hidden = false; $("empty").replaceChildren(...emptyState("กำลังค้นหาและตรวจข้อมูล…", "…"));
  $("rows").replaceChildren();
  try {
    const data = await api("/api/search", body);
    state.visits = data.visits; state.fileDate = data.file_date;
    state.selected = new Set(data.visits.filter((v) => v.default_selected).map((v) => v.vn));
    $("empty").replaceChildren(...emptyState("ไม่พบรายการในช่วงนี้"));
    if (!data.z_ready) notice("ไม่มีตารางรหัส Z ที่เบิกได้ โปรแกรมจึงยังไม่ได้ตรวจ PDx รหัส Z นำเข้าไฟล์ได้ที่ส่วนประวัติการส่งออก", "warn");
    render();
  } catch (err) { $("empty").replaceChildren(...emptyState(err.message, "!")); }
}

async function loadHistory() {
  try {
    const rows = await api("/api/exports");
    $("history").replaceChildren(rows.length ? el("table", {},
      el("thead", {}, el("tr", {}, ...["เวลา", "Session", "ประเภท", "จำนวน", "ยอดเบิก", "ไฟล์", "ผู้สร้าง"].map((t) => el("th", {}, t)))),
      el("tbody", {}, ...rows.map((r) => el("tr", {}, el("td", {}, r.created_at.replace("T", " ")), el("td", {}, r.session), el("td", {}, r.tflag), el("td", {}, String(r.bill_count)),
        el("td", {}, baht.format(Number(r.claim_total))), el("td", {}, el("a", { href: `/api/exports/${r.id}/download` }, r.file)), el("td", {}, r.created_by || "-"))))) : el("p", {}, "ยังไม่มีประวัติ"));
  } catch (err) { $("history").replaceChildren(el("p", {}, err.message)); }
}

async function start() {
  const today = new Date();
  $("search").elements.start.value = isoDate(new Date(today.getFullYear(), today.getMonth(), 1));
  $("search").elements.end.value = isoDate(today);
  setupPresets($("search"));
  bindSort($("thead"), state.sort, renderRows);
  try {
    const status = await api("/api/status");
    for (const problem of status.settings_problems) notice(problem);
    const dbState = status.database;
    if (status.db_error) {
      setConnection(`เชื่อมต่อไม่ได้ (${status.db_host})`, false);
      notice(status.db_error);
      $("pttype-label").textContent = "โหลดสิทธิไม่ได้";
      loadHistory();
      return;
    }
    if (!dbState) { setConnection("ยังไม่ได้ตั้งค่าฐานข้อมูล", false); return; }
    setConnection(`${dbState.ok ? "HOSxP" : "ฐานข้อมูลไม่ครบ"} · ${status.db_host}`, dbState.ok);
    if (dbState.missing_tables.length) notice(`ไม่พบตาราง: ${dbState.missing_tables.join(", ")}`);
    if (dbState.missing_columns.length) notice(`ไม่พบคอลัมน์: ${dbState.missing_columns.join(", ")}`);
    if (dbState.optional_missing.some((name) => name.endsWith(".chi"))) notice("ไม่พบตาราง chi ตรวจการส่งซ้ำได้เฉพาะรายการที่ส่งออกจากโปรแกรมนี้ กรุณาตรวจ RCM_DB", "warn", true);
    if (dbState.user_can_write) notice("user ฐานข้อมูลนี้มีสิทธิ์เขียน ควรใช้ user ที่มีแค่ SELECT", "warn", true);
    const pttypes = await api("/api/pttypes");
    fillPttypes(pttypes);
  } catch (err) { setConnection("เชื่อมต่อไม่ได้", false); notice(err.message); $("pttype-label").textContent = "โหลดสิทธิไม่ได้"; }
  loadHistory();
}

try { const saved = localStorage.getItem("ssop.station"); if (saved) $("station").value = saved; } catch (e) { /* use the .env default */ }
$("station").addEventListener("blur", (e) => { const v = e.target.value.trim(); if (/^\d$/.test(v) && v !== "0") e.target.value = v.padStart(2, "0"); });
$("search").addEventListener("submit", (e) => { e.preventDefault(); clearNotices(); search(); });
$("filter").addEventListener("input", (e) => { state.text = e.target.value; renderRows(); });
$("select-ready").onclick = () => { state.selected = new Set(state.visits.filter((v) => v.default_selected).map((v) => v.vn)); render(); };
$("select-none").onclick = () => { state.selected.clear(); render(); };
$("export").onclick = runExport;
$("drawer-close").onclick = closeDrawer;
$("drawer-backdrop").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden && !$("confirm").open) closeDrawer(); });
$("zfile").addEventListener("change", async (e) => {
  const file = e.target.files[0]; if (!file) return;
  try {
    const response = await fetch("/api/zcodes", { method: "POST", body: file });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail);
    notice(`นำเข้ารหัส Z ${data.count} รหัสแล้ว กดค้นหาอีกครั้งเพื่อตรวจใหม่`, "ok");
  } catch (err) { notice(err.message); }
  e.target.value = "";
});
start();
