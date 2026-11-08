"use strict";
const state = { rows: [], selected: new Set(), filter: "all", text: "", current: null };
const CARE_AS = { M: "M: Main", B: "B: suB", A: "A: suprA", X: "X: eXt" };

const CATEGORIES = {
  all: ["ทั้งหมด", () => true],
  ready: ["พร้อมส่ง", (r) => r.selectable && !r.has_warn && !r.status],
  warn: ["มีคำเตือน", (r) => r.selectable && r.has_warn],
  error: ["Error", (r) => r.has_error],
  special: ["เคสพิเศษ", (r) => r.special],
  sent: ["ส่งแล้ว/ติด C", (r) => r.status],
};

function resultBadge(r) {
  if (r.special) return el("span", { class: "badge b-special", title: "ต้องส่งด้วยโปรแกรมเดิม" }, r.special);
  if (r.has_error) return el("span", { class: "badge b-error" }, "Error");
  if (r.has_warn) return el("span", { class: "badge b-warn" }, "เตือน");
  return el("span", { class: "badge b-ok" }, "ผ่าน");
}

function visible() {
  const q = state.text.toLowerCase();
  const test = CATEGORIES[state.filter][1];
  return state.rows.filter((r) => test(r) && (!q || `${r.ptname} ${r.an} ${r.hn} ${r.pdx}`.toLowerCase().includes(q)));
}

function renderChips() {
  $("summary").replaceChildren(...Object.entries(CATEGORIES).map(([key, [label, test]]) =>
    el("button", { class: "chip", type: "button", "aria-pressed": String(state.filter === key), onclick: () => { state.filter = key; render(); } },
      label, el("b", {}, String(state.rows.filter(test).length)))));
}

async function setCareAs(r, value) {
  try {
    await api("/api/aipn/careas", { an: r.an, care_as: value === r.care_as_default ? "" : value });
    r.care_as = value;
  } catch (err) { notice(err.message); renderRows(); }
}

function renderRows() {
  const list = visible();
  $("rows").replaceChildren(...list.map((r) => {
    const box = el("input", {
      type: "checkbox", checked: state.selected.has(r.an), disabled: !r.selectable,
      title: r.selectable ? "" : "รายการนี้ส่งไม่ได้", "aria-label": `เลือก AN ${r.an}`,
      onclick: (e) => e.stopPropagation(),
      onchange: (e) => { e.target.checked ? state.selected.add(r.an) : state.selected.delete(r.an); renderTotals(); },
    });
    const care = el("select", { class: "care-as", "aria-label": `CareAs ของ AN ${r.an}`, onclick: (e) => e.stopPropagation(), onchange: (e) => setCareAs(r, e.target.value) },
      ...Object.entries(CARE_AS).map(([code, label]) => el("option", { value: code, selected: r.care_as === code }, code === r.care_as_default ? `${label} (ค่าเริ่มต้น)` : label)));
    const problems = r.special ? "ต้องส่งด้วยโปรแกรมเดิม" : r.issues.map((i) => i.message).join(", ");
    return el("tr", { class: state.current === r.an ? "current" : "", onclick: () => openDrawer(r) },
      el("td", {}, box), el("td", {}, resultBadge(r)),
      el("td", {}, r.status ? el("span", { class: "badge b-sent" }, r.status) : ""),
      el("td", {}, r.an), el("td", {}, r.hn), el("td", {}, r.ptname), el("td", {}, r.discharge_at),
      el("td", {}, r.pdx), el("td", {}, r.pay_plan), el("td", {}, care),
      el("td", { class: "num" }, baht.format(Number(r.send_amount)), r.excluded_count ? el("div", { class: "muted-note" }, `ไม่ส่ง ${r.excluded_count} รายการ`) : ""),
      el("td", { class: "problems" }, problems));
  }));
  $("empty").hidden = list.length > 0;
  if (!list.length && state.rows.length) $("empty").textContent = "ไม่มีรายการตามตัวกรองนี้";
}

function selectedRows() { return state.rows.filter((r) => state.selected.has(r.an)); }
function renderTotals() {
  const list = selectedRows();
  const total = list.reduce((sum, r) => sum + Number(r.send_amount), 0);
  $("selected-count").textContent = `เลือก ${list.length.toLocaleString("th-TH")} คน`;
  $("selected-total").textContent = `ยอดส่ง ${baht.format(total)} บาท`;
  $("export").disabled = list.length === 0;
}
function render() { renderChips(); renderRows(); renderTotals(); }

function table(headers, rows) {
  return el("table", {}, el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", {}, h)))), el("tbody", {}, ...rows));
}

async function openDrawer(r) {
  state.current = r.an; renderRows();
  $("drawer").hidden = false;
  $("drawer-title").textContent = `AN ${r.an}`;
  const body = $("drawer-body");
  body.replaceChildren(el("p", {}, "กำลังโหลด…"));
  try {
    const data = await api("/api/aipn/detail", { an: r.an });
    if (state.current !== r.an) return;
    const parts = [el("dl", { class: "sum" },
      el("dt", {}, "ชื่อ"), el("dd", {}, r.ptname), el("dt", {}, "HN"), el("dd", {}, r.hn), el("dt", {}, "เลขบัตร"), el("dd", {}, r.cid),
      el("dt", {}, "สิทธิ"), el("dd", {}, r.pttype), el("dt", {}, "Admit"), el("dd", {}, r.admit_at), el("dt", {}, "จำหน่าย"), el("dd", {}, r.discharge_at),
      el("dt", {}, "HospMain ที่จะส่ง"), el("dd", {}, r.hmain || "(ว่าง)"), el("dt", {}, "AuthCode"), el("dd", {}, r.auth_code || "(ไม่มี)"),
      el("dt", {}, "UPayPlan"), el("dd", {}, r.pay_plan))];
    if (r.special || r.issues.length) {
      parts.push(el("h3", {}, "ผลตรวจ"));
      const items = r.issues.map((i) => el("li", {}, el("span", { class: `badge ${i.level === "error" ? "b-error" : "b-warn"}` }, i.message),
        ...i.items.map((name) => el("div", { class: "flag-note" }, name))));
      if (r.special) items.unshift(el("li", {}, el("span", { class: "badge b-special" }, `${r.special}: ต้องส่งด้วยโปรแกรมเดิม`)));
      parts.push(el("ul", { class: "issues" }, ...items));
      if (r.issues.length) parts.push(el("p", {}, "แก้ข้อมูลใน HOSxP แล้วกดค้นหาอีกครั้ง"));
    }
    parts.push(el("h3", {}, "การวินิจฉัย"));
    parts.push(data.dx.length ? table(["ประเภท", "รหัส", "ชื่อโรค"], data.dx.map((d) => el("tr", {}, el("td", {}, d.type), el("td", {}, d.code), el("td", {}, d.name)))) : el("p", {}, "ไม่มีข้อมูล"));
    parts.push(el("h3", {}, "หัตถการที่จะส่ง"));
    parts.push(data.ops.length ? el("ol", { class: "op-lines" }, ...data.ops.map((o) => el("li", {}, o.line.split("|").slice(2).join(" | ")))) : el("p", {}, "ไม่มี"));
    parts.push(el("h3", {}, "รายการค่าใช้จ่าย (ติ๊กเพื่อไม่ส่งรายการนั้น)"));
    const amount = el("p", { class: "send-amount" });
    const showAmount = () => { amount.textContent = `ยอดส่ง ${baht.format(Number(r.send_amount))} บาท DRGCharge ${baht.format(Number(r.drg_charge))} บาท (ไม่ส่ง ${r.excluded_count} รายการ)`; };
    showAmount();
    parts.push(amount);
    if (data.skipped.length) parts.push(el("p", { class: "muted-note" }, `ไม่ส่งตามกฎเดิม (ไม่มีชื่อหรือชื่อขึ้นต้น "ยา."): ${data.skipped.join(", ")}`));
    const setPrice = async (item, value) => {
      try {
        Object.assign(r, await api("/api/aipn/price", { an: r.an, item: item.key, unitprice: value }));
        if (!r.selectable) state.selected.delete(r.an);
        render(); await openDrawer(r);
      } catch (err) { notice(err.message); await openDrawer(r); }
    };
    const priceCell = (item) => {
      const input = el("input", { class: "price-input", type: "text", inputmode: "decimal", value: item.unitprice,
        "aria-label": `ราคาต่อหน่วยที่จะส่งของ ${item.name}`,
        onchange: (e) => setPrice(item, e.target.value.trim()),
        onkeydown: (e) => { if (e.key === "Enter") e.target.blur(); } });
      const notes = [];
      if (item.chi_price !== null) {
        notes.push(el("div", { class: item.price_wrong ? "flag-note" : "muted-note" }, `สกส. ${baht.format(Number(item.chi_price))}`));
        if (item.price_wrong) notes.push(el("button", { type: "button", class: "small", onclick: () => setPrice(item, item.chi_price) }, "ใช้ราคา สกส."));
      }
      if (item.price_edited) {
        notes.push(el("div", { class: "muted-note" }, `HOSxP ${baht.format(Number(item.hosxp_unitprice))}`));
        notes.push(el("button", { type: "button", class: "small", onclick: () => setPrice(item, null) }, "กลับไปใช้ราคา HOSxP"));
      }
      return el("td", { class: "num" }, input, ...notes);
    };
    parts.push(el("p", { class: "muted-note" }, "แก้ราคาต่อหน่วยได้ในช่องราคา ค่าที่แก้ใช้เฉพาะไฟล์ที่ส่งจากโปรแกรมนี้ และไม่แก้ราคาใน HOSxP"));
    parts.push(table(["ไม่ส่ง", "หมวด", "รายการ", "รหัสมาตรฐาน", "จำนวน", "ราคา/หน่วย", "รวม"], data.items.map((item) => {
      const row = el("tr", { class: `${item.excluded ? "excluded" : ""} ${item.price_wrong && !item.excluded ? "flagged" : ""}`.trim() });
      const override = el("div", { class: "muted-note", hidden: !item.hosxp_excluded || item.excluded }, "HOSxP กำหนดไม่ส่ง แต่โปรแกรมนี้จะส่งรายการนี้");
      row.append(
        el("td", {}, el("input", { type: "checkbox", checked: item.excluded, "aria-label": `ไม่ส่ง ${item.name}`,
          onchange: async (e) => {
            try {
              Object.assign(r, await api("/api/aipn/item", { an: r.an, item: item.key, excluded: e.target.checked }));
              row.classList.toggle("excluded", e.target.checked); override.hidden = !item.hosxp_excluded || e.target.checked;
              if (!r.selectable) state.selected.delete(r.an);
              showAmount(); render();
            } catch (err) { e.target.checked = !e.target.checked; notice(err.message); }
          } })),
        el("td", {}, `${item.billgroup}${item.claimcat === "D" ? "" : ` (${item.claimcat})`}`),
        el("td", {}, item.name, override), el("td", {}, item.std), el("td", { class: "num" }, item.qty), priceCell(item), el("td", { class: "num" }, baht.format(Number(item.amount))));
      return row;
    })));
    body.replaceChildren(...parts);
  } catch (err) { body.replaceChildren(el("p", {}, err.message)); }
}
function closeDrawer() { $("drawer").hidden = true; state.current = null; renderRows(); }

async function runExport() {
  clearNotices();
  const session = $("session").value.trim();
  if (!/^\d{5}$/.test(session) || Number(session) < 10000) { notice("กรุณาระบุ Session No เป็นตัวเลข 5 หลัก ตั้งแต่ 10000"); $("session").focus(); return; }
  const list = selectedRows();
  const appeal = $("appeal").value;
  const total = list.reduce((sum, r) => sum + Number(r.send_amount), 0);
  const summary = el("dl", { class: "sum" },
    el("dt", {}, "จำนวน"), el("dd", {}, `${list.length} คน`), el("dt", {}, "ยอดส่งรวม"), el("dd", {}, `${baht.format(total)} บาท`),
    el("dt", {}, "มีคำเตือน"), el("dd", {}, `${list.filter((r) => r.has_warn).length} คน`),
    el("dt", {}, "ไฟล์"), el("dd", {}, `AIPN${session}.ZIP`), el("dt", {}, "ประเภท"), el("dd", {}, $("appeal").selectedOptions[0].textContent));
  if (!await confirmDialog("ยืนยันการสร้าง ZIP", [summary], "สร้าง ZIP")) return;
  const payload = { ans: list.map((r) => r.an), session, appeal };
  try {
    let result;
    try { result = await api("/api/aipn/export", payload); }
    catch (err) {
      if (!err.data || !err.data.needs_confirm) throw err;
      const again = el("p", {}, `มี ${err.data.resend.length} AN ที่เคยส่งแล้วหรือติด C (${err.data.resend.join(", ")}) ต้องการส่งซ้ำใช่หรือไม่?`);
      if (!await confirmDialog("ยืนยันการส่งซ้ำ", [again], "ส่งซ้ำ")) return;
      result = await api("/api/aipn/export", { ...payload, confirm_resend: true });
    }
    const box = notice(`สร้าง ${result.file} แล้ว: ${result.count} คน ยอดส่ง ${baht.format(Number(result.total))} บาท `, "ok", true);
    box.append(el("a", { href: `/api/aipn/exports/${result.id}/download` }, "ดาวน์โหลด ZIP"));
    state.selected.clear();
    $("session").value = "";
    await Promise.all([search(), loadHistory()]);
  } catch (err) {
    const detail = err.data && err.data.blocked ? `: ${err.data.blocked.map((b) => `${b.an} (${b.reason})`).join(", ")}` : "";
    notice(err.message + detail);
  }
}

async function search() {
  const form = new FormData($("search"));
  const pttypes = [...document.querySelectorAll("#pttype-list input:checked")].map((i) => i.value);
  const body = { start: form.get("start"), end: form.get("end"), an: form.get("an").trim(), pttypes };
  $("empty").hidden = false; $("empty").textContent = "กำลังค้นหาและตรวจข้อมูล…";
  $("rows").replaceChildren();
  try {
    const data = await api("/api/aipn/search", body);
    state.rows = data.admissions;
    state.selected = new Set(data.admissions.filter((r) => r.default_selected).map((r) => r.an));
    if (!$("session").value && data.next_session) $("session").value = data.next_session;
    $("empty").textContent = "ไม่พบรายการ";
    render();
  } catch (err) { $("empty").textContent = err.message; }
}

async function loadHistory() {
  try {
    const rows = await api("/api/aipn/exports");
    $("history").replaceChildren(rows.length ? table(["เวลา", "Session", "ประเภท", "จำนวน", "ยอดส่ง", "ไฟล์", "ผู้สร้าง"],
      rows.map((r) => el("tr", {}, el("td", {}, r.created_at.replace("T", " ")), el("td", {}, r.session), el("td", {}, r.appeal || "ปกติ"),
        el("td", {}, String(r.an_count)), el("td", {}, baht.format(Number(r.total))), el("td", {}, el("a", { href: `/api/aipn/exports/${r.id}/download` }, r.file)), el("td", {}, r.created_by || "-"))))
      : el("p", {}, "ยังไม่มีประวัติ"));
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
  $("search").elements.start.value = iso(new Date(today.getFullYear(), today.getMonth() - 1, 1));
  $("search").elements.end.value = iso(today);
  try {
    const status = await api("/api/status");
    for (const problem of status.settings_problems) notice(problem);
    if (status.db_error) { $("connection").textContent = `เชื่อมต่อไม่ได้ (${status.db_host})`; notice(status.db_error); $("pttype-label").textContent = "โหลดสิทธิไม่ได้"; loadHistory(); return; }
    if (!status.database) { $("connection").textContent = "ยังไม่ได้ตั้งค่าฐานข้อมูล"; return; }
    $("connection").textContent = `เชื่อมต่อ HOSxP แล้ว (${status.db_host})`;
    if (status.database.user_can_write) notice("user ฐานข้อมูลนี้มีสิทธิ์เขียน ควรใช้ user ที่มีแค่ SELECT", "warn", true);
    const pttypes = await api("/api/aipn/pttypes");
    $("pttype-list").replaceChildren(...pttypes.map((p) => el("label", {}, el("input", { type: "checkbox", value: p.pttype, checked: true, onchange: updatePttypeLabel }), `${p.pttype}: ${p.name}`)));
    updatePttypeLabel();
  } catch (err) { $("connection").textContent = "เชื่อมต่อไม่ได้"; notice(err.message); $("pttype-label").textContent = "โหลดสิทธิไม่ได้"; }
  loadHistory();
}

$("search").addEventListener("submit", (e) => { e.preventDefault(); clearNotices(); search(); });
$("filter").addEventListener("input", (e) => { state.text = e.target.value; renderRows(); });
$("select-ready").onclick = () => { state.selected = new Set(state.rows.filter((r) => r.default_selected).map((r) => r.an)); render(); };
$("select-none").onclick = () => { state.selected.clear(); render(); };
$("export").onclick = runExport;
$("drawer-close").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden && !$("confirm").open) closeDrawer(); });
start();
