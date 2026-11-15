"use strict";
// รายการ PPFS: pick a service type, check the visits, pick items into the list, build the 18-file ZIP for e-Claim.
const state = { rows: [], services: [], selected: new Set(), filter: "all", text: "", sort: { key: "date", dir: "asc" } };
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
  return sortRows(list.map((r) => Object.assign(r, { rank: rank(r) })), state.sort, ["amount", "rank", "days", "count"]);
}

function render() {
  renderCards($("summary"), CATEGORIES, state.rows, state.filter, (r) => r.amount, (key) => { state.filter = key; render(); }, "รายการ");
  const list = visible();
  showSort($("thead"), state.sort);
  $("rows").replaceChildren(...list.map((r) => {
    const box = el("input", { type: "checkbox", checked: state.selected.has(r.key), disabled: !selectable(r),
      title: selectable(r) ? "เลือกส่ง" : "รายการนี้ส่งไม่ได้", "aria-label": `เลือก ${r.vn} ${r.code}`,
      onchange: (e) => { toggle(r.key, e.target.checked); } });
    const problems = [...r.errors, ...r.warnings].map((i) => i.message);
    return el("tr", { class: state.selected.has(r.key) ? "picked" : "" },
      el("td", { class: "check" }, box),
      el("td", {}, resultBadge(r), r.sent ? el("span", { class: "badge b-sent sent-tag", title: `ส่งออกเมื่อ ${thaiDateTime(r.sent)}` }, "ส่งแล้ว") : null),
      el("td", { class: "mono" }, toDmy(r.date)), el("td", { class: "mono" }, r.vn), el("td", { class: "mono" }, r.hn),
      el("td", { class: "name-cell", title: r.ptname }, r.ptname, el("span", { class: "sub" }, r.pttype)),
      el("td", { class: "mono" }, r.labor_date ? toDmy(r.labor_date) : dash()),
      el("td", { class: "num" }, r.days === null ? dash() : String(r.days)),
      el("td", {}, itemLabel(r)),
      el("td", { class: "num" }, r.count ? String(r.count) : dash()),
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
      el("span", { class: "sub" }, `${r.code} ${r.code_name}${r.window_label ? ` · ช่วงวันที่ ${r.window_label}` : ""}${r.count ? ` · ครั้งที่ ${r.count}` : ""}${r.warnings.length ? ` · ⚠ ${r.warnings.map((w) => w.message).join(", ")}` : ""}`)),
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
$("service").addEventListener("change", () => { state.rows = []; state.selected.clear(); renderRules(); render(); });
$("filter").addEventListener("input", (e) => { state.text = e.target.value; render(); });
$("select-ready").onclick = () => { visible().filter((r) => selectable(r) && !r.warnings.length && !r.sent).forEach((r) => state.selected.add(r.key)); render(); };
$("select-none").onclick = () => { state.selected.clear(); render(); };
$("export").onclick = exportZip;
bindSort($("thead"), state.sort, render);
setupPresets($("search"));
(() => { const today = new Date(); $("search").elements.start.value = isoDate(new Date(today.getFullYear(), today.getMonth(), 1)); $("search").elements.end.value = isoDate(today); })();
loadServices();
loadHistory();
connection();
render();
