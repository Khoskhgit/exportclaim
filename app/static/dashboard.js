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

// ------------------------------------------------------------------ charts (plain HTML bars)
const TH_SHORT = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
const monthName = (key) => { const [y, m] = key.split("-").map(Number); return `${TH_SHORT[m - 1]} ${String(y + 543).slice(2)}`; };
const share = (n, total) => (total ? `${(100 * n / total).toFixed(1)}%` : "0%");

function statusChart(reps) {
  if (!reps.length) return [el("p", { class: "muted" }, "ยังไม่มี REP ในช่วงวันที่นี้")];
  return reps.map((a) => {
    const total = a.sent.n;
    const parts = [["good", "ผ่าน (A)", a.passed], ["warn", "ติด C", a.c], ["bad", "Deny", a.deny]].filter(([, , p]) => p.n);
    return el("div", { class: "viz-row" },
      el("span", { class: "viz-label" }, a.label),
      el("div", { class: "viz-track", role: "img", "aria-label": `${a.label}: ${parts.map(([, l, p]) => `${l} ${p.n}`).join(", ")} จาก ${total} visit` },
        ...parts.map(([tone, label, p]) => el("span", {
          class: `viz-seg st-${tone}`, style: `flex-grow:${p.n}`,
          "data-tip": `${a.label}\n${label}: ${count.format(p.n)} visit (${share(p.n, total)})\n${baht.format(p.amount)} บาท`,
        }))),
      el("span", { class: "viz-value" }, `ผ่าน ${share(a.passed.n, total)}`, el("span", { class: "sub" }, `${count.format(total)} visit`)));
  });
}

function moneyChart(reps) {
  if (!reps.length) return [el("p", { class: "muted" }, "ยังไม่มี REP ในช่วงวันที่นี้")];
  const series = (a) => [["s-1", "ยอดส่ง (REP)", a.sent.amount], ["s-2", "ชดเชย (REP)", a.has_paid ? a.paid : null], ["s-3", "STM จ่าย", a.stm_paid]];
  const max = Math.max(1, ...reps.flatMap((a) => series(a).map(([, , v]) => v || 0)));
  return reps.map((a) => el("div", { class: "viz-row viz-row-group" },
    el("span", { class: "viz-label" }, a.label),
    el("div", { class: "viz-bars" }, ...series(a).map(([cls, label, value]) => el("div", { class: "viz-bar-line" },
      el("span", { class: "viz-bar-wrap" }, value ? el("span", {
        class: `viz-bar ${cls}`, style: `width:${Math.max(0.6, 100 * value / max)}%`,
        "data-tip": `${a.label}\n${label}: ${baht.format(value)} บาท`,
      }) : null),
      el("span", { class: "viz-num" }, value == null ? "ไม่มีข้อมูล" : value ? baht.format(value) : "0"))))));
}

function monthChart(m) {
  const values = m.months || [];
  const max = Math.max(0, ...values.map((x) => x.items));
  if (!max) return el("div", { class: "viz-mini viz-mini-empty" }, "ไม่มีการส่งในช่วงนี้");
  return el("div", { class: "viz-mini-wrap" },
    el("div", { class: "viz-mini", role: "img", "aria-label": `${m.label} รายเดือน: ${values.map((x) => `${monthName(x.month)} ${x.items}`).join(", ")}` },
      ...values.map((x) => el("span", { class: "viz-col-hit", "data-tip": `${m.label}\n${monthName(x.month)}: ${count.format(x.items)} ${m.unit}` },
        el("span", { class: "viz-col", style: `height:${x.items ? Math.max(4, 100 * x.items / max) : 0}%` })))),
    el("div", { class: "viz-mini-axis" }, el("span", {}, monthName(values[0].month)),
      el("span", {}, `สูงสุด ${count.format(max)}`), el("span", {}, monthName(values[values.length - 1].month))));
}

// One tooltip for every mark with data-tip: it follows the pointer and stays inside the window.
(() => {
  const tip = $("viz-tip");
  document.addEventListener("pointermove", (e) => {
    const mark = e.target.closest && e.target.closest("[data-tip]");
    if (!mark) { tip.hidden = true; return; }
    tip.replaceChildren(...mark.dataset.tip.split("\n").map((line, i) => el(i ? "div" : "strong", {}, line)));
    tip.hidden = false;
    const box = tip.getBoundingClientRect();
    const x = Math.min(e.clientX + 14, window.innerWidth - box.width - 8);
    const y = e.clientY + 16 + box.height > window.innerHeight ? e.clientY - box.height - 12 : e.clientY + 16;
    tip.style.transform = `translate(${Math.max(8, x)}px, ${y}px)`;
  });
  document.addEventListener("scroll", () => { tip.hidden = true; }, { passive: true });
})();

function renderReps(reps) {
  $("rep-rows").replaceChildren(...reps.map((a) => el("tr", {},
    el("td", {}, a.label), part(a.sent), part(a.passed, "ok"), part(a.c, "warn"), part(a.deny, "bad"),
    el("td", { class: "num" }, pct(a)), money(a.paid, "ok", a.has_paid), money(a.stm_paid), codes(a.codes))));
  $("rep-empty").hidden = reps.length > 0;
  $("chart-status").replaceChildren(...statusChart(reps));
  $("chart-money").replaceChildren(...moneyChart(reps));
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
    monthChart(m),
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
