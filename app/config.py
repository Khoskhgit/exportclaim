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
    dental_pttypes: tuple[str, ...] = ("29",)
    disabled_pttypes: tuple[str, ...] = ("33",)
    ofc_approve_pttypes: tuple[str, ...] = ("21",)
    # PPFS 27) child iron: fees 3004084 (6-12 months) and 3004023 (2-5 years), keyed under the placeholder ADP 10000
    # ("NONE"). The ZIP sends the code of the drug form given, as the user set: syrup 689697, tablets 776508.
    ppfs_iron_child_icodes: tuple[str, ...] = ("3004084",)
    ppfs_iron_child2_icodes: tuple[str, ...] = ("3004023",)
    ppfs_iron_syrup_adp: str = "689697"
    ppfs_iron_tablet_adp: str = "776508"
    # PPFS 26) child Hb/Hct screening (fees 3003783 6-12 months, 3004022 3-5 years): sent with the ADP 10000 HOSxP
    # has, as the user set.
    ppfs_anemia_child_adp: str = "10000"
    ppfs_anemia_child_icodes: tuple[str, ...] = ("3003783",)
    ppfs_anemia_child2_icodes: tuple[str, ...] = ("3004022",)
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
    # HOSxP user/password login; off by default since 2026-11-18 (the user asked for MOPH ID only). LOGIN_HOSXP=1 opens it.
    hosxp_login: bool = False
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
    # e-Claim web (ดึง REP): the same login with TOTP set on iam.nhso.go.th; ECLAIM_USER/PASSWORD override NHSO_*
    eclaim_user: str = ""
    eclaim_password: str = field(default="", repr=False)
    eclaim_totp_secret: str = field(default="", repr=False)
    # AIPN web (cs8.chi.or.th/aipnupload, สกส.): pull REP RP1 and Statement AIPN
    aipn_hcode: str = ""
    aipn_user: str = ""
    aipn_password: str = field(default="", repr=False)
    # Daily automatic REP pull (e-Claim + Seamless) at HH:MM (Asia/Bangkok); empty = off
    auto_pull_time: str = ""
    auto_pull_months: int = 2
    # LINE Messaging API (LINE Official Account): the summary goes to LINE_TO (a user ID "U..."), or to every
    # friend of the account (broadcast) when LINE_TO is empty
    line_channel_token: str = field(default="", repr=False)
    line_to: str = ""
    # MOPH Alert (MOPH Notify): the keys of one LINE group; used instead of LINE_CHANNEL_TOKEN when set
    moph_alert_client_key: str = field(default="", repr=False)
    moph_alert_secret_key: str = field(default="", repr=False)
    moph_alert_url: str = ""
    # MMS ประกันสังคม (mms.sso.go.th): dental REP / STM reports
    mms_user: str = ""
    mms_password: str = field(default="", repr=False)
    mms_hosp_code: str = ""

    @property
    def allowed_hosts(self) -> list[str]:
        """Host names the browser may use: this machine, HOST, ALLOWED_HOSTS and the MOPH redirect host."""
        from urllib.parse import urlsplit
        names = ["127.0.0.1", "localhost", *self.extra_hosts]
        if self.host not in {"0.0.0.0", "::", ""}:
            names.append(self.host)
        for uri in self.moph_redirect_uris:
            redirect = urlsplit(uri).hostname
            if redirect:
                names.append(redirect)
        return list(dict.fromkeys(n.strip() for n in names if n.strip()))

    @property
    def moph_redirect_uris(self) -> tuple[str, ...]:
        """MOPH_REDIRECT_URI may list several URLs (comma-separated), one per address people use."""
        return tuple(u.strip() for u in self.moph_redirect_uri.split(",") if u.strip())

    def moph_redirect_for(self, host: str) -> str:
        """The redirect URL on the host the browser is using now, else the first one listed."""
        from urllib.parse import urlsplit
        uris = self.moph_redirect_uris
        host = (host or "").lower()
        return next((u for u in uris if urlsplit(u).netloc.lower() == host), uris[0] if uris else "")

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
        dental_pttypes=_csv(get("DENTAL_PTTYPES")) or ("29",),
        disabled_pttypes=_csv(get("DISABLED_PTTYPES")) or ("33",),
        ofc_approve_pttypes=_csv(get("OFC_APPROVE_PTTYPES")) or ("21",),
        ppfs_iron_child_icodes=_csv(get("PPFS_IRON_CHILD_ICODES")) or ("3004084",),
        ppfs_iron_child2_icodes=_csv(get("PPFS_IRON_CHILD2_ICODES")) or ("3004023",),
        ppfs_iron_syrup_adp=(get("PPFS_IRON_SYRUP_ADP") or "").strip() or "689697",
        ppfs_iron_tablet_adp=(get("PPFS_IRON_TABLET_ADP") or "").strip() or "776508",
        ppfs_anemia_child_adp=(get("PPFS_ANEMIA_CHILD_ADP") or "").strip() or "10000",
        ppfs_anemia_child_icodes=_csv(get("PPFS_ANEMIA_CHILD_ICODES")) or ("3003783",),
        ppfs_anemia_child2_icodes=_csv(get("PPFS_ANEMIA_CHILD2_ICODES")) or ("3004022",),
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
        hosxp_login=_bool(get("LOGIN_HOSXP"), False),
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
        eclaim_user=(get("ECLAIM_USER") or get("NHSO_USER") or "").strip(),
        eclaim_password=get("ECLAIM_PASSWORD") or get("NHSO_PASSWORD") or "",
        eclaim_totp_secret=(get("ECLAIM_TOTP_SECRET") or "").strip(),
        aipn_hcode=(get("AIPN_HCODE") or get("HCODE") or "").strip(),
        aipn_user=(get("AIPN_USER") or "").strip(),
        aipn_password=get("AIPN_PASSWORD") or "",
        auto_pull_time=(get("AUTO_PULL_TIME") or "").strip(),
        auto_pull_months=int(get("AUTO_PULL_MONTHS") or 2),
        line_channel_token=(get("LINE_CHANNEL_TOKEN") or "").strip(),
        line_to=(get("LINE_TO") or "").strip(),
        moph_alert_client_key=(get("MOPH_ALERT_CLIENT_KEY") or "").strip(),
        moph_alert_secret_key=(get("MOPH_ALERT_SECRET_KEY") or "").strip(),
        moph_alert_url=(get("MOPH_ALERT_URL") or "").strip(),
        mms_user=(get("MMS_USER") or "").strip(),
        mms_password=get("MMS_PASSWORD") or "",
        mms_hosp_code=(get("MMS_HOSP_CODE") or "").strip() or (f"00{(get('HCODE') or '').strip().zfill(5)}00" if (get("HCODE") or "").strip() else ""),
    )
