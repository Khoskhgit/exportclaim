"""Krungthai Corporate Online (bizgrowing.krungthai.com) > Medical Welfare > Healthcare Online: today's card
transactions of the hospital as healthcare_online_dd-mm-yyyy.csv, the file the Approve Code page imports.

The site sits behind a bot screen that turns plain HTTP clients away, so this drives a headless Chrome with
Playwright, like the MMS pull: log in with KTB_COMPANY_ID / KTB_USER_ID / KTB_PASSWORD (no captcha; the account
asks no 2FA), open Healthcare Online, search each merchant number for today 00:00-23:59 and press "Export CSV".
The page only offers today (its Post Date can't be changed), so the pull runs late in the evening.

It only reads: no other menu (Manual Keyin, Manual Adjustment) is touched, and it logs out at the end. A refused
login is never tried again in the same run, so the account is not locked by retries.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

log = logging.getLogger("ssop.ktb_web")
LOGIN_URL = "https://www.bizgrowing.krungthai.com/corporate/"
HOME = "**/ktbgovHealthCare/main**"


class KtbWebError(Exception):
    pass


@dataclass
class KtbWeb:
    company_id: str
    user_id: str
    password: str
    headless: bool = True
    timeout: int = 60_000

    def online_csvs(self) -> list[tuple[str, bytes]]:
        """Today's Healthcare Online export of every merchant number, as (file name, bytes); the same file once."""
        if not (self.company_id and self.user_id and self.password):
            raise KtbWebError("ยังไม่ได้ตั้งค่า KTB_COMPANY_ID / KTB_USER_ID / KTB_PASSWORD ใน .env")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise KtbWebError("ยังไม่ได้ติดตั้ง Playwright: pip install playwright แล้ว playwright install chromium") from None
        with sync_playwright() as pw:
            try:
                try:
                    browser = pw.chromium.launch(headless=self.headless, channel="chrome")
                except Exception:  # noqa: BLE001 - no Google Chrome: the bundled Chromium
                    browser = pw.chromium.launch(headless=self.headless)
            except Exception as exc:  # noqa: BLE001
                raise KtbWebError(f"เปิดเบราว์เซอร์ไม่ได้ (playwright install chromium): {exc}") from None
            try:
                ctx = browser.new_context(locale="th-TH", accept_downloads=True, viewport={"width": 1366, "height": 1000})
                ctx.set_default_timeout(self.timeout)
                page = ctx.new_page()
                self._login(page)
                try:
                    return self._export(page)
                finally:
                    self._logout(page)
            finally:
                browser.close()

    def _login(self, page) -> None:
        page.goto(LOGIN_URL, wait_until="networkidle")
        page.fill("#loginform input[name=companyId]", self.company_id)
        page.fill("#loginform input[name=username]", self.user_id)
        page.fill("#password-field", self.password)
        page.click("#loginButton")
        try:
            page.wait_for_url(HOME, timeout=45_000)
        except Exception:  # noqa: BLE001 - still on the login page: say why, never try again
            if page.is_visible("#twoFAPassword"):
                raise KtbWebError("KTB ขอรหัส 2FA ดึงอัตโนมัติไม่ได้") from None
            message = page.evaluate("""() => [...document.querySelectorAll('.error, .alert, .errorMsg, #errorMsg, .modal-body')]
                .map(e => e.innerText.trim()).filter(Boolean).join(' ').slice(0, 200)""")
            raise KtbWebError(f"เข้าสู่ระบบ KTB ไม่ผ่าน{': ' + message if message else ''}") from None
        page.wait_for_load_state("networkidle")

    def _export(self, page) -> list[tuple[str, bytes]]:
        page.evaluate("() => loadPortal.call(onlineMenu, 'OL', 'OL001')")
        page.wait_for_function("""() => { const d = document.querySelector('#dateinput');
            return d && d.value && document.querySelectorAll('#merchantno option').length > 0 }""")
        page.wait_for_timeout(1000)
        merchants = page.eval_on_selector_all("#merchantno option", "os => os.map(o => o.value).filter(Boolean)")
        files, seen = [], set()
        for merchant in merchants:
            page.select_option("#merchantno", merchant)
            with page.expect_response(lambda r: "healthcareOnline/search" in r.url):
                page.click("#doSearch")
            page.wait_for_timeout(1500)
            if not page.is_visible("#doExport"):  # no transaction today: the page hides the button
                log.info("KTB merchant %s: no transactions", merchant)
                continue
            with page.expect_download() as waiting:
                page.click("#doExport")
            download = waiting.value
            data = open(download.path(), "rb").read()
            digest = hashlib.sha256(data).hexdigest()
            if digest not in seen:
                seen.add(digest)
                files.append((download.suggested_filename or "healthcare_online.csv", data))
        return files

    @staticmethod
    def _logout(page) -> None:
        try:
            page.click("#logout", timeout=5_000)
            page.wait_for_timeout(2_000)
        except Exception:  # noqa: BLE001 - the session ends by itself after 15 minutes
            log.info("KTB logout skipped")
