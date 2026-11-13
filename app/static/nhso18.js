"use strict";
const state = { rows: [], selected: new Set(), filter: "all", text: "", current: null, sort: { key: "", dir: "asc" }, pttypes: [] };
const GROUPS = { UCS: /^(UCS|WEL)$/, OFC: /^(A2|OFC|PTY|KTT)/, SSS: /SS/, LGO: /LGO/, BKK: /BKK/, "พ.ร.บ.": /INS|^A9/ };

const CATEGORIES = {
  all: ["ทั้งหมด", () => true, "all"],
  ready: ["พร้อมส่ง", (r) => r.selectable && !r.has_warn && !r.status, "ok"],
  warn: ["มีคำเตือน", (r) => r.selectable && r.has_warn, "warn"],
  error: ["Error", (r) => r.has_error, "error"],
  sent: ["ส่งแล้ว", (r) => r.status, "sent"],
};

function rank(r) { return r.has_error ? 2 : r.has_warn ? 1 : 0; }
function resultBadge(r) {
  if (r.has_error) return el("span", { class: "badge b-error" }, "Error");
  if (r.has_warn) return el("span", { class: "badge b-warn" }, "เตือน");
  return el("span", { class: "badge b-ok" }, "ผ่าน");
}
function visible() {
  const q = state.text.toLowerCase();
  const test = CATEGORIES[state.filter][1];
  const list = state.rows.filter((r) => test(r) && (!q || `${r.ptname} ${r.key} ${r.hn} ${r.pdx}`.toLowerCase().includes(q)));
  return sortRows(list.map((r) => Object.assign(r, { rank: rank(r) })), state.sort, ["money", "rank"]);
}
function renderChips() { renderCards($("summary"), CATEGORIES, state.rows, state.filter, (r) => r.money, (key) => { state.filter = key; render(); }); }

function renderRows() {
  const list = visible();
  showSort($("thead"), state.sort);
  $("rows").replaceChildren(...list.map((r) => {
    const box = el("input", { type: "checkbox", checked: state.selected.has(r.key), disabled: !r.selectable,
      title: r.selectable ? "เลือกส่ง" : "รายการนี้ส่งไม่ได้", "aria-label": `เลือก ${r.key}`, onclick: (e) => e.stopPropagation(),
      onchange: (e) => { pick(r, e.target.checked); renderRows(); renderTotals(); } });
    const problems = [...r.errors, ...r.warns].map((i) => i.message);
    return el("tr", { class: state.current === r.key ? "current" : "", onclick: () => openDrawer(r) },
      el("td", { class: "check" }, box), el("td", {}, resultBadge(r)),
      el("td", {}, r.status ? el("span", { class: "badge b-sent" }, r.status) : (r.approve_required && r.claimcode ? null : el("span", { class: "muted" }, "—")), ktbTag(r), walkinTag(r), uucTag(r)),
      el("td", {}, r.kind), el("td", { class: "mono" }, r.key), el("td", { class: "mono" }, r.hn),
      el("td", { class: "name-cell", title: r.ptname }, r.ptname, el("span", { class: "sub" }, r.pttype)),
      el("td", { class: "mono" }, toDmy(r.date)), el("td", {}, r.maininscl), el("td", {}, r.pdx || el("span", { class: "muted" }, "—")),
      el("td", { class: "num" }, baht.format(Number(r.money)), r.excluded_count ? el("span", { class: "excluded-note" }, `ไม่ส่ง ${r.excluded_count} รายการ`) : ""),
      el("td", { class: "problems" }, problemText(problems)));
  }));
  $("empty").hidden = list.length > 0;
  if (!list.length && state.rows.length) $("empty").replaceChildren(...emptyState("ไม่มีรายการตามตัวกรองนี้"));
  $("result-count").textContent = state.rows.length ? `แสดง ${count.format(list.length)} จาก ${count.format(state.rows.length)} รายการ` : "";
}
// UCS walk-in visits of the same HN and day are sent as one claim on the first VN, so they are picked together.
function pick(r, on) {
  const rows = r.walkin_group ? state.rows.filter((x) => x.walkin_group === r.walkin_group && x.selectable) : [r];
  rows.forEach((x) => (on ? state.selected.add(x.key) : state.selected.delete(x.key)));
}
function walkinTag(r) {
  if (r.walkin_first) return el("span", { class: "walkin-tag", title: "Walk-in บัตรทองวันเดียวกัน ค่าใช้จ่ายรวมไปส่งกับ VN แรก" }, `รวมกับ ${r.walkin_first}`);
  if (r.walkin_group) return el("span", { class: "walkin-tag first", title: "Walk-in บัตรทองวันเดียวกัน visit อื่นรวมเข้ามาที่ VN นี้" }, "VN แรก (รวม walk-in)");
  return null;
}
// OFC/LGO visits with an imported Krungthai Approve Code: the code goes under the status, or under "คีย์ KTB แล้ว" when not yet sent.
function ktbTag(r) {
  if (!r.approve_required || !r.claimcode) return null;
  return el("span", { class: "ktb-tag", title: "Approve Code กรุงไทย (นำเข้าที่เมนู Approve Code)" },
    r.status ? null : el("span", { class: "ktb-label" }, "คีย์ KTB แล้ว"), el("span", { class: "ktb-code mono" }, r.claimcode));
}
function uucTag(r) {
  return r.uuc_set ? el("span", { class: "uuc-tag", title: "กำหนด UUC เองในโปรแกรมนี้ (ไม่แก้ HOSxP)" }, `UUC ${r.uuc_set} (กำหนดเอง)`) : null;
}
// UUC 1 = ใช้สิทธิ / 2 = ไม่ใช้สิทธิ. "อัตโนมัติ" is 1, or 2 when HOSxP has request_funds N; the choice is kept in this app only.
function uucControl(r, data) {
  const box = el("div", { class: "uuc-box" });
  const draw = () => {
    const used = r.uuc_set || data.uuc_auto;
    const choice = (value, label) => el("button", { type: "button", class: (r.uuc_set || "") === value ? "primary" : "",
      "aria-pressed": String((r.uuc_set || "") === value),
      onclick: async () => {
        try {
          await api("/api/nhso18/uuc", { key: r.key, uuc: value });
          r.uuc_set = value; r.uuc = value || data.uuc_auto || r.uuc; draw(); renderRows();
        } catch (err) { notice(err.message); }
      } }, label);
    box.replaceChildren(
      el("div", { class: "uuc-head" }, el("strong", {}, `UUC ที่จะส่ง: ${used || "—"}`), " ",
        el("span", { class: "muted" }, used === "1" ? "(ใช้สิทธิ)" : used === "2" ? "(ไม่ใช้สิทธิ)" : ""),
        el("span", { class: "muted-note" }, data.uuc_auto === "2" ? " อัตโนมัติ = 2 เพราะ HOSxP ตั้งไม่ขอเบิกไว้ (request_funds N)" : ` อัตโนมัติ = ${data.uuc_auto || "—"}`)),
      el("div", { class: "uuc-buttons" }, choice("", `อัตโนมัติ (${data.uuc_auto === "2" ? "ไม่ใช้สิทธิ" : "ใช้สิทธิ"})`), choice("1", "1 ใช้สิทธิ"), choice("2", "2 ไม่ใช้สิทธิ")));
  };
  draw();
  return box;
}
// How many picked visits go as UUC 1 (ใช้สิทธิ) and 2 (ไม่ใช้สิทธิ), for the confirm dialogs.
function uucSummary(list) {
  const two = list.filter((r) => r.uuc === "2").length;
  return [el("dt", {}, "UUC"), el("dd", {}, `1 ใช้สิทธิ ${count.format(list.length - two)} · `,
    el("span", { class: two ? "danger-text" : "" }, `2 ไม่ใช้สิทธิ ${count.format(two)}`))];
}
function mergedText(result) { return result.merged ? ` · รวม walk-in ${result.merged} visit เข้ากับ VN แรก` : ""; }
function selectedRows() { return state.rows.filter((r) => state.selected.has(r.key)); }
function renderTotals() {
  const list = selectedRows();
  $("selected-count").textContent = `เลือก ${count.format(list.length)} รายการ`;
  $("selected-total").textContent = `ค่าใช้จ่าย ${baht.format(list.reduce((s, r) => s + Number(r.money), 0))} บาท`;
  $("export").disabled = list.length === 0;
  $("fdh-send").disabled = $("fdh-prescreen").disabled = $("eclaim-send").disabled = list.length === 0 || state.fdhBusy;
}
function render() { renderChips(); renderRows(); renderTotals(); }

function table(headers, rows) {
  return el("table", {}, el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", { class: h.num ? "num" : "" }, h.label || h)))), el("tbody", {}, ...rows));
}

// Totals of the cost tab by charge category (หมวด), split into what is sent and what is left out.
function costSummary(items) {
  const groups = new Map();
  for (const item of items) {
    const key = item.billmaud || "-";
    const g = groups.get(key) || { code: key, name: item.billmaud_name || "", count: 0, total: 0, left: 0 };
    const amount = Number(item.amount) || 0;
    g.count += 1; g.total += amount;
    if (item.excluded || item.hosxp_excluded) g.left += amount;
    groups.set(key, g);
  }
  const list = [...groups.values()].sort((a, b) => (Number(a.code) || 999) - (Number(b.code) || 999));
  const sum = (k) => list.reduce((s, g) => s + g[k], 0);
  const money = (n, cls = "num") => el("td", { class: cls }, baht.format(n));
  const head = el("thead", {}, el("tr", {}, el("th", {}, "หมวด"), el("th", { class: "num" }, "รายการ"),
    el("th", { class: "num" }, "ยอดรวม"), el("th", { class: "num" }, "ไม่ส่ง"), el("th", { class: "num" }, "ยอดที่ส่ง")));
  const body = el("tbody", {}, ...list.map((g) => el("tr", {},
    el("td", {}, el("span", { class: "mono" }, g.code), g.name ? ` ${g.name}` : ""), el("td", { class: "num" }, count.format(g.count)),
    money(g.total), g.left ? money(g.left, "num left") : el("td", { class: "num muted" }, "—"), money(g.total - g.left))));
  const foot = el("tfoot", {}, el("tr", {}, el("th", {}, "รวมทั้งหมด"), el("th", { class: "num" }, count.format(sum("count"))),
    el("th", { class: "num" }, baht.format(sum("total"))), el("th", { class: "num" }, baht.format(sum("left"))),
    el("th", { class: "num" }, baht.format(sum("total") - sum("left")))));
  return el("table", { class: "cost-table" }, head, body, foot);
}

async function openDrawer(r) {
  state.current = r.key; renderRows();
  $("drawer").hidden = false; $("drawer-backdrop").hidden = false;
  $("drawer-title").textContent = r.ptname || r.key;
  $("drawer-sub").textContent = `${r.kind} ${r.key} · HN ${r.hn} · ${toDmy(r.date)}`;
  const body = $("drawer-body");
  body.replaceChildren(el("p", { class: "muted" }, "กำลังโหลด…"));
  try {
    const data = await api("/api/nhso18/detail", { key: r.key, kind: r.kind });
    if (state.current !== r.key) return;
    const check = [el("dl", { class: "info-grid" }, ...[["เลขบัตร", r.cid], ["สิทธิ", r.pttype], ["MainInscl", r.maininscl], ...(r.approve_required
        ? [["Approve Code (ใช้ส่ง)", r.claimcode || "(ยังไม่มี)"], ["ClaimCode ใน HOSxP", r.claimcode_hosxp || "(ไม่มี)"]]
        : [["ClaimCode", r.claimcode || "(ไม่มี)"]]),
      ["PDx", r.pdx], ["ค่าใช้จ่าย", `${baht.format(Number(r.money))} บาท`], ["สถานะ", r.status || "ยังไม่ส่ง"], ["ชดเชย FDH", r.fdh_amount ? `${baht.format(Number(r.fdh_amount))} บาท` : "—"]]
      .map(([k, v]) => el("div", {}, el("dt", {}, k), el("dd", {}, v || "—"))))];
    check.push(uucControl(r, data));
    const issues = [...r.errors.map((i) => ({ ...i, level: "error" })), ...r.warns.map((i) => ({ ...i, level: "warn" }))];
    if (issues.length) {
      check.push(el("h3", {}, "ผลตรวจ"));
      check.push(el("ul", { class: "issues" }, ...issues.map((i) => el("li", { class: `issue ${i.level}` },
        el("div", { class: "issue-title" }, el("span", { class: `badge ${i.level === "error" ? "b-error" : "b-warn"}` }, i.level === "error" ? "Error" : "เตือน"), " ", i.message),
        i.items.length ? el("ul", {}, ...i.items.slice(0, 20).map((n) => el("li", {}, n))) : null))));
      check.push(el("p", { class: "hint" }, "แก้ข้อมูลใน HOSxP หรือติ๊กไม่ส่งรายการที่มีปัญหาในแท็บค่าใช้จ่าย แล้วกดค้นหาอีกครั้ง"));
    } else check.push(el("div", { class: "all-good" }, "✓ ผ่านการตรวจทั้งหมด พร้อมส่ง"));

    const dx = [data.dx.length ? table(["ประเภท", "รหัส", "ชื่อโรค"], data.dx.map((d) => el("tr", {}, el("td", {}, d.type), el("td", { class: "mono" }, d.code), el("td", {}, d.name)))) : el("p", { class: "muted" }, "ไม่มีข้อมูล")];
    const summary = el("div", { class: "cost-summary" });
    const showSummary = () => summary.replaceChildren(costSummary(data.items));
    showSummary();
    const costs = [summary, el("p", { class: "muted-note" }, "ติ๊ก \"ไม่ส่ง\" เพื่อเอารายการออกจากแฟ้ม CHT, CHA, ADP, DRU และ PHDB (ไม่แก้ข้อมูลใน HOSxP) รายการที่ HOSxP กำหนดไม่ส่งไว้จะไม่ถูกส่งเหมือนโปรแกรมเดิม"),
      table(["ไม่ส่ง", "หมวด", "รายการ", "ADP", { label: "จำนวน", num: true }, { label: "รวม", num: true }], data.items.map((item) => {
        const row = el("tr", { class: item.excluded || item.hosxp_excluded ? "excluded" : "" });
        row.append(
          el("td", { class: "check" }, el("input", { type: "checkbox", checked: item.excluded || item.hosxp_excluded, disabled: item.hosxp_excluded,
            title: item.hosxp_excluded ? "HOSxP กำหนดไม่ส่ง" : "", "aria-label": `ไม่ส่ง ${item.name}`,
            onchange: async (e) => {
              try {
                const res = await api("/api/nhso18/item", { key: r.key, icode: item.icode, excluded: e.target.checked });
                item.excluded = e.target.checked;
                r.excluded_count = res.excluded_count; row.classList.toggle("excluded", e.target.checked); renderRows(); showSummary();
              } catch (err) { e.target.checked = !e.target.checked; notice(err.message); }
            } })),
          el("td", {}, item.billmaud), el("td", {}, item.name, item.hosxp_excluded ? el("div", { class: "muted-note" }, "HOSxP กำหนดไม่ส่ง") : ""),
          el("td", { class: "mono" }, item.adp), el("td", { class: "num" }, item.qty), el("td", { class: "num" }, baht.format(Number(item.amount))));
        return row;
      }))];
    body.replaceChildren(...tabs([
      { id: "check", label: "สรุปและผลตรวจ", badge: issues.length || undefined, alert: r.has_error, content: check },
      { id: "dx", label: "การวินิจฉัย", badge: data.dx.length, content: dx },
      { id: "cost", label: "ค่าใช้จ่าย", badge: data.items.length, content: costs },
    ]));
  } catch (err) { body.replaceChildren(el("p", {}, err.message)); }
}
function closeDrawer() { $("drawer").hidden = true; $("drawer-backdrop").hidden = true; state.current = null; renderRows(); }

async function runExport() {
  clearNotices();
  const list = selectedRows();
  const summary = el("dl", { class: "sum" },
    el("dt", {}, "OPD"), el("dd", {}, `${list.filter((r) => r.kind === "OPD").length} visit`),
    el("dt", {}, "IPD"), el("dd", {}, `${list.filter((r) => r.kind === "IPD").length} AN`),
    ...uucSummary(list),
    el("dt", {}, "ค่าใช้จ่ายรวม"), el("dd", {}, `${baht.format(list.reduce((s, r) => s + Number(r.money), 0))} บาท`),
    el("dt", {}, "มีคำเตือน"), el("dd", {}, `${list.filter((r) => r.has_warn).length} รายการ`));
  if (!await confirmDialog("ยืนยันการสร้าง ZIP 18 แฟ้ม", [summary], "สร้าง ZIP")) return;
  const payload = { rows: list.map((r) => ({ key: r.key, kind: r.kind })) };
  $("export").disabled = true; $("export").textContent = "กำลังสร้าง…";
  try {
    let result;
    try { result = await api("/api/nhso18/export", payload); }
    catch (err) {
      if (!err.data || !err.data.needs_confirm) throw err;
      const again = el("p", {}, `มี ${err.data.resend.length} รายการที่เคยส่งหรือส่งออกแล้ว ต้องการส่งซ้ำใช่หรือไม่?`);
      if (!await confirmDialog("ยืนยันการส่งซ้ำ", [again], "ส่งซ้ำ")) return;
      result = await api("/api/nhso18/export", { ...payload, confirm_resend: true });
    }
    const counts = Object.entries(result.counts).filter(([, n]) => n).map(([f, n]) => `${f} ${n}`).join(", ");
    const box = notice(`สร้าง ${result.file} แล้ว: ${result.count} รายการ (${counts})${mergedText(result)}`, "ok", true);
    box.querySelector(".notice-text").append(" ", el("a", { href: `/api/nhso18/exports/${result.id}/download` }, "ดาวน์โหลด ZIP"));
    state.selected.clear();
    await Promise.all([search(), loadHistory()]);
  } catch (err) {
    const detail = err.data && err.data.blocked ? `: ${err.data.blocked.map((b) => `${b.key} (${b.reason})`).join(", ")}` : "";
    notice(err.message + detail);
  } finally { $("export").textContent = "สร้าง ZIP 18 แฟ้ม"; renderTotals(); }
}

// ------------------------------------------------------------- FDH
function fdhZone() { return $("fdh-zone").value; }
function fdhZoneLabel(zone) { return zone === "prod" ? "PROD (ส่งจริง)" : "UAT (ทดสอบ)"; }

async function runFdh(prescreen) {
  clearNotices();
  const list = selectedRows();
  const zone = fdhZone();
  const what = prescreen ? "PreScreen" : "ส่ง FDH";
  const summary = el("dl", { class: "sum" },
    el("dt", {}, "ปลายทาง"), el("dd", { class: zone === "prod" ? "danger-text" : "" }, fdhZoneLabel(zone)),
    el("dt", {}, "บัญชี FDH"), el("dd", {}, (state.fdhStatus && state.fdhStatus.fdh_user) || "ยังไม่ได้ตั้ง"),
    el("dt", {}, "OPD"), el("dd", {}, `${list.filter((r) => r.kind === "OPD").length} visit`),
    el("dt", {}, "IPD"), el("dd", {}, `${list.filter((r) => r.kind === "IPD").length} AN`),
    ...uucSummary(list),
    el("dt", {}, "ค่าใช้จ่ายรวม"), el("dd", {}, `${baht.format(list.reduce((s, r) => s + Number(r.money), 0))} บาท`),
    el("dt", {}, "PHDB"), el("dd", {}, prescreen ? "ไม่ส่ง (PreScreen)" : $("fdh-phdb").checked ? "ส่งด้วย" : "ไม่ส่ง"));
  if (!await confirmDialog(`ยืนยัน${what}`, [summary], what)) return;
  const payload = { rows: list.map((r) => ({ key: r.key, kind: r.kind })), zone, prescreen, phdb: $("fdh-phdb").checked };
  const button = $(prescreen ? "fdh-prescreen" : "fdh-send");
  state.fdhBusy = true; renderTotals(); button.textContent = "กำลังส่ง…";
  try {
    let result;
    try { result = await api("/api/nhso18/fdh/send", payload); }
    catch (err) {
      if (!err.data || !err.data.needs_confirm) throw err;
      const again = el("p", {}, `มี ${err.data.resend.length} รายการที่เคยส่งหรือส่งออกแล้ว ต้องการส่งซ้ำใช่หรือไม่?`);
      if (!await confirmDialog("ยืนยันการส่งซ้ำ", [again], "ส่งซ้ำ")) return;
      result = await api("/api/nhso18/fdh/send", { ...payload, confirm_resend: true });
    }
    const counts = Object.entries(result.counts).filter(([, n]) => n).map(([f, n]) => `${f} ${n}`).join(", ");
    const head = `${what} ${fdhZoneLabel(result.zone)} ${result.count} รายการ (${counts})${mergedText(result)}`;
    notice(result.ok ? `${head}: ${result.message}` : `${head} ไม่ผ่าน: ${result.message}`, result.ok ? "ok" : "error", true);
    if (result.ok && !prescreen) { state.selected.clear(); await search(); }
  } catch (err) {
    const detail = err.data && err.data.blocked ? `: ${err.data.blocked.map((b) => `${b.key} (${b.reason})`).join(", ")}` : "";
    notice(err.message + detail);
  } finally {
    state.fdhBusy = false; button.textContent = prescreen ? "PreScreen" : "ส่ง FDH"; renderTotals(); loadFdhHistory();
  }
}

async function testFdh() {
  clearNotices();
  $("fdh-test").disabled = true;
  try { notice((await api("/api/nhso18/fdh/test", { zone: fdhZone() })).message, "ok"); }
  catch (err) { notice(err.message); }
  finally { $("fdh-test").disabled = false; }
}

async function loadFdhHistory() {
  if ($("fdh").hidden) { $("fdh-history").replaceChildren(el("p", { class: "muted" }, "ยังไม่ได้ตั้งค่า FDH")); return; }
  try {
    const rows = await api("/api/nhso18/fdh/history");
    $("fdh-history").replaceChildren(rows.length ? table(["เวลา", "ปลายทาง", "ประเภท", "จำนวน", "ผล", "ข้อความ", "ผู้ส่ง", "บัญชี FDH"], rows.map((r) => el("tr", {},
      el("td", {}, thaiDateTime(r.created_at)), el("td", {}, r.zone.toUpperCase()), el("td", {}, r.mode === "prescreen" ? "PreScreen" : "ส่ง"),
      el("td", {}, String(r.visit_count)), el("td", {}, el("span", { class: `badge ${r.ok ? "b-ok" : "b-error"}` }, r.ok ? "สำเร็จ" : "ไม่ผ่าน")),
      el("td", { class: "problems" }, r.message), el("td", {}, r.created_by || "-"), el("td", { class: "mono" }, r.fdh_user || "-")))) : el("p", {}, "ยังไม่มีประวัติ"));
  } catch (err) { $("fdh-history").replaceChildren(el("p", {}, err.message)); }
}

// Each login may keep its own FDH account (any machine); without one the .env account is used.
function showFdhAccount(status) {
  state.fdhStatus = status;
  $("fdh").hidden = !status.ready && !status.can_set; $("fdh-off").hidden = !$("fdh").hidden;
  const label = status.source === "own" ? `บัญชี: ${status.fdh_user} (ของฉัน)`
    : status.source === "env" ? `บัญชี: ${status.fdh_user} (กลาง)` : "ตั้งบัญชี FDH";
  $("fdh-account").textContent = label;
  $("fdh-account").classList.toggle("missing", !status.ready);
  $("fdh-account").disabled = !status.can_set;
  $("fdh-account").title = status.can_set ? "ตั้งบัญชี FDH ของฉัน" : "ต้องเข้าสู่ระบบจึงจะตั้งบัญชีของตัวเองได้";
}
function openFdhAccount() {
  const st = state.fdhStatus || {};
  const form = $("fdh-account-form");
  form.reset();
  form.elements.user.value = st.source === "own" ? st.fdh_user : "";
  $("fdh-account-now").textContent = st.source === "own" ? `ตอนนี้ใช้บัญชีของฉัน: ${st.fdh_user}`
    : st.env_user ? `ตอนนี้ใช้บัญชีกลาง: ${st.env_user}` : "ยังไม่มีบัญชี FDH";
  $("fdh-account-clear").hidden = st.source !== "own";
  $("fdh-account-dialog").showModal();
}
async function saveFdhAccount(e) {
  e.preventDefault();
  const form = $("fdh-account-form"), button = $("fdh-account-save");
  button.disabled = true; button.textContent = "กำลังตรวจกับ FDH…";
  try {
    showFdhAccount(await api("/api/nhso18/fdh/account", { user: form.elements.user.value.trim(), password: form.elements.password.value }));
    $("fdh-account-dialog").close();
    notice(`บันทึกบัญชี FDH ${state.fdhStatus.fdh_user} แล้ว (ขอ Token ได้)`, "ok");
  } catch (err) { $("fdh-account-now").textContent = err.message; }
  finally { form.elements.password.value = ""; button.disabled = false; button.textContent = "ตรวจและบันทึก"; }
}
async function clearFdhAccount() {
  try {
    showFdhAccount(await api("/api/nhso18/fdh/account", { clear: true }));
    $("fdh-account-dialog").close();
    notice("ลบบัญชี FDH ของฉันแล้ว", "ok");
  } catch (err) { $("fdh-account-now").textContent = err.message; }
}

async function setupFdh() {
  try { showFdhAccount(await api("/api/nhso18/fdh/status")); }
  catch (err) { $("fdh-off").hidden = false; }
  $("fdh-account").onclick = openFdhAccount;
  $("fdh-account-form").onsubmit = saveFdhAccount;
  $("fdh-account-clear").onclick = clearFdhAccount;
  $("fdh-account-cancel").onclick = () => $("fdh-account-dialog").close();
  const mark = () => $("fdh").classList.toggle("prod", fdhZone() === "prod");
  $("fdh-zone").onchange = mark; mark();
  loadFdhHistory();
}

// ------------------------------------------------------------- New e-Claim API
async function runEclaim() {
  clearNotices();
  const list = selectedRows();
  const inscl = $("eclaim-inscl").value, dup = $("eclaim-dup").checked;
  const summary = el("dl", { class: "sum" },
    el("dt", {}, "ปลายทาง"), el("dd", { class: "danger-text" }, "New e-Claim (ส่งจริง)"),
    el("dt", {}, "OPD"), el("dd", {}, `${list.filter((r) => r.kind === "OPD").length} visit`),
    el("dt", {}, "IPD"), el("dd", {}, `${list.filter((r) => r.kind === "IPD").length} AN`),
    ...uucSummary(list),
    el("dt", {}, "ค่าใช้จ่ายรวม"), el("dd", {}, `${baht.format(list.reduce((s, r) => s + Number(r.money), 0))} บาท`),
    el("dt", {}, "สิทธิ"), el("dd", {}, inscl || "ทุกสิทธิ"),
    el("dt", {}, "นำเข้าซ้ำ"), el("dd", { class: dup ? "danger-text" : "" }, dup ? "ใช่" : "ไม่"));
  if (!await confirmDialog("ยืนยันส่ง New e-Claim", [summary], "ส่ง New e-Claim")) return;
  const payload = { rows: list.map((r) => ({ key: r.key, kind: r.kind })), maininscl: inscl, import_dup: dup };
  state.fdhBusy = true; renderTotals(); $("eclaim-send").textContent = "กำลังส่ง…";
  try {
    let result;
    try { result = await api("/api/nhso18/eclaim/send", payload); }
    catch (err) {
      if (!err.data || !err.data.needs_confirm) throw err;
      const again = el("p", {}, `มี ${err.data.resend.length} รายการที่เคยส่งหรือส่งออกแล้ว ต้องการส่งซ้ำใช่หรือไม่?`);
      if (!await confirmDialog("ยืนยันการส่งซ้ำ", [again], "ส่งซ้ำ")) return;
      result = await api("/api/nhso18/eclaim/send", { ...payload, confirm_resend: true });
    }
    const counts = Object.entries(result.counts).filter(([, n]) => n).map(([f, n]) => `${f} ${n}`).join(", ");
    const head = `ส่ง New e-Claim ${result.count} รายการ (${counts})${mergedText(result)}`;
    notice(result.ok ? `${head}: ${result.message}` : `${head} ไม่ผ่าน: ${result.message}`, result.ok ? "ok" : "error", true);
    if (result.ok) { state.selected.clear(); await search(); }
  } catch (err) {
    const detail = err.data && err.data.blocked ? `: ${err.data.blocked.map((b) => `${b.key} (${b.reason})`).join(", ")}` : "";
    notice(err.message + detail);
  } finally {
    state.fdhBusy = false; $("eclaim-send").textContent = "ส่ง New e-Claim"; renderTotals(); loadEclaimHistory();
  }
}

async function testEclaim() {
  clearNotices();
  $("eclaim-test").disabled = true;
  try { notice((await api("/api/nhso18/eclaim/test", {})).message, "ok"); }
  catch (err) { notice(err.message); }
  finally { $("eclaim-test").disabled = false; }
}

async function loadEclaimHistory() {
  if ($("eclaim").hidden) { $("eclaim-history").replaceChildren(el("p", { class: "muted" }, "ยังไม่ได้ตั้งค่า New e-Claim API")); return; }
  try {
    const rows = await api("/api/nhso18/eclaim/history");
    $("eclaim-history").replaceChildren(rows.length ? table(["เวลา", "สิทธิ", "นำเข้าซ้ำ", "จำนวน", "ผล", "ข้อความ", "ผู้ส่ง"], rows.map((r) => el("tr", {},
      el("td", {}, thaiDateTime(r.created_at)), el("td", {}, r.maininscl || "ทุกสิทธิ"), el("td", {}, r.import_dup ? "ใช่" : "-"),
      el("td", {}, String(r.visit_count)), el("td", {}, el("span", { class: `badge ${r.ok ? "b-ok" : "b-error"}` }, r.ok ? "สำเร็จ" : "ไม่ผ่าน")),
      el("td", { class: "problems" }, r.message), el("td", {}, r.created_by || "-")))) : el("p", {}, "ยังไม่มีประวัติ"));
  } catch (err) { $("eclaim-history").replaceChildren(el("p", {}, err.message)); }
}

async function setupEclaim() {
  try {
    const status = await api("/api/nhso18/eclaim/status");
    $("eclaim").hidden = !status.ready; $("eclaim-off").hidden = status.ready;
  } catch (err) { $("eclaim-off").hidden = false; }
  loadEclaimHistory();
}

async function search() {
  const form = $("search");
  const key = form.elements.key.value.trim();
  const body = {
    start: form.elements.start.value, end: form.elements.end.value, hn: form.elements.hn.value.trim(),
    vn: key.length > 9 ? key : "", an: key.length && key.length <= 9 ? key : "",
    opd: form.elements.opd.checked, ipd: form.elements.ipd.checked,
    pttypes: [...document.querySelectorAll("#pttype-list input.one:checked")].map((i) => i.value),
  };
  $("empty").hidden = false; $("empty").replaceChildren(...emptyState("กำลังค้นหาและตรวจข้อมูล… (เดือนหนึ่งอาจใช้เวลาหลายนาที)", "…"));
  $("rows").replaceChildren();
  try {
    const data = await api("/api/nhso18/search", body);
    state.rows = data.rows;
    state.selected = new Set(data.rows.filter((r) => r.default_selected).map((r) => r.key));
    $("empty").replaceChildren(...emptyState("ไม่พบรายการในช่วงนี้"));
    render();
  } catch (err) { $("empty").replaceChildren(...emptyState(err.message, "!")); }
}

async function loadHistory() {
  try {
    const rows = await api("/api/nhso18/exports");
    $("history").replaceChildren(rows.length ? table(["เวลา", "จำนวน", "ไฟล์", "ผู้สร้าง"], rows.map((r) => el("tr", {},
      el("td", {}, thaiDateTime(r.created_at)), el("td", {}, String(r.visit_count)),
      el("td", {}, el("a", { href: `/api/nhso18/exports/${r.id}/download` }, r.file)), el("td", {}, r.created_by || "-")))) : el("p", {}, "ยังไม่มีประวัติ"));
  } catch (err) { $("history").replaceChildren(el("p", {}, err.message)); }
}

function pickGroup(name) {
  const test = GROUPS[name];
  const codes = new Set(state.pttypes.filter((p) => test.test(p.hipdata_code || "")).map((p) => p.pttype));
  document.querySelectorAll("#pttype-list input.one").forEach((box) => { box.checked = codes.has(box.value); });
  updatePttypeLabel();
}

async function start() {
  const today = new Date();
  $("search").elements.start.value = isoDate(new Date(today.getFullYear(), today.getMonth(), 1));
  $("search").elements.end.value = isoDate(today);
  setupPresets($("search"));
  bindSort($("thead"), state.sort, renderRows);
  $("groups").replaceChildren(...Object.keys(GROUPS).map((g) => el("button", { type: "button", onclick: () => pickGroup(g) }, g)));
  try {
    const status = await api("/api/status");
    if (status.db_error) { setConnection(`เชื่อมต่อไม่ได้ (${status.db_host})`, false); notice(status.db_error); return; }
    if (!status.database) { setConnection("ยังไม่ได้ตั้งค่าฐานข้อมูล", false); return; }
    setConnection(`HOSxP · ${status.db_host}`, true);
    state.pttypes = await api("/api/nhso18/pttypes");
    fillPttypes(state.pttypes);
    pickGroup("UCS");
  } catch (err) { setConnection("เชื่อมต่อไม่ได้", false); notice(err.message); }
  loadHistory();
  setupFdh();
  setupEclaim();
}

$("search").addEventListener("submit", (e) => { e.preventDefault(); clearNotices(); search(); });
$("filter").addEventListener("input", (e) => { state.text = e.target.value; renderRows(); });
$("select-ready").onclick = () => { state.selected = new Set(state.rows.filter((r) => r.default_selected).map((r) => r.key)); render(); };
$("select-none").onclick = () => { state.selected.clear(); render(); };
$("export").onclick = runExport;
$("fdh-send").onclick = () => runFdh(false);
$("fdh-prescreen").onclick = () => runFdh(true);
$("fdh-test").onclick = testFdh;
$("eclaim-send").onclick = runEclaim;
$("eclaim-test").onclick = testEclaim;
$("drawer-close").onclick = closeDrawer;
$("drawer-backdrop").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden && !$("confirm").open) closeDrawer(); });
start();
