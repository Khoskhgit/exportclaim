"use strict";
// The PPFS dashboard: one row per fund (service type), asked from the server one fund at a time.

const COLS = ["all", "sent", "passed", "c", "deny", "waiting"];
const state = { results: [], run: 0 };

const num = (part) => Number(part.amount || 0);
const cell = (part, cls = "") => el("td", { class: `num ${cls}` },
  part.n ? el("span", {}, count.format(part.n), el("span", { class: "sub" }, `${baht.format(num(part))} บาท`)) : "—");
const money = (value, cls = "") => el("td", { class: `num ${cls}` }, Number(value) ? baht.format(Number(value)) : "—");

function codesCell(codes) {
  if (!codes.length) return el("td", {}, "—");
  return el("td", { class: "dash-codes" }, ...codes.slice(0, 4).map((c) =>
    el("span", { class: "code-chip", title: c.detail || "" }, `${c.code} ×${count.format(c.n)}`)),
    codes.length > 4 ? el("span", { class: "sub" }, `อีก ${codes.length - 4} รหัส`) : null);
}

function row(r) {
  if (r.failed) return el("tr", {}, el("td", {}, r.label), el("td", { colspan: 11, class: "problems" }, r.failed));
  if (!r.all) return el("tr", { class: "loading-row" }, el("td", {}, r.label), el("td", { colspan: 11, class: "muted" }, r.pending ? "กำลังสรุป…" : "รอคิว"));
  return el("tr", {}, el("td", {}, el("a", { href: "/ppfs" }, r.label), el("span", { class: "sub" }, `${count.format(r.visits)} visit`)),
    cell(r.all), cell(r.sent), cell(r.passed, "ok"), cell(r.c, "warn"), cell(r.deny, "bad"), cell(r.waiting),
    money(r.paid, "ok"), money(r.stm_paid), cell(r.ready), cell(r.error, "bad"), codesCell(r.codes));
}

function totals() {
  const done = state.results.filter((r) => r.all);
  const sum = (key) => ({ n: done.reduce((s, r) => s + r[key].n, 0), amount: done.reduce((s, r) => s + num(r[key]), 0) });
  const t = Object.fromEntries([...COLS, "ready", "error"].map((k) => [k, sum(k)]));
  t.paid = done.reduce((s, r) => s + Number(r.paid), 0);
  t.stm_paid = done.reduce((s, r) => s + Number(r.stm_paid), 0);
  return t;
}

function render() {
  $("rows").replaceChildren(...state.results.map(row));
  $("empty").hidden = state.results.length > 0;
  const t = totals();
  const ready = state.results.filter((r) => r.all).length;
  $("foot").replaceChildren(ready ? el("tr", { class: "total-row" }, el("td", {}, "รวมทุกกองทุน"),
    cell(t.all), cell(t.sent), cell(t.passed, "ok"), cell(t.c, "warn"), cell(t.deny, "bad"), cell(t.waiting),
    money(t.paid, "ok"), money(t.stm_paid), cell(t.ready), cell(t.error, "bad"), el("td", {})) : "");
  const card = (label, part, tone, amount) => el("div", { class: `card tone-${tone}` },
    el("span", { class: "card-label" }, label),
    el("span", { class: "card-count" }, count.format(part.n), el("small", {}, " รายการ")),
    el("span", { class: "card-amount" }, `${baht.format(amount ?? part.amount)} บาท`));
  $("cards").replaceChildren(...(ready ? [
    card("ส่งแล้ว", t.sent, "sent"), card("ผ่าน (A)", t.passed, "ok"),
    el("div", { class: "card tone-paid" }, el("span", { class: "card-label" }, "ชดเชย (REP)"),
      el("span", { class: "card-count" }, baht.format(t.paid), el("small", {}, " บาท")),
      el("span", { class: "card-amount" }, `STM จ่ายแล้ว ${baht.format(t.stm_paid)} บาท`)),
    card("ติด C", t.c, "warn"), card("Deny", t.deny, "error"), card("รอ REP", t.waiting, "all"),
    card("ยังไม่ส่ง (พร้อมส่ง)", t.ready, "special"), card("ยังไม่ส่ง (มี Error)", t.error, "error"),
  ] : []));
}

async function load() {
  const form = $("search");
  const period = { start: form.elements.start.value, end: form.elements.end.value };
  if (!period.start || !period.end) return notice("เลือกช่วงวันที่ก่อน");
  const run = ++state.run;
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const services = await api("/api/ppfs/services");
    state.results = services.map((s) => ({ service: s.key, label: s.label }));
    render();
    // One fund at a time: each one reads HOSxP for the whole period.
    for (const [i, s] of services.entries()) {
      if (run !== state.run) return;
      $("progress").textContent = `กำลังสรุป ${i + 1} / ${services.length}: ${s.label}`;
      state.results[i].pending = true; render();
      try {
        state.results[i] = { ...(await api("/api/ppfs/summary", { service: s.key, ...period })), service: s.key, label: s.label };
      } catch (err) {
        state.results[i] = { service: s.key, label: s.label, failed: err.message };
      }
      render();
    }
    $("progress").textContent = `สรุป ${services.length} กองทุน · ${toDmy(period.start)} ถึง ${toDmy(period.end)}`;
  } catch (err) { notice(err.message); } finally { if (run === state.run) button.disabled = false; }
}

async function connection() {
  try {
    const s = await api("/api/status");
    if (s.database) setConnection(`HOSxP · ${s.db_host}`, true);
    else setConnection(s.db_error ? `เชื่อมต่อไม่ได้ (${s.db_host})` : "ยังไม่ได้ตั้งค่าฐานข้อมูล", false);
  } catch (err) { setConnection("เชื่อมต่อไม่ได้", false); }
}

$("search").addEventListener("submit", (e) => { e.preventDefault(); clearNotices(); load(); });
$("fiscal").onclick = () => {
  const today = new Date();
  const year = today.getMonth() >= 9 ? today.getFullYear() : today.getFullYear() - 1;
  $("search").elements.start.value = `${year}-10-01`;
  $("search").elements.end.value = isoDate(today);
};
setupPresets($("search"));
$("fiscal").click();
render();
connection();
