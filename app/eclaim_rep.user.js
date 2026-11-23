// ==UserScript==
// @name         KHH Claim Sure: ดึง REP e-Claim
// @namespace    khh-claim-sure
// @version      1.0
// @description  ดาวน์โหลด REP (Excel) จาก e-Claim สปสช. ที่ยังไม่ได้นำเข้า แล้วส่งเข้า KHH Claim Sure
// @match        https://eclaim.nhso.go.th/webComponent/validation/ValidationMainAction.do*
// @grant        GM_xmlhttpRequest
// @connect      __APP_HOST__
// @noframes
// ==/UserScript==

// The app builds this file per user (เมนู REP / STM > ติดตั้งสคริปต์ดึง REP e-Claim): APP and TOKEN are filled in.
// The script never logs in or touches the captcha: it runs inside the e-Claim session the user opened, reads the
// REP lists, downloads each REP's "download excel" file and posts it to /api/repstm/import of the app.
(function () {
  'use strict';
  const APP = '__APP_URL__';
  const TOKEN = '__TOKEN__';
  const HCODE = '__HCODE__';
  const LIST_URL = '/webComponent/validation/ValidationMainAction.do';
  const FUNDS = [['ucs', 'UCS บัตรทอง'], ['ofc', 'OFC ข้าราชการ'], ['lgo', 'LGO อปท.']];
  const PAUSE_MS = 800;

  const form = document.getElementById('form-search');
  if (!form) return; // detail pages (lev=DH) have no search form

  // ------------------------------------------------------------ panel
  const panel = document.createElement('div');
  panel.style.cssText = 'margin:10px 0;padding:10px 12px;border:2px solid #0a7d4f;border-radius:8px;background:#f2fbf6;' +
    'font:14px sans-serif;max-width:1000px';
  panel.innerHTML = `
    <b style="color:#0a7d4f">KHH Claim Sure</b> ดึง REP ที่ยังไม่ได้นำเข้า
    <span style="margin-left:12px">ย้อนหลัง
      <select id="khh-months"><option value="1">เดือนนี้</option><option value="2" selected>2 เดือน</option>
      <option value="3">3 เดือน</option><option value="6">6 เดือน</option><option value="12">12 เดือน</option></select>
    </span>
    <span id="khh-funds" style="margin-left:12px">${FUNDS.map(([v, label]) =>
      `<label style="margin-right:8px;font-weight:normal"><input type="checkbox" value="${v}" checked> ${label}</label>`).join('')}</span>
    <button type="button" id="khh-run" style="margin-left:8px;padding:3px 12px;background:#0a7d4f;color:#fff;border:0;border-radius:4px">
      ส่ง REP เข้า KHH Claim Sure</button>
    <div id="khh-log" style="margin-top:8px;max-height:260px;overflow:auto;font:12px/1.5 monospace;white-space:pre-wrap"></div>`;
  form.parentNode.insertBefore(panel, form.nextSibling);
  const logBox = panel.querySelector('#khh-log');
  const button = panel.querySelector('#khh-run');

  function log(message, color) {
    const line = document.createElement('div');
    line.textContent = message;
    if (color) line.style.color = color;
    logBox.appendChild(line);
    logBox.scrollTop = logBox.scrollHeight;
  }
  const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

  // ------------------------------------------------------------ app (KHH Claim Sure)
  function app(path, data, contentType) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'POST', url: APP + path, data,
        headers: { 'X-KHH-Token': TOKEN, 'Content-Type': contentType },
        timeout: 120000,
        onload: (r) => {
          let body = {};
          try { body = JSON.parse(r.responseText || '{}'); } catch (e) { body = { detail: r.responseText.slice(0, 200) }; }
          if (r.status === 401) reject(new Error('KHH Claim Sure ไม่รับรหัสของสคริปต์ ให้ติดตั้งสคริปต์ใหม่จากหน้า REP / STM'));
          else if (r.status >= 400) reject(new Error(body.detail || `HTTP ${r.status}`));
          else resolve(body);
        },
        onerror: () => reject(new Error(`ติดต่อ ${APP} ไม่ได้ (เปิดแอปในแท็บอื่นและยอมรับ certificate ก่อน หรือเครื่องนี้เข้า server ไม่ได้)`)),
        ontimeout: () => reject(new Error(`${APP} ตอบช้าเกินไป`)),
      });
    });
  }

  // ------------------------------------------------------------ e-Claim
  function months(count) {
    const now = new Date();
    const out = [];
    for (let k = 0; k < count; k++) {
      const d = new Date(now.getFullYear(), now.getMonth() - k, 1);
      out.push({ mo: String(d.getMonth() + 1), ye: String(d.getFullYear() + 543) });
    }
    return out;
  }

  async function listReps(fund, mo, ye) {
    const fields = new URLSearchParams(new FormData(form));
    fields.set('mo', mo); fields.set('ye', ye); fields.set('maininscl', fund); fields.set('repNo', '');
    if (!fields.get('zp')) fields.set('zp', HCODE);
    const response = await fetch(LIST_URL, { method: 'POST', body: fields, credentials: 'same-origin' });
    const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
    if (!doc.getElementById('form-search')) throw new Error('e-Claim ไม่แสดงหน้ารายการ (session หมดอายุ? login ใหม่แล้วกดอีกครั้ง)');
    const table = doc.getElementById('content2');
    if (!table) return [];
    const heads = [...table.querySelectorAll('thead th')].map((th) => th.textContent.trim());
    const col = (name) => heads.findIndex((h) => h === name);
    const iRep = col('ลำดับที่'), iFile = col('File'), iExcel = col('Excel File');
    if (iRep < 0 || iExcel < 0) throw new Error(`หน้า ${fund} ${mo}/${ye} ไม่มีคอลัมน์ ลำดับที่ / Excel File (เว็บ e-Claim เปลี่ยนรูปแบบ?)`);
    const rows = [];
    for (const tr of table.querySelectorAll('tbody tr')) {
      const cells = tr.querySelectorAll('td');
      const repNo = (cells[iRep] && cells[iRep].textContent || '').trim();
      const link = cells[iExcel] && cells[iExcel].querySelector('a[href]');
      if (!/^\d+$/.test(repNo) || !link) continue;
      const fn = (iFile >= 0 && cells[iFile] ? cells[iFile].textContent.trim() : '') || `eclaim_${repNo}`;
      rows.push({ repNo, fund, url: new URL(link.getAttribute('href'), location.href).href, name: fn.replace(/\.ecd$/i, '') + '.xls' });
    }
    return rows;
  }

  async function download(row) {
    const response = await fetch(row.url, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`ดาวน์โหลดไม่ได้ HTTP ${response.status}`);
    const data = await response.arrayBuffer();
    const head = new Uint8Array(data.slice(0, 64));
    const text = String.fromCharCode(...head).trimStart().toLowerCase();
    if (text.startsWith('<')) throw new Error('ได้หน้าเว็บแทนไฟล์ Excel (session หมดอายุ? login ใหม่แล้วกดอีกครั้ง)');
    return data;
  }

  // ------------------------------------------------------------ run
  async function run() {
    const funds = [...panel.querySelectorAll('#khh-funds input:checked')].map((i) => i.value);
    const span = months(Number(panel.querySelector('#khh-months').value));
    logBox.textContent = '';
    if (!funds.length) { log('เลือกสิทธิอย่างน้อย 1 สิทธิ', '#b00'); return; }

    const found = new Map();
    for (const fund of funds) {
      for (const { mo, ye } of span) {
        const rows = await listReps(fund, mo, ye);
        rows.forEach((r) => found.has(r.repNo) || found.set(r.repNo, r));
        log(`${fund.toUpperCase()} ${mo}/${ye}: ${rows.length} REP`);
        await sleep(300);
      }
    }
    if (!found.size) { log('ไม่พบ REP ในช่วงที่เลือก'); return; }

    const known = new Set((await app('/api/repstm/known', JSON.stringify({ rep_nos: [...found.keys()] }), 'application/json')).known);
    const todo = [...found.values()].filter((r) => !known.has(r.repNo));
    log(`พบ ${found.size} REP นำเข้าแล้ว ${known.size} ต้องดึงใหม่ ${todo.length}`, '#0a7d4f');

    let ok = 0, failed = 0;
    for (const row of todo) {
      try {
        const data = await download(row);
        const result = await app(`/api/repstm/import?name=${encodeURIComponent(row.name)}`, new Blob([data]), 'application/octet-stream');
        ok += 1;
        log(`✔ ${row.repNo} ${row.name}: ${result.summary || 'นำเข้าแล้ว'}`, '#0a7d4f');
      } catch (error) {
        failed += 1;
        log(`✘ ${row.repNo} ${row.name}: ${error.message}`, '#b00');
        if (/session|certificate|ไม่รับรหัส|ติดต่อ/.test(error.message)) break;
      }
      await sleep(PAUSE_MS);
    }
    log(`เสร็จ: นำเข้า ${ok} ไฟล์${failed ? ` ไม่สำเร็จ ${failed}` : ''} ดูผลที่ ${APP}/repstm`, failed ? '#b00' : '#0a7d4f');
  }

  button.addEventListener('click', async () => {
    button.disabled = true;
    try { await run(); } catch (error) { log(`✘ ${error.message}`, '#b00'); }
    finally { button.disabled = false; }
  });
})();
