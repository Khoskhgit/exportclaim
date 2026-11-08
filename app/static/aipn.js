"use strict";
const state = { rows: [], selected: new Set(), filter: "all", text: "", current: null, sort: { key: "", dir: "asc" } };
const CARE_AS = { M: "M: Main", B: "B: suB", A: "A: suprA", X: "X: eXt" };

const CATEGORIES = {
  all: ["ทั้งหมด", () => true, "all"],
  ready: ["พร้อมส่ง", (r) => r.selectable && !r.has_warn && !r.status, "ok"],
  warn: ["มีคำเตือน", (r) => r.selectable && r.has_warn, "warn"],
  error: ["Error", (r) => r.has_error, "error"],
  special: ["เคสพิเศษ", (r) => r.special, "special"],
  sent: ["ส่งแล้ว/ติด C", (r) => r.status, "sent"],
};

function rank(r) { return r.special ? 3 : r.has_error ? 2 : r.has_warn ? 1 : 0; }
function resultBadge(r) {
  if (r.special) return el("span", { class: "badge b-special", title: "ต้องส่งด้วยโปรแกรมเดิม" }, r.special);
  if (r.has_error) return el("span", { class: "badge b-error" }, "Error");
  if (r.has_warn) return el("span", { class: "badge b-warn" }, "เตือน");
  return el("span", { class: "badge b-ok" }, "ผ่าน");
}

function visible() {
  const q = state.text.toLowerCase();
  const test = CATEGORIES[state.filter][1];
  const list = state.rows.filter((r) => test(r) && (!q || `${r.ptname} ${r.an} ${r.hn} ${r.pdx}`.toLowerCase().includes(q)));
  return sortRows(list.map((r) => Object.assign(r, { rank: rank(r) })), state.sort, ["send_amount", "rank"]);
}

function renderChips() {
  renderCards($("summary"), CATEGORIES, state.rows, state.filter, (r) => r.send_amount, (key) => { state.filter = key; render(); });
}

async function setCareAs(r, value) {
  try {
    await api("/api/aipn/careas", { an: r.an, care_as: value === r.care_as_default ? "" : value });
    r.care_as = value;
    notice(`AN ${r.an} ใช้ CareAs ${value}`, "ok");
  } catch (err) { notice(err.message); renderRows(); }
}

function renderRows() {
  const list = visible();
  showSort($("thead"), state.sort);
  $("rows").replaceChildren(...list.map((r) => {
    const box = el("input", {
      type: "checkbox", checked: state.selected.has(r.an), disabled: !r.selectable,
      title: r.selectable ? "เลือกส่ง" : "รายการนี้ส่งไม่ได้", "aria-label": `เลือก AN ${r.an}`,
      onclick: (e) => e.stopPropagation(),
      onchange: (e) => { e.target.checked ? state.selected.add(r.an) : state.selected.delete(r.an); renderTotals(); },
    });
    const care = el("select", { class: "care-as", "aria-label": `CareAs ของ AN ${r.an}`, onclick: (e) => e.stopPropagation(), onchange: (e) => setCareAs(r, e.target.value) },
      ...Object.entries(CARE_AS).map(([code, label]) => el("option", { value: code, selected: r.care_as === code }, code === r.care_as_default ? `${label} ★` : label)));
    const problems = r.special ? ["ต้องส่งด้วยโปรแกรมเดิม"] : r.issues.map((i) => i.message);
    return el("tr", { class: state.current === r.an ? "current" : "", onclick: () => openDrawer(r) },
      el("td", { class: "check" }, box), el("td", {}, resultBadge(r)),
      el("td", {}, r.status ? el("span", { class: "badge b-sent" }, r.status) : el("span", { class: "muted" }, "—")),
      el("td", { class: "mono" }, r.an), el("td", { class: "mono" }, r.hn),
      el("td", { class: "name-cell", title: r.ptname }, r.ptname, el("span", { class: "sub" }, r.pttype)),
      el("td", { class: "mono" }, r.discharge_at), el("td", {}, r.pdx || el("span", { class: "muted" }, "—")), el("td", {}, r.pay_plan), el("td", {}, care),
      el("td", { class: "num" }, baht.format(Number(r.send_amount)), r.excluded_count ? el("span", { class: "excluded-note" }, `ไม่ส่ง ${r.excluded_count} รายการ`) : ""),
      el("td", { class: "problems" }, problemText(problems)));
  }));
  $("empty").hidden = list.length > 0;
  if (!list.length && state.rows.length) $("empty").replaceChildren(...emptyState("ไม่มีรายการตามตัวกรองนี้"));
  $("result-count").textContent = state.rows.length ? `แสดง ${count.format(list.length)} จาก ${count.format(state.rows.length)} รายการ` : "";
}

function selectedRows() { return state.rows.filter((r) => state.selected.has(r.an)); }
function renderTotals() {
  const list = selectedRows();
  const total = list.reduce((sum, r) => sum + Number(r.send_amount), 0);
  $("selected-count").textContent = `เลือก ${count.format(list.length)} คน`;
  $("selected-total").textContent = `ยอดส่ง ${baht.format(total)} บาท`;
  $("export").disabled = list.length === 0;
}
function render() { renderChips(); renderRows(); renderTotals(); }

function table(headers, rows) {
  return el("table", {}, el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", { class: h.num ? "num" : "" }, h.label || h)))), el("tbody", {}, ...rows));
}
function info(pairs) {
  return el("dl", { class: "info-grid" }, ...pairs.map(([k, v]) => el("div", {}, el("dt", {}, k), el("dd", {}, v || "—"))));
}

async function openDrawer(r) {
  state.current = r.an; renderRows();
  $("drawer").hidden = false; $("drawer-backdrop").hidden = false;
  $("drawer-title").textContent = r.ptname || `AN ${r.an}`;
  $("drawer-sub").textContent = `AN ${r.an} · HN ${r.hn} · จำหน่าย ${r.discharge_at}`;
  const body = $("drawer-body");
  if (!body.children.length || body.dataset.an !== r.an) body.replaceChildren(el("p", { class: "muted" }, "กำลังโหลด…"));
  body.dataset.an = r.an;
  try {
    const data = await api("/api/aipn/detail", { an: r.an });
    if (state.current !== r.an) return;

    const check = [info([["เลขบัตร", r.cid], ["สิทธิ", r.pttype], ["Admit", r.admit_at], ["จำหน่าย", r.discharge_at],
      ["HospMain ที่จะส่ง", r.hmain || "(ว่าง)"], ["AuthCode", r.auth_code || "(ไม่มี)"], ["UPayPlan", r.pay_plan], ["CareAs", r.care_as]])];
    if (r.special || r.issues.length) {
      check.push(el("h3", {}, "ผลตรวจ"));
      const items = r.issues.map((i) => el("li", { class: `issue ${i.level}` },
        el("div", { class: "issue-title" }, el("span", { class: `badge ${i.level === "error" ? "b-error" : "b-warn"}` }, i.level === "error" ? "Error" : "เตือน"), " ", i.message),
        i.items.length ? el("ul", {}, ...i.items.map((name) => el("li", {}, name))) : null));
      if (r.special) items.unshift(el("li", { class: "issue special" }, el("div", { class: "issue-title" }, el("span", { class: "badge b-special" }, r.special), " ต้องส่งด้วยโปรแกรมเดิม")));
      check.push(el("ul", { class: "issues" }, ...items));
      if (r.issues.some((i) => i.code === "UNITPRICE_WRONG")) check.push(el("p", { class: "hint" }, "ราคาไม่ตรง สกส.: ไปที่แท็บ \"ค่าใช้จ่าย\" แล้วกด \"ใช้ราคา สกส.\" หรือแก้ราคาเอง"));
      if (r.issues.some((i) => i.level === "error")) check.push(el("p", { class: "hint" }, "Error: แก้ข้อมูลใน HOSxP หรือติ๊กไม่ส่งรายการที่มีปัญหา แล้วกดค้นหาอีกครั้ง"));
    } else {
      check.push(el("div", { class: "all-good" }, "✓ ผ่านการตรวจทั้งหมด พร้อมส่ง"));
    }

    const dx = [data.dx.length ? table(["ประเภท", "รหัส", "ชื่อโรค"], data.dx.map((d) => el("tr", {}, el("td", {}, d.type), el("td", { class: "mono" }, d.code), el("td", {}, d.name)))) : el("p", { class: "muted" }, "ไม่มีข้อมูลการวินิจฉัย")];
    const ops = [data.ops.length ? table(["รหัส", "ชื่อหัตถการ", "แพทย์", "เริ่ม", "สิ้นสุด"], data.ops.map((o) => {
      const f = o.line.split("|");
      return el("tr", {}, el("td", { class: "mono" }, f[2]), el("td", {}, f[3]), el("td", {}, f[4]), el("td", { class: "mono" }, f[5].replace("T", " ")), el("td", { class: "mono" }, f[6].replace("T", " ")));
    })) : el("p", { class: "muted" }, "ไม่มีหัตถการ")];

    const amount = el("div", { class: "amount-bar" });
    const showAmount = () => amount.replaceChildren(
      el("div", {}, el("span", {}, "ยอดส่ง "), el("b", {}, baht.format(Number(r.send_amount)))),
      el("div", {}, el("span", {}, "DRGCharge "), el("b", {}, baht.format(Number(r.drg_charge)))),
      el("div", {}, el("span", {}, `ไม่ส่ง ${r.excluded_count} รายการ`)));
    showAmount();
    const setPrice = async (item, value) => {
      try {
        Object.assign(r, await api("/api/aipn/price", { an: r.an, item: item.key, unitprice: value }));
        if (!r.selectable) state.selected.delete(r.an);
        render(); await openDrawer(r);
        notice(value === null ? `${item.name}: กลับไปใช้ราคา HOSxP` : `${item.name}: ใช้ราคา ${baht.format(Number(value))} บาท`, "ok");
      } catch (err) { notice(err.message); await openDrawer(r); }
    };
    const priceCell = (item) => {
      const input = el("input", { class: "price-input", type: "text", inputmode: "decimal", value: item.unitprice,
        "aria-label": `ราคาต่อหน่วยที่จะส่งของ ${item.name}`,
        onchange: (e) => setPrice(item, e.target.value.trim()),
        onkeydown: (e) => { if (e.key === "Enter") e.target.blur(); } });
      const notes = [];
      if (item.chi_price !== null) {
        notes.push(el("div", { class: "price-note" }, el("span", { class: item.price_wrong ? "flag-note" : "muted-note" }, `สกส. ${baht.format(Number(item.chi_price))}`),
          item.price_wrong ? el("button", { type: "button", class: "small", onclick: () => setPrice(item, item.chi_price) }, "ใช้ราคา สกส.") : null));
      }
      if (item.price_edited) {
        notes.push(el("div", { class: "price-note" }, el("span", { class: "muted-note" }, `HOSxP ${baht.format(Number(item.hosxp_unitprice))}`),
          el("button", { type: "button", class: "small ghost", onclick: () => setPrice(item, null) }, "คืนค่า")));
      }
      return el("td", { class: "num" }, input, ...notes);
    };
    const costs = [amount,
      el("p", { class: "muted-note" }, "ติ๊ก \"ไม่ส่ง\" เพื่อเอารายการออก หรือแก้ราคาต่อหน่วยในช่องราคา ค่าที่แก้ใช้เฉพาะไฟล์ที่ส่งจากโปรแกรมนี้ ไม่แก้ข้อมูลใน HOSxP"),
      data.skipped.length ? el("p", { class: "muted-note" }, `ไม่ส่งตามกฎเดิม (ไม่มีชื่อหรือชื่อขึ้นต้น "ยา."): ${data.skipped.join(", ")}`) : null,
      table(["ไม่ส่ง", "หมวด", "รายการ", "รหัสมาตรฐาน", { label: "จำนวน", num: true }, { label: "ราคา/หน่วย", num: true }, { label: "รวม", num: true }], data.items.map((item) => {
        const row = el("tr", { class: `${item.excluded ? "excluded" : ""} ${item.price_wrong && !item.excluded ? "flagged" : ""}`.trim() });
        const override = el("div", { class: "muted-note", hidden: !item.hosxp_excluded || item.excluded }, "HOSxP กำหนดไม่ส่ง แต่โปรแกรมนี้จะส่งรายการนี้");
        row.append(
          el("td", { class: "check" }, el("input", { type: "checkbox", checked: item.excluded, "aria-label": `ไม่ส่ง ${item.name}`,
            onchange: async (e) => {
              try {
                Object.assign(r, await api("/api/aipn/item", { an: r.an, item: item.key, excluded: e.target.checked }));
                row.classList.toggle("excluded", e.target.checked); override.hidden = !item.hosxp_excluded || e.target.checked;
                if (!r.selectable) state.selected.delete(r.an);
                showAmount(); render();
              } catch (err) { e.target.checked = !e.target.checked; notice(err.message); }
            } })),
          el("td", {}, item.billgroup, item.claimcat === "D" ? "" : el("span", { class: "sub" }, `ClaimCat ${item.claimcat}`)),
          el("td", {}, item.name, override), el("td", { class: "mono" }, item.std), el("td", { class: "num" }, item.qty), priceCell(item),
          el("td", { class: "num" }, baht.format(Number(item.amount))));
        return row;
      }))];

    const flagged = data.items.filter((i) => i.price_wrong && !i.excluded).length;
    body.replaceChildren(...tabs([
      { id: "check", label: "สรุปและผลตรวจ", badge: r.issues.length + (r.special ? 1 : 0) || undefined, alert: r.has_error || !!r.special, content: check },
      { id: "dx", label: "การวินิจฉัย", badge: data.dx.length, content: dx },
      { id: "ops", label: "หัตถการ", badge: data.ops.length, content: ops },
      { id: "cost", label: "ค่าใช้จ่าย", badge: flagged ? `${flagged} ราคาไม่ตรง` : data.items.length, alert: flagged > 0, content: costs },
    ]));
  } catch (err) { body.replaceChildren(el("p", {}, err.message)); }
}
function closeDrawer() { $("drawer").hidden = true; $("drawer-backdrop").hidden = true; state.current = null; renderRows(); }

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
    const box = notice(`สร้าง ${result.file} แล้ว: ${result.count} คน ยอดส่ง ${baht.format(Number(result.total))} บาท`, "ok", true);
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
  const pttypes = [...document.querySelectorAll("#pttype-list input.one:checked")].map((i) => i.value);
  const body = { start: form.get("start"), end: form.get("end"), an: form.get("an").trim(), pttypes };
  $("empty").hidden = false; $("empty").replaceChildren(...emptyState("กำลังค้นหาและตรวจข้อมูล…", "…"));
  $("rows").replaceChildren();
  try {
    const data = await api("/api/aipn/search", body);
    state.rows = data.admissions;
    state.selected = new Set(data.admissions.filter((r) => r.default_selected).map((r) => r.an));
    if (!$("session").value && data.next_session) $("session").value = data.next_session;
    $("empty").replaceChildren(...emptyState("ไม่พบรายการในช่วงนี้"));
    render();
  } catch (err) { $("empty").replaceChildren(...emptyState(err.message, "!")); }
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

async function start() {
  const today = new Date();
  $("search").elements.start.value = isoDate(new Date(today.getFullYear(), today.getMonth() - 1, 1));
  $("search").elements.end.value = isoDate(today);
  setupPresets($("search"));
  bindSort($("thead"), state.sort, renderRows);
  try {
    const status = await api("/api/status");
    for (const problem of status.settings_problems) notice(problem);
    if (status.db_error) { setConnection(`เชื่อมต่อไม่ได้ (${status.db_host})`, false); notice(status.db_error); $("pttype-label").textContent = "โหลดสิทธิไม่ได้"; loadHistory(); return; }
    if (!status.database) { setConnection("ยังไม่ได้ตั้งค่าฐานข้อมูล", false); return; }
    setConnection(`HOSxP · ${status.db_host}`, true);
    if (status.database.user_can_write) notice("user ฐานข้อมูลนี้มีสิทธิ์เขียน ควรใช้ user ที่มีแค่ SELECT", "warn", true);
    const pttypes = await api("/api/aipn/pttypes");
    fillPttypes(pttypes);
  } catch (err) { setConnection("เชื่อมต่อไม่ได้", false); notice(err.message); $("pttype-label").textContent = "โหลดสิทธิไม่ได้"; }
  loadHistory();
}

$("search").addEventListener("submit", (e) => { e.preventDefault(); clearNotices(); search(); });
$("filter").addEventListener("input", (e) => { state.text = e.target.value; renderRows(); });
$("select-ready").onclick = () => { state.selected = new Set(state.rows.filter((r) => r.default_selected).map((r) => r.an)); render(); };
$("select-none").onclick = () => { state.selected.clear(); render(); };
$("export").onclick = runExport;
$("drawer-close").onclick = closeDrawer;
$("drawer-backdrop").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden && !$("confirm").open) closeDrawer(); });
start();
