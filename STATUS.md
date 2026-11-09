# สถานะงาน (อัปเดต 2026-11-08)

## ใช้งานได้แล้ว
| ส่วน | สถานะ | หมายเหตุ |
|---|---|---|
| ส่งออก SSOP (ผู้ป่วยนอก) | ใช้ได้ | ค้น ตรวจ เลือก ติ๊กไม่ส่ง สร้าง ZIP TFlag A/E/D กรอก Station เองได้ |
| ส่งออก AIPN (ผู้ป่วยใน) | ใช้ได้ | สร้าง XML ตรงกับโปรแกรมเดิมทุกไบต์ 6 จาก 8 ไฟล์ (อีก 2 ไฟล์ต่างเพราะข้อมูล HOSxP ถูกแก้หลังส่ง) แก้ราคาและ CareAs ได้ |
| ส่ง FDH จากหน้า 18 แฟ้ม | ทดสอบเชื่อมต่อได้ | ขอ Token ได้แล้ว UAT ตอบกลับได้ ยังไม่ได้ส่งข้อมูลจริงทั้ง UAT และ PROD |
| ส่ง New e-Claim API จากหน้า 18 แฟ้ม | รอสิทธิ์ API | โปรแกรมพร้อม แต่ สปสช. ตอบว่าบัญชีนี้ยังไม่ได้เข้าร่วมโครงการส่งข้อมูลผ่าน API ต้องสมัครที่ apieclaim@nhso.go.th |
| login บัญชี HOSxP | ใช้ได้ | อ่าน `opduser` อย่างเดียว ล็อก 15 นาทีเมื่อผิด 5 ครั้ง |
| login MOPH ID | ใช้ได้ | Health ID + Provider ID เข้าได้เฉพาะบุคลากร รพ.คลองหาด (10866) ส่ง `User-Agent: EMR` เพราะ Cloudflare ของ provider.id.th บล็อกชื่อ Python |
| HTTPS | ใช้ได้ | certificate สร้างเองใน `data/tls/` (`make_cert.py`) |
| GitHub | ขึ้นแล้ว | https://github.com/Khoskhgit/exportclaim branch `main` |
| Test | ผ่าน 188 รายการ | `.venv/bin/python -m pytest -q` |

## เครื่องที่ใช้
- **เครื่องพัฒนา (192.168.11.20):** https://localhost:5180 เปิดเฉพาะเครื่องตัวเอง
- **Server (192.168.80.88):** โฟลเดอร์ `/var/khclaim/exportclaim` ตั้ง `HOST=0.0.0.0`, `ALLOWED_HOSTS=192.168.80.88`, `PORT=5180` รันเป็น systemd service `exportclaim` ด้วย user `exportclaim`

อัปเดตโปรแกรมบน server:
```bash
cd /var/khclaim/exportclaim && git pull && chown -R exportclaim:exportclaim . && systemctl restart exportclaim
```

## ยังค้างหรือยังไม่ได้ยืนยัน
1. **Server 192.168.80.88:** ยังไม่ได้ยืนยันว่า service ขึ้น `active (running)` และเครื่องอื่นเข้า https://192.168.80.88:5180 ได้ (ต้องเปิด firewall port 5180)
2. **MOPH ID บน server:** redirect URL `https://192.168.80.88:5180/auth/healthid/callback` อาจต้องลงทะเบียนกับ MOPH ID ถ้า login ไม่ผ่าน
3. **สิทธิ์ฐานข้อมูล:** user `qkhh_reader` มีสิทธิ์ `ALL PRIVILEGES` ควรสร้าง user ที่มีแค่ SELECT + EXECUTE ให้โปรแกรมนี้
4. **ทดลองส่งจริง:** ยังไม่ได้อัปโหลด ZIP SSOP/AIPN ที่สร้างจากโปรแกรมนี้เข้าระบบจริงและดูผลตอบกลับ (SSOP VN ที่ติด C ต้องส่งใหม่ด้วย TFlag E)
5. **SSOP:** รหัสผลตอบกลับ 13 รหัสยังไม่มีคำอธิบายใน HOSxP นำเข้า REP/STM ได้ที่เมนู REP / STM แล้ว
6. **AIPN:** เคส ODS, ฟอกเลือด (PF), มะเร็ง (SSOCAC), โควิด ยังต้องส่งด้วยโปรแกรมเดิม ยังไม่ได้นำเข้าไฟล์ REP/RESULT
7. **Login HOSxP:** ผู้ใช้ทดสอบบัญชีจริงผ่านแล้ว 1 บัญชี ยังไม่ได้ตั้ง `LOGIN_GROUPS` (ตอนนี้ผู้ใช้ HOSxP ที่เปิดใช้งานทุกคนเข้าได้)
8. **FDH:** ยังไม่ได้ลองส่ง UAT/PROD ด้วยข้อมูลจริงและดูผลตอบกลับ คอลัมน์ที่ FDH มีแต่โปรแกรมเดิมไม่ได้เติม (OOP.SERVPRICE, CHT.INVOICE_NO ฯลฯ) ยังว่าง ควรลบ `exports/api_fdh.txt` และ `exports/api_eclaim_new.txt` เพราะมีรหัสผ่านของ รพ. อื่น
9. **New e-Claim API:** ขอ Token ไม่ได้เพราะบัญชียังไม่ได้เข้าร่วมโครงการ API ของ สปสช. เมื่อได้สิทธิ์แล้วให้กด "ทดสอบ Token" แล้วลองส่งจำนวนน้อยก่อน
10. **Walk-in บัตรทองหลาย visit:** รวมเข้า VN แรกแล้ว (ทดลองกับข้อมูลจริง 1 คู่ ยอด CHT ตรง) ยังไม่ได้ส่งจริงดูว่า สปสช. รับและจ่ายตามที่คาด
11. **REP / STM:** นำเข้าได้เฉพาะ SSOP (SOCDBIL/SOCDDIS/SOGNSTMP) ทดลองกับไฟล์จริง 1 REP และ 1 STM แล้ว ยังไม่รองรับ REP/STM ของ AIPN และ สปสช.

## ไฟล์ที่ไม่อยู่ใน git
`.env` (รหัสผ่านและ key), `data/` (SQLite, session, certificate), `exports/` และไฟล์ ZIP/XML ที่มีข้อมูลผู้ป่วย ต้องคัดลอกหรือสร้างใหม่บนแต่ละเครื่อง
