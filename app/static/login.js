"use strict";
const form = document.getElementById("login-form");
const errorBox = document.getElementById("login-error");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorBox.hidden = true;
  const button = form.querySelector(".login-submit");
  button.disabled = true;
  button.textContent = "กำลังตรวจสอบ…";
  try {
    const response = await fetch("/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: form.username.value.trim(), password: form.password.value }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `เข้าสู่ระบบไม่สำเร็จ (${response.status})`);
    window.location.assign(data.next || "/");
  } catch (err) {
    errorBox.querySelector(".notice-text").textContent = err.message;
    errorBox.hidden = false;
    form.password.value = "";
    form.password.focus();
  } finally {
    button.disabled = false;
    button.textContent = "เข้าสู่ระบบ";
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
