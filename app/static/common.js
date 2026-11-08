"use strict";
const $ = (id) => document.getElementById(id);
const baht = new Intl.NumberFormat("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key in node) node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) if (child !== null && child !== undefined) node.append(child);
  return node;
}

async function api(path, body) {
  const options = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && data.login) { window.location.assign("/login?error=expired"); throw new Error(data.detail); }
  if (!response.ok) { const err = new Error(data.detail || `ผิดพลาด (${response.status})`); err.data = data; throw err; }
  return data;
}

function notice(message, kind = "error", lasting = false) {
  const box = el("div", { class: `notice ${kind}${lasting ? " lasting" : ""}`, role: "alert" }, message);
  $("notices").append(box);
  if (kind !== "error" && !lasting) setTimeout(() => box.remove(), 8000);
  return box;
}
function clearNotices() { $("notices").querySelectorAll(".notice:not(.lasting)").forEach((box) => box.remove()); }

function confirmDialog(title, content, okLabel) {
  return new Promise((resolve) => {
    const dialog = $("confirm");
    $("confirm-title").textContent = title;
    $("confirm-body").replaceChildren(...content);
    $("confirm-ok").textContent = okLabel;
    const done = (value) => { dialog.close(); $("confirm-ok").onclick = $("confirm-cancel").onclick = null; resolve(value); };
    $("confirm-ok").onclick = () => done(true);
    $("confirm-cancel").onclick = () => done(false);
    dialog.oncancel = (e) => { e.preventDefault(); done(false); };
    dialog.showModal();
  });
}


async function showUser() {
  try {
    const me = await api("/api/me");
    if (!me.login_required || !me.name) return;
    const box = el("div", { class: "user-box" }, el("span", {}, `${me.name}${me.method === "moph" ? " (MOPH ID)" : ""}`),
      el("button", { type: "button", class: "small", onclick: async () => {
        await fetch("/auth/logout", { method: "POST" }).catch(() => null);
        window.location.assign("/login");
      } }, "ออกจากระบบ"));
    document.querySelector(".top").append(box);
  } catch (err) { /* the page shows its own errors */ }
}
showUser();
