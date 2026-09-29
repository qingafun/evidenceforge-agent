"""Desktop profiles. Secrets are protected for the current Windows account."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Settings


class DesktopSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    base_url: str = "https://api.openai.com/v1"
    model: str = Field(default="", max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)
    tavily_api_key: str | None = Field(default=None, max_length=4096)
    max_tokens: int = Field(default=64000, ge=1000, le=200000)
    max_output_tokens: int = Field(default=4096, ge=256, le=32768)
    request_timeout: float = Field(default=60, ge=1, le=180)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                     and not parsed.username and not parsed.password
                     and not parsed.query and not parsed.fragment)
            _ = parsed.port
        except ValueError:
            valid = False
        if not valid or len(value) > 2000:
            raise ValueError("请填写不含账号、密码或查询参数的 HTTP(S) 服务地址。")
        value = value.rstrip("/")
        if value.endswith("/chat/completions"):
            raise ValueError("请填写 Base URL（通常以 /v1 结尾），无需添加 /chat/completions。")
        return value


class ConnectionTestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    base_url: str
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)
    request_timeout: float = Field(default=15, ge=1, le=180)

    _base_url = field_validator("base_url")(DesktopSettingsInput.validate_base_url.__func__)


def default_profile_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "EvidenceForge"


def protect_secret(value: str) -> str:
    if not value:
        return ""
    if os.name != "nt":
        raise RuntimeError("桌面版密钥保存需要 Windows DPAPI。")
    import win32crypt

    encrypted = win32crypt.CryptProtectData(value.encode("utf-8"), "EvidenceForge", None, None, None, 0)
    return base64.b64encode(encrypted).decode("ascii")


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    if os.name != "nt":
        raise RuntimeError("此加密配置只能由原 Windows 用户读取。")
    import win32crypt

    return win32crypt.CryptUnprotectData(base64.b64decode(value, validate=True), None, None, None, 0)[1].decode("utf-8")


class DesktopSettingsStore:
    def __init__(self, profile_dir: Path):
        self.profile_dir = Path(profile_dir).resolve()
        self.path = self.profile_dir / "settings.json"

    @property
    def first_run(self) -> bool:
        return not self.path.exists()

    def load(self) -> dict:
        defaults = DesktopSettingsInput().model_dump()
        defaults.update(api_key="", tavily_api_key="")
        if not self.path.exists():
            return defaults
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if raw.pop("version") != 1:
                raise ValueError("Unsupported profile")
            raw["api_key"] = unprotect_secret(raw.pop("protected_api_key", ""))
            raw["tavily_api_key"] = unprotect_secret(raw.pop("protected_tavily_api_key", ""))
            return DesktopSettingsInput.model_validate(raw).model_dump()
        except Exception:
            raise RuntimeError("无法读取桌面配置。请使用保存配置的 Windows 账号，或备份后移走 settings.json 重新设置。") from None

    def save(self, values: dict) -> None:
        payload = DesktopSettingsInput.model_validate(values).model_dump()
        payload["protected_api_key"] = protect_secret(payload.pop("api_key") or "")
        payload["protected_tavily_api_key"] = protect_secret(payload.pop("tavily_api_key") or "")
        payload["version"] = 1
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def settings(self) -> Settings:
        # Explicit fields and no dotenv prevent packaging-time or cwd secrets
        # from leaking into a fresh desktop profile.
        return Settings(_env_file=None, data_dir=self.profile_dir / "data", **self.load())

    def public(self, settings: Settings, active_runs: int = 0) -> dict:
        return {
            "model": settings.model, "base_url": settings.base_url,
            "api_key_set": bool(settings.api_key), "tavily_api_key_set": bool(settings.tavily_api_key),
            "model_configured": settings.live_available, "web_search_configured": bool(settings.tavily_api_key),
            "max_tokens": settings.max_tokens, "max_output_tokens": settings.max_output_tokens,
            "request_timeout": settings.request_timeout, "data_dir": str(settings.data_dir),
            "first_run": self.first_run, "storage": "Windows DPAPI", "active_runs": active_runs,
        }


class ProfileLock:
    """The OS releases this lock even after a crash; no stale PID file cleanup."""

    def __init__(self, profile_dir: Path):
        self.path = Path(profile_dir) / "desktop.lock"
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, 2)
        if not self.handle.tell():
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise RuntimeError("EvidenceForge 已在运行，请切换到已打开的窗口。") from None
        return self

    def __exit__(self, *_):
        if self.handle:
            self.handle.close()
            self.handle = None
