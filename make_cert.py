"""Create a self-signed HTTPS certificate in data/tls/ for this server.

Usage:  .venv/bin/python make_cert.py [extra names or IPs ...]
The certificate covers localhost, 127.0.0.1, HOST, ALLOWED_HOSTS and the MOPH redirect host from .env.
Needs the openssl command.
"""
import ipaddress
import shutil
import subprocess
import sys
from pathlib import Path

from app.config import BASE_DIR, load_settings


def main() -> None:
    if not shutil.which("openssl"):
        raise SystemExit("ไม่พบคำสั่ง openssl กรุณาติดตั้งก่อน")
    settings = load_settings()
    names = list(dict.fromkeys([*settings.allowed_hosts, *sys.argv[1:]]))
    entries = []
    for name in names:
        try:
            ipaddress.ip_address(name)
            entries.append(f"IP:{name}")
        except ValueError:
            entries.append(f"DNS:{name}")
    folder = BASE_DIR / "data" / "tls"
    folder.mkdir(parents=True, exist_ok=True)
    key, cert = folder / "localhost-key.pem", folder / "localhost.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825",
        "-subj", f"/CN={names[0] if names else 'localhost'}",
        "-addext", "subjectAltName=" + ",".join(entries),
        "-addext", "extendedKeyUsage=serverAuth",
        "-keyout", str(key), "-out", str(cert),
    ], check=True, capture_output=True)
    Path(key).chmod(0o600)
    print(f"สร้าง certificate แล้ว: {cert}")
    print("ใช้ได้กับ: " + ", ".join(names))


if __name__ == "__main__":
    main()
