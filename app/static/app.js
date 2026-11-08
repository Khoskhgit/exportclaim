"use strict";
const state = { visits: [], selected: new Set(), filter: "all", text: "", current: null, fileDate: null };

const CATEGORIES = {
  all: ["ทั้งหมด", () => true],
  ready: ["พร้อมส่ง", (v) => v.selectable && !v.has_warn && !v.has_warn_c && !v.status],
  warn: ["มีคำเตือน", (v) => v.selectable && (v.has_warn || v.has_warn_c)],
  error: ["Error", (v) => v.has_error],
  special: ["เคสพิเศษ", (v) => v.special],
  sent: ["ส่งแล้ว", (v) => v.already_sent],
};

function resultBadge(v) {
  if (v.special) return el("span", { class: "badge b-special", title: "ต้องส่งด้วยโปรแกรมเดิม" }, v.special);
  if (v.has_error) return el("span", { class: "badge b-error" }, "Error");
  if (v.has_warn_c) return el("span", { class: "badge b-c" }, "เบิกไม่ได้?");
  if (v.has_warn) return el("span", { class: "badge b-warn" }, "เตือน");
  return el("span", { class: "badge b-ok" }, "ผ่าน");
}

function visible() {
  const q = state.text.toLowerCase();
  const test = CATEGORIES[state.filter][1];
  return state.visits.filter((v) => test(v) && (!q || `${v.ptname} ${v.vn} ${v.hn} ${v.pdx}`.toLowerCase().includes(q)));
}

function renderChips() {
  const chips = Object.entries(CATEGORIES).map(([key, [label, test]]) => {
    const count = state.visits.filter(test).length;
    return el("button", { class: "chip", type: "button", "aria-pressed": String(state.filter === key), onclick: () => { state.filter = key; render(); } }, label, el("b", {}, String(count)));
  });
  $("summary").replaceChildren(...chips);
}

function renderRows() {
  const list = visible();
  $("rows").replaceChildren(...list.map((v) => {
    const box = el("input", {
      type: "checkbox", checked: state.selected.has(v.vn), disabled: !v.selectable,
      title: v.selectable ? "" : "รายการนี้ส่งไม่ได้", "aria-label": `เลือก VN ${v.vn}`,
      onclick: (e) => e.stopPropagation(),
      onchange: (e) => { e.target.checked ? state.selected.add(v.vn) : state.selected.delete(v.vn); renderTotals(); },
    });
    const problems = v.special ? `ต้องส่งด้วยโปรแกรมเดิม` : [
      ...(v.result_codes.length ? [`ติด C: ${v.result_codes.map((r) => r.code).join(", ")}`] : []),
      ...v.issues.map((i) => i.message),
    ].join(", ");
    return el("tr", { class: state.current === v.vn ? "current" : "", onclick: () => openDrawer(v) },
      el("td", {}, box), el("td", {}, resultBadge(v)),
      el("td", {}, v.status ? el("span", { class: "badge b-sent" }, v.status) : ""),
      el("td", {}, v.vn), el("td", {}, v.hn), el("td", {}, v.ptname), el("td", {}, v.visit_at),
      el("td", {}, v.pdx), el("td", { class: "num" }, baht.format(Number(v.send_amount)), v.excluded_count ? el("div", { class: "muted-note" }, `ไม่ส่ง ${v.excluded_count} รายการ`) : ""),
      el("td", { class: "problems" }, problems));
  }));
  const empty = $("empty");
  empty.hidden = list.length > 0;
  if (!list.length && state.visits.length) empty.textContent = "ไม่มีรายการตามตัวกรองนี้";
}

function selectedVisits() { return state.visits.filter((v) => state.selected.has(v.vn)); }
function renderTotals() {
  const list = selectedVisits();
  const total = list.reduce((sum, v) => sum + Number(v.send_amount), 0);
  $("selected-count").textContent = `เลือก ${list.length.toLocaleString("th-TH")} คน`;
  $("selected-total").textContent = `ค่าใช้จ่าย ${baht.format(total)} บาท`;
  $("export").disabled = list.length === 0;
}
function render() { renderChips(); renderRows(); renderTotals(); }

async function openDrawer(v) {
  state.current = v.vn; renderRows();
  $("drawer").hidden = false;
  $("drawer-title").textContent = `VN ${v.vn}`;
  const body = $("drawer-body");
  body.replaceChildren(el("p", {}, "กำลังโหลด…"));
  try {
    const data = await api("/api/detail", { vn: v.vn });
    if (state.current !== v.vn) return;
    const parts = [
      el("dl", { class: "sum" },
        el("dt", {}, "ชื่อ"), el("dd", {}, v.ptname), el("dt", {}, "HN"), el("dd", {}, v.hn),
        el("dt", {}, "เลขบัตร"), el("dd", {}, v.cid), el("dt", {}, "สิทธิ"), el("dd", {}, v.pttype),
        el("dt", {}, "HospMain"), el("dd", {}, v.hospmain), el("dt", {}, "คลินิก"), el("dd", {}, v.clinic),
        el("dt", {}, "เวลาสิ้นสุด"), el("dd", {}, data.end_time)),
    ];
    if (v.result_codes.length) {
      parts.push(el("h3", {}, "ผลตอบกลับ: ติด C"));
      parts.push(el("ul", { class: "result-codes" }, ...v.result_codes.map((r) => {
        const related = data.items.filter((item) => r.groups.includes(item.group));
        return el("li", {},
          el("span", { class: `badge ${r.warning ? "b-warn" : "b-error"}` }, r.code),
          r.warning ? " เตือน" : " สาเหตุที่ต้องแก้",
          el("div", {}, r.name),
          el("div", { class: "muted-note" }, `วิธีแก้: ${r.hint}`),
          ...related.map((item) => el("div", { class: "flag-note" }, `รายการที่เกี่ยวข้อง: ${item.icode} ${item.name} ${baht.format(Number(item.sum_price))} บาท`)));
      })));
      parts.push(el("p", {}, "แก้แล้วกดค้นหาอีกครั้ง จากนั้นเลือก visit นี้และส่งใหม่ด้วยประเภท E: แก้ไข โดยใช้ Session No เลขใหม่"));
    }
    if (v.special || v.issues.length) {
      parts.push(el("h3", {}, "ผลตรวจ"));
      const rows = v.issues.map((i) => el("li", {}, el("span", { class: `badge ${{ error: "b-error", warn: "b-warn", warn_c: "b-c" }[i.level]}` }, i.message)));
      if (v.special) rows.unshift(el("li", {}, el("span", { class: "badge b-special" }, `${v.special}: ต้องส่งด้วยโปรแกรมเดิม`)));
      parts.push(el("ul", { class: "issues" }, ...rows));
      const unmapped = data.items.filter((item) => item.tmlt_problem);
      if (unmapped.length) {
        parts.push(el("p", {}, "รายการที่ Map TMLT ยังไม่ครบ:"));
        parts.push(el("ul", { class: "flagged-items" }, ...unmapped.map((item) => el("li", {}, `${item.icode} ${item.name}: ${item.tmlt_problem}`))));
      }
      if (v.issues.length) parts.push(el("p", {}, "แก้ข้อมูลใน HOSxP แล้วกดค้นหาอีกครั้ง"));
    }
    parts.push(el("h3", {}, "การวินิจฉัย"));
    parts.push(data.dx.length ? el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "ประเภท"), el("th", {}, "รหัส"), el("th", {}, "ชื่อโรค"))),
      el("tbody", {}, ...data.dx.map((d) => el("tr", {}, el("td", {}, d.type), el("td", {}, d.code), el("td", {}, d.name))))) : el("p", {}, "ไม่มีข้อมูล"));
    parts.push(el("h3", {}, "รายการค่าใช้จ่าย (ติ๊กเพื่อไม่ส่งรายการนั้น)"));
    const amount = el("p", { class: "send-amount" });
    const showAmount = () => { amount.textContent = `ยอดที่จะส่ง ${baht.format(Number(v.send_amount))} บาท ยอดเบิก ${baht.format(Number(v.claim_amount))} บาท (ไม่ส่ง ${v.excluded_count} รายการ)`; };
    showAmount();
    parts.push(amount);
    parts.push(el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "ไม่ส่ง"), el("th", {}, "หมวด"), el("th", {}, "รายการ"), el("th", { class: "num" }, "จำนวน"), el("th", { class: "num" }, "รวม"))),
      el("tbody", {}, ...data.items.map((item) => {
        const row = el("tr", { class: `${item.excluded ? "excluded" : ""} ${item.tmlt_problem ? "flagged" : ""}`.trim() });
        const override = el("div", { class: "muted-note", hidden: !item.hosxp_excluded || item.excluded }, "HOSxP กำหนดไม่ส่ง แต่โปรแกรมนี้จะส่งรายการนี้");
        row.append(
          el("td", {}, el("input", { type: "checkbox", checked: item.excluded, "aria-label": `ไม่ส่ง ${item.name}`,
            onchange: async (e) => {
              try {
                Object.assign(v, await api("/api/exclusion", { vn: v.vn, icode: item.icode, excluded: e.target.checked }));
                row.classList.toggle("excluded", e.target.checked); override.hidden = !item.hosxp_excluded || e.target.checked;
                showAmount(); renderRows(); renderTotals();
              }
              catch (err) { e.target.checked = !e.target.checked; notice(err.message); }
            } })),
          el("td", {}, item.group), el("td", {}, item.name, override, item.tmlt_problem ? el("div", { class: "flag-note" }, `Map TMLT: ${item.tmlt_problem}`) : "", item.default_usage ? el("div", { class: "muted-note" }, `ไม่มีวิธีใช้ใน HOSxP จะส่งเป็น "${item.default_usage}"`) : ""), el("td", { class: "num" }, item.qty), el("td", { class: "num" }, baht.format(Number(item.sum_price))));
        return row;
      }))));
    body.replaceChildren(...parts);
  } catch (err) { body.replaceChildren(el("p", {}, err.message)); }
}
function closeDrawer() { $("drawer").hidden = true; state.current = null; renderRows(); }

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
    const box = notice(`สร้าง ${result.file} แล้ว: ${result.bill_count} คน ยอดเบิก ${baht.format(Number(result.claim_total))} บาท `, "ok");
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
  const pttypes = [...document.querySelectorAll("#pttype-list input:checked")].map((i) => i.value);
  const body = { start: form.get("start"), end: form.get("end"), vn: form.get("vn").trim(), hn: form.get("hn").trim(), pttypes };
  $("empty").hidden = false; $("empty").textContent = "กำลังค้นหาและตรวจข้อมูล…";
  $("rows").replaceChildren();
  try {
    const data = await api("/api/search", body);
    state.visits = data.visits; state.fileDate = data.file_date;
    state.selected = new Set(data.visits.filter((v) => v.default_selected).map((v) => v.vn));
    $("empty").textContent = "ไม่พบรายการ";
    if (!data.z_ready) notice("ไม่มีตารางรหัส Z ที่เบิกได้ โปรแกรมจึงยังไม่ได้ตรวจ PDx รหัส Z นำเข้าไฟล์ได้ที่ส่วนประวัติการส่งออก", "warn");
    render();
  } catch (err) { $("empty").textContent = err.message; }
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

function updatePttypeLabel() {
  const total = document.querySelectorAll("#pttype-list input").length;
  const chosen = document.querySelectorAll("#pttype-list input:checked").length;
  $("pttype-label").textContent = `เลือกแล้ว ${chosen} จาก ${total} สิทธิ`;
}

async function start() {
  const today = new Date();
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  $("search").elements.start.value = iso(new Date(today.getFullYear(), today.getMonth(), 1));
  $("search").elements.end.value = iso(today);
  try {
    const status = await api("/api/status");
    for (const problem of status.settings_problems) notice(problem);
    const dbState = status.database;
    if (status.db_error) {
      $("connection").textContent = `เชื่อมต่อไม่ได้ (${status.db_host})`;
      notice(status.db_error);
      $("pttype-label").textContent = "โหลดสิทธิไม่ได้";
      loadHistory();
      return;
    }
    if (!dbState) { $("connection").textContent = "ยังไม่ได้ตั้งค่าฐานข้อมูล"; return; }
    $("connection").textContent = `${dbState.ok ? "เชื่อมต่อ HOSxP แล้ว" : "ฐานข้อมูลไม่ครบ"} (${status.db_host})`;
    if (dbState.missing_tables.length) notice(`ไม่พบตาราง: ${dbState.missing_tables.join(", ")}`);
    if (dbState.missing_columns.length) notice(`ไม่พบคอลัมน์: ${dbState.missing_columns.join(", ")}`);
    if (dbState.optional_missing.some((name) => name.endsWith(".chi"))) notice("ไม่พบตาราง chi ตรวจการส่งซ้ำได้เฉพาะรายการที่ส่งออกจากโปรแกรมนี้ กรุณาตรวจ RCM_DB", "warn", true);
    if (dbState.user_can_write) notice("user ฐานข้อมูลนี้มีสิทธิ์เขียน ควรใช้ user ที่มีแค่ SELECT", "warn", true);
    const pttypes = await api("/api/pttypes");
    $("pttype-list").replaceChildren(...pttypes.map((p) => el("label", {}, el("input", { type: "checkbox", value: p.pttype, checked: true, onchange: updatePttypeLabel }), `${p.pttype}: ${p.name}`)));
    updatePttypeLabel();
  } catch (err) { $("connection").textContent = "เชื่อมต่อไม่ได้"; notice(err.message); $("pttype-label").textContent = "โหลดสิทธิไม่ได้"; }
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
