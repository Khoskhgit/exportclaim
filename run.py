"""Start the app on this computer only. Uses HTTPS when a certificate is configured (data/tls or SSL_CERTFILE)."""
import uvicorn

from app.config import load_settings

if __name__ == "__main__":
    settings = load_settings()
    tls = {}
    if settings.ssl_certfile and settings.ssl_keyfile:
        tls = {"ssl_certfile": settings.ssl_certfile, "ssl_keyfile": settings.ssl_keyfile}
    print(f"เปิดที่ {'https://localhost' if tls else 'http://127.0.0.1'}:8765")
    uvicorn.run("app.main:get_app", factory=True, host="127.0.0.1", port=8765, access_log=False, **tls)
