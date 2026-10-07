"""Đọc và kiểm tra cấu hình từ file .env."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    pass


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def mask_uid(uid: str) -> str:
    """A123456789 -> A12****789 (dùng cho log / email / LLM)."""
    if len(uid) < 7:
        return "*" * len(uid)
    return uid[:3] + "*" * (len(uid) - 6) + uid[-3:]


@dataclass(frozen=True)
class Settings:
    uid: str
    birthday: str
    captcha_mode: str
    captcha_solver_cmd: str
    max_captcha_retries: int
    headless: bool
    gmail_address: str
    gmail_app_password: str
    mail_to: list[str] = field(default_factory=list)
    output_dir: Path = PROJECT_ROOT / "output"
    openai_model: str = "gpt-4o-mini"
    browser_channel: str = "chrome"

    @property
    def masked_uid(self) -> str:
        return mask_uid(self.uid)


def load_settings(require_mvdis: bool = True, require_mail: bool = True) -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")

    uid = os.getenv("MVDIS_UID", "").strip().upper()
    birthday = os.getenv("MVDIS_BIRTHDAY", "").strip()
    errors: list[str] = []

    if require_mvdis:
        if not re.fullmatch(r"[A-Z][A-Z0-9]\d{8}", uid):
            errors.append("MVDIS_UID trống hoặc sai định dạng (ví dụ A123456789).")
        if not re.fullmatch(r"\d{7}", birthday):
            errors.append("MVDIS_BIRTHDAY phải là 7 chữ số theo lịch Dân Quốc (ví dụ 0780702).")

    captcha_mode = os.getenv("CAPTCHA_MODE", "manual").strip().lower()
    captcha_solver_cmd = os.getenv("CAPTCHA_SOLVER_CMD", "").strip()
    if captcha_mode not in {"manual", "external"}:
        errors.append("CAPTCHA_MODE chỉ nhận 'manual' hoặc 'external'.")
    if captcha_mode == "external" and not captcha_solver_cmd:
        errors.append("CAPTCHA_MODE=external nhưng CAPTCHA_SOLVER_CMD đang trống.")

    gmail_address = os.getenv("GMAIL_ADDRESS", "").strip()
    # App Password Gmail luôn là 16 chữ cái; bỏ khoảng trắng và dấu nháy lỡ gõ thừa.
    gmail_app_password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "").strip("\"'")
    mail_to = [m.strip() for m in os.getenv("MAIL_TO", "").split(",") if m.strip()]
    if require_mail:
        if not gmail_address or not gmail_app_password:
            errors.append("Thiếu GMAIL_ADDRESS hoặc GMAIL_APP_PASSWORD.")
        if not mail_to:
            errors.append("MAIL_TO trống.")

    if errors:
        raise ConfigError("Cấu hình .env không hợp lệ:\n- " + "\n- ".join(errors))

    output_dir = Path(os.getenv("OUTPUT_DIR", "./output"))
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        uid=uid,
        birthday=birthday,
        captcha_mode=captcha_mode,
        captcha_solver_cmd=captcha_solver_cmd,
        max_captcha_retries=int(os.getenv("MAX_CAPTCHA_RETRIES", "3") or 3),
        headless=_bool(os.getenv("HEADLESS"), default=False),
        gmail_address=gmail_address,
        gmail_app_password=gmail_app_password,
        mail_to=mail_to,
        output_dir=output_dir,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
        browser_channel=os.getenv("BROWSER_CHANNEL", "chrome").strip(),
    )
