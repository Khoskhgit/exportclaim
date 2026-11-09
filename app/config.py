"""Settings loaded from the environment / .env file."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")
DEFAULT_USAGE_NAME = "วิธีใช้ตามแพทย์สั่ง"


class ConfigError(ValueError):
    """Raised when required settings are missing or malformed."""


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _existing(path: Path) -> str:
    return str(path) if path.is_file() else ""


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = ""
    db_password: str = field(default="", repr=False)
    db_name: str = "hos"
    rcm_db: str = "rcm"
    hcode: str = ""
    hname: str = ""
    hosp_main: tuple[str, ...] = ()
    default_doctor: str = ""
    default_usage_code: str = "0201"
    default_usage_name: str = DEFAULT_USAGE_NAME
    station: str = "01"
    use_vn_as_invno: bool = True
    pdx_only: bool = False
    export_dir: Path = BASE_DIR / "exports"
    data_dir: Path = BASE_DIR / "data"
    # Login
    login_required: bool = True
    session_hours: int = 8
    login_groups: tuple[str, ...] = ()
    moph_health_id_client: str = ""
    moph_health_id_secret: str = field(default="", repr=False)
    moph_provider_id_client: str = ""
    moph_provider_id_secret: str = field(default="", repr=False)
    port: int = 5180
    host: str = "127.0.0.1"
    extra_hosts: tuple[str, ...] = ()
    moph_redirect_uri: str = "http://localhost:5180/auth/moph/callback"
    moph_allowed_hcodes: tuple[str, ...] = ()
    health_id_base_url: str = "https://moph.id.th"
    provider_id_base_url: str = "https://provider.id.th"
    moph_user_agent: str = "EMR"
    ssl_certfile: str = ""
    ssl_keyfile: str = ""
    # Financial Data Hub
    fdh_user: str = ""
    fdh_password: str = field(default="", repr=False)
    fdh_token_url: str = "https://fdh.moph.go.th"
    fdh_hash_key: str = field(default="$jwt@moph#", repr=False)
    # New e-Claim API (nhsoapi.nhso.go.th ecimp)
    nhso_user: str = ""
    nhso_password: str = field(default="", repr=False)

    @property
    def allowed_hosts(self) -> list[str]:
        """Host names the browser may use: this machine, HOST, ALLOWED_HOSTS and the MOPH redirect host."""
        from urllib.parse import urlsplit
        names = ["127.0.0.1", "localhost", *self.extra_hosts]
        if self.host not in {"0.0.0.0", "::", ""}:
            names.append(self.host)
        redirect = urlsplit(self.moph_redirect_uri).hostname
        if redirect:
            names.append(redirect)
        return list(dict.fromkeys(n.strip() for n in names if n.strip()))

    @property
    def moph_ready(self) -> bool:
        return all((self.moph_health_id_client, self.moph_health_id_secret,
                    self.moph_provider_id_client, self.moph_provider_id_secret, self.moph_redirect_uri))

    @property
    def moph_hcodes(self) -> tuple[str, ...]:
        """Hospitals whose staff may log in with MOPH ID: MOPH_ALLOWED_HCODES, or this hospital."""
        return self.moph_allowed_hcodes or ((self.hcode,) if self.hcode else ())

    def problems(self) -> list[str]:
        """Return the settings that must be fixed before the app can export."""
        found = []
        for name, value in (
            ("HOSXP_USER", self.db_user),
            ("HOSXP_DB", self.db_name),
            ("HCODE", self.hcode),
            ("HNAME", self.hname),
            ("DEFAULT_DOCTOR", self.default_doctor),
            ("HOSP_MAIN", self.hosp_main),
        ):
            if not value:
                found.append(f"ยังไม่ได้ตั้งค่า {name}")
        for name, value in (("HOSXP_DB", self.db_name), ("RCM_DB", self.rcm_db)):
            if value and not IDENTIFIER.match(value):
                found.append(f"{name} มีอักขระที่ไม่อนุญาต")
        return found


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file or BASE_DIR / ".env")
    get = os.environ.get
    return Settings(
        db_host=get("HOSXP_HOST", "127.0.0.1"),
        db_port=int(get("HOSXP_PORT", "3306")),
        db_user=get("HOSXP_USER", ""),
        db_password=get("HOSXP_PASSWORD", ""),
        db_name=get("HOSXP_DB", "hos"),
        rcm_db=get("RCM_DB", "rcm"),
        hcode=get("HCODE", ""),
        hname=get("HNAME", ""),
        hosp_main=_csv(get("HOSP_MAIN")),
        default_doctor=get("DEFAULT_DOCTOR", ""),
        default_usage_code=get("DEFAULT_USAGE_CODE", "0201"),
        default_usage_name=get("DEFAULT_USAGE_NAME") or DEFAULT_USAGE_NAME,
        station=get("STATION", "01").zfill(2),
        use_vn_as_invno=_bool(get("USE_VN_AS_INVNO"), True),
        pdx_only=_bool(get("PDX_ONLY"), False),
        export_dir=Path(get("EXPORT_DIR", str(BASE_DIR / "exports"))),
        data_dir=Path(get("DATA_DIR", str(BASE_DIR / "data"))),
        port=int(get("PORT") or 5180),
        host=(get("HOST") or "127.0.0.1").strip(),
        extra_hosts=_csv(get("ALLOWED_HOSTS")),
        login_required=_bool(get("LOGIN_REQUIRED"), True),
        session_hours=int(get("SESSION_HOURS", "8") or 8),
        login_groups=_csv(get("LOGIN_GROUPS")),
        # HEALTHID_* / PROVIDERID_* / HOSPITAL_CODE are the names on the MOPH key sheets.
        moph_health_id_client=(get("MOPH_HEALTH_ID_CLIENT") or get("HEALTHID_CLIENT_ID") or "").strip(),
        moph_health_id_secret=(get("MOPH_HEALTH_ID_SECRET") or get("HEALTHID_CLIENT_SECRET") or "").strip(),
        moph_provider_id_client=(get("MOPH_PROVIDER_ID_CLIENT") or get("PROVIDERID_CLIENT_ID") or "").strip(),
        moph_provider_id_secret=(get("MOPH_PROVIDER_ID_SECRET") or get("PROVIDERID_CLIENT_SECRET") or "").strip(),
        moph_redirect_uri=(get("MOPH_REDIRECT_URI") or get("HEALTHID_REDIRECT_URI")
                           or f"http://localhost:{int(get('PORT') or 5180)}/auth/moph/callback").strip(),
        moph_allowed_hcodes=_csv(get("MOPH_ALLOWED_HCODES") or get("HOSPITAL_CODE")),
        health_id_base_url=(get("HEALTHID_BASE_URL") or "https://moph.id.th").strip().rstrip("/"),
        provider_id_base_url=(get("PROVIDERID_API_URL") or "https://provider.id.th").strip().rstrip("/"),
        moph_user_agent=(get("MOPH_USER_AGENT") or "EMR").strip(),
        ssl_certfile=get("SSL_CERTFILE") or _existing(BASE_DIR / "data" / "tls" / "localhost.pem"),
        ssl_keyfile=get("SSL_KEYFILE") or _existing(BASE_DIR / "data" / "tls" / "localhost-key.pem"),
        fdh_user=(get("FDH_USER") or "").strip(),
        fdh_password=get("FDH_PASSWORD") or "",
        fdh_token_url=(get("FDH_TOKEN_URL") or "https://fdh.moph.go.th").strip().rstrip("/"),
        fdh_hash_key=get("FDH_HASH_KEY") or "$jwt@moph#",
        nhso_user=(get("NHSO_USER") or "").strip(),
        nhso_password=get("NHSO_PASSWORD") or "",
    )
