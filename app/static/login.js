"use strict";
const form = document.getElementById("login-form");
const errorBox = document.getElementById("login-error");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorBox.hidden = true;
  const button = form.querySelector("button");
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
