"use strict";
// The home page: REP / STM results by right and each menu's sends, from the app's own records (no HOSxP query).

const part = (p, cls = "") => el("td", { class: `num ${cls}` },
  p.n ? el("span", {}, count.format(p.n), el("span", { class: "sub" }, `${baht.format(p.amount)} บาท`)) : "—");
const money = (value, cls = "", known = true) => el("td", { class: `num ${cls}` }, known && Number(value) ? baht.format(Number(value)) : "—");
const pct = (a) => (a.sent.n ? `${(100 * a.passed.n / a.sent.n).toFixed(1)}%` : "—");

function codes(list) {
  if (!list.length) return el("td", {}, "—");
  return el("td", { class: "dash-codes" }, ...list.slice(0, 5).map((c) =>
    el("span", { class: "code-chip", title: c.name || "ไม่มีคำอธิบาย" }, `${c.code} ×${count.format(c.n)}`)));
}

function sum(reps) {
  const add = (key) => ({ n: reps.reduce((s, a) => s + a[key].n, 0), amount: reps.reduce((s, a) => s + a[key].amount, 0) });
  return { sent: add("sent"), passed: add("passed"), c: add("c"), deny: add("deny"),
    paid: reps.reduce((s, a) => s + a.paid, 0), stm_paid: reps.reduce((s, a) => s + a.stm_paid, 0), has_paid: reps.some((a) => a.has_paid) };
}

function renderReps(reps) {
  $("rep-rows").replaceChildren(...reps.map((a) => el("tr", {},
    el("td", {}, a.label), part(a.sent), part(a.passed, "ok"), part(a.c, "warn"), part(a.deny, "bad"),
    el("td", { class: "num" }, pct(a)), money(a.paid, "ok", a.has_paid), money(a.stm_paid), codes(a.codes))));
  $("rep-empty").hidden = reps.length > 0;
  const t = sum(reps);
  $("rep-foot").replaceChildren(reps.length > 1 ? el("tr", {}, el("td", {}, "รวมทุกสิทธิ"), part(t.sent), part(t.passed, "ok"),
    part(t.c, "warn"), part(t.deny, "bad"), el("td", { class: "num" }, pct(t)), money(t.paid, "ok", t.has_paid), money(t.stm_paid), el("td", {})) : "");
  const card = (label, value, unit, sub, tone) => el("div", { class: `card tone-${tone}` }, el("span", { class: "card-label" }, label),
    el("span", { class: "card-count" }, value, el("small", {}, ` ${unit}`)), el("span", { class: "card-amount" }, sub));
  $("cards").replaceChildren(
    card("ส่งแล้ว (มี REP)", count.format(t.sent.n), "visit", `${baht.format(t.sent.amount)} บาท`, "sent"),
    card("ผ่าน (A)", count.format(t.passed.n), "visit", `${pct(t)} · ${baht.format(t.passed.amount)} บาท`, "ok"),
    card("ติด C", count.format(t.c.n), "visit", `${baht.format(t.c.amount)} บาท`, "warn"),
    card("Deny", count.format(t.deny.n), "visit", `${baht.format(t.deny.amount)} บาท`, "error"),
    card("ชดเชย (REP)", t.has_paid ? baht.format(t.paid) : "—", "บาท", "e-Claim ที่มียอดชดเชยใน REP", "paid"),
    card("STM จ่าย", baht.format(t.stm_paid), "บาท", "ตาม Statement ที่นำเข้า", "paid"));
}

function renderMenus(menus) {
  $("menus").replaceChildren(...menus.map((m) => el("a", { class: "dash-menu", href: m.href },
    el("span", { class: "dash-menu-label" }, m.label),
    el("span", { class: "dash-menu-count" }, count.format(m.items || 0), el("small", {}, ` ${m.unit}`)),
    el("span", { class: "sub" }, [
      `${m.what} ${count.format(m.n || 0)}`,
      m.amount != null ? `${baht.format(m.amount)} บาท` : "",
      m.paid ? `จ่ายแล้ว ${baht.format(m.paid)} บาท` : "",
    ].filter(Boolean).join(" · ")),
    el("span", { class: "sub" }, m.last ? `ล่าสุด ${thaiDateTime(m.last)}` : "ยังไม่มีในช่วงนี้"))));
}

function renderUploads(list) {
  $("uploads").replaceChildren(...(list.length ? list.map((u) => el("li", {},
    el("span", { class: "dash-up-when" }, thaiDateTime(u.at)), el("span", { class: "mono" }, u.name), el("span", { class: "sub" }, u.summary || "")))
    : [el("li", { class: "muted" }, "ยังไม่เคยนำเข้า")]));
}

async function load() {
  const form = $("search");
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const q = new URLSearchParams({ start: form.elements.start.value, end: form.elements.end.value });
    const data = await api(`/api/dashboard?${q}`);
    renderReps(data.reps);
    renderMenus(data.menus);
    renderUploads(data.uploads);
  } catch (err) { notice(err.message); } finally { button.disabled = false; }
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
load();
connection();
