"use strict";
const form = document.getElementById("login-form");
const errorBox = document.getElementById("login-error");
const ring = document.getElementById("ring");

// The ring: 48 bars round the form. A light sweeps round it, faster while the login is being checked
// (.busy), all lit when it succeeds (.ok) and a red flash when it fails (.fail).
const BARS = 48;
for (let i = 0; i < BARS; i += 1) {
  const bar = document.createElement("span");
  bar.className = "ring-bar";
  bar.style.setProperty("--a", `${(360 / BARS) * i}deg`);
  // Each bar starts part-way through the sweep (a fraction of one turn: 3 s idle, 0.9 s while waiting, in app.css),
  // so the light runs round in order at any speed.
  bar.style.setProperty("--f", String(1 - i / BARS));
  ring.append(bar);
}
function ringState(state) {
  ring.classList.remove("busy", "ok", "fail");
  if (state) {
    void ring.offsetWidth; // restart the animation when the same state comes again
    ring.classList.add(state);
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorBox.hidden = true;
  const button = form.querySelector(".login-submit");
  button.disabled = true;
  button.textContent = "กำลังตรวจสอบ…";
  ringState("busy");
  let ok = false;
  try {
    const response = await fetch("/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: form.username.value.trim(), password: form.password.value }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `เข้าสู่ระบบไม่สำเร็จ (${response.status})`);
    ok = true;
    ringState("ok");
    button.textContent = "สำเร็จ กำลังเข้าสู่ระบบ…";
    setTimeout(() => window.location.assign(data.next || "/"), 450);
  } catch (err) {
    ringState("fail");
    setTimeout(() => ringState(null), 750);
    errorBox.querySelector(".notice-text").textContent = err.message;
    errorBox.hidden = false;
    form.password.value = "";
    form.password.focus();
  } finally {
    if (!ok) {
      button.disabled = false;
      button.textContent = "เข้าสู่ระบบ";
    }
  }
});

// Show or hide the password, and warn when Caps Lock is on.
const toggle = document.getElementById("toggle-password");
toggle.addEventListener("click", () => {
  const show = form.password.type === "password";
  form.password.type = show ? "text" : "password";
  toggle.textContent = show ? "ซ่อน" : "แสดง";
  toggle.setAttribute("aria-pressed", String(show));
  toggle.setAttribute("aria-label", show ? "ซ่อนรหัสผ่าน" : "แสดงรหัสผ่าน");
  form.password.focus();
});
const caps = document.getElementById("caps-warning");
["keydown", "keyup"].forEach((type) => form.password.addEventListener(type, (e) => {
  if (e.getModifierState) caps.hidden = !e.getModifierState("CapsLock");
}));
form.password.addEventListener("blur", () => { caps.hidden = true; });
