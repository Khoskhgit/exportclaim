"""Start the app. HOST=127.0.0.1 (default) serves this computer only; set HOST=0.0.0.0 to serve the network.

Uses HTTPS when a certificate is configured (data/tls or SSL_CERTFILE).
"""
import uvicorn

from app.config import load_settings

if __name__ == "__main__":
    settings = load_settings()
    tls = {}
    if settings.ssl_certfile and settings.ssl_keyfile:
        tls = {"ssl_certfile": settings.ssl_certfile, "ssl_keyfile": settings.ssl_keyfile}
    elif settings.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("เปิดให้เครื่องอื่นใช้ต้องมี HTTPS: สร้าง certificate ด้วย make_cert.py ก่อน")
    scheme = "https" if tls else "http"
    for name in settings.allowed_hosts:
        print(f"เปิดที่ {scheme}://{name}:{settings.port}")
    uvicorn.run("app.main:get_app", factory=True, host=settings.host, port=settings.port, access_log=False, **tls)
