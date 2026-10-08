"""Lấy mã captcha: tự giải bằng OCR (ddddocr), nhập tay, hoặc gọi lệnh ngoài.

Chế độ:
- "auto"     : tự giải captcha bằng thư viện ddddocr (không cần người ngồi trước
               máy). Nếu sai, vòng lặp tra cứu sẽ tải captcha mới và thử lại.
- "manual"   : in đường dẫn ảnh và chờ bạn gõ mã qua bàn phím.
- "external" : gọi lệnh trong CAPTCHA_SOLVER_CMD (do bạn tự cấu hình) với đường
               dẫn ảnh làm tham số, rồi đọc dòng đầu tiên của stdout.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .config import Settings

log = logging.getLogger(__name__)

_ddddocr_instance = None


class CaptchaUnavailable(Exception):
    pass


def get_captcha_code(image_path: Path, settings: Settings) -> str:
    if settings.captcha_mode == "auto":
        return _auto(image_path)
    if settings.captcha_mode == "external":
        return _external(image_path, settings.captcha_solver_cmd)
    return _manual(image_path)


def _get_ddddocr():
    """Khởi tạo DdddOcr một lần rồi tái sử dụng (nạp model ONNX khá tốn thời gian)."""
    global _ddddocr_instance
    if _ddddocr_instance is None:
        try:
            import ddddocr
        except ImportError as exc:
            raise CaptchaUnavailable(
                "CAPTCHA_MODE=auto cần thư viện ddddocr. Cài bằng: pip install ddddocr"
            ) from exc
        _ddddocr_instance = ddddocr.DdddOcr(show_ad=False)
    return _ddddocr_instance


def _auto(image_path: Path) -> str:
    ocr = _get_ddddocr()
    code = ocr.classification(image_path.read_bytes()).strip()
    if not code:
        raise CaptchaUnavailable("ddddocr không nhận diện được mã captcha.")
    log.info("ddddocr giải captcha %s -> %s", image_path.name, code)
    return code


def _manual(image_path: Path) -> str:
    print(f"\nẢnh captcha đã lưu tại: {image_path}")
    try:
        code = input("Nhập mã captcha (Enter để bỏ qua): ").strip()
    except EOFError as exc:  # chạy nền, không có terminal
        raise CaptchaUnavailable(
            "CAPTCHA_MODE=manual nhưng không có terminal để nhập. "
            "Hãy cấu hình CAPTCHA_MODE=external cho chạy nền."
        ) from exc
    if not code:
        raise CaptchaUnavailable("Người dùng không nhập captcha.")
    return code


def _external(image_path: Path, command: str) -> str:
    log.info("Gọi CAPTCHA_SOLVER_CMD cho %s", image_path.name)
    proc = subprocess.run(
        f'{command} "{image_path}"',
        shell=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise CaptchaUnavailable(
            f"CAPTCHA_SOLVER_CMD lỗi (exit {proc.returncode}): {proc.stderr.strip()[:300]}"
        )
    lines = proc.stdout.strip().splitlines()
    if not lines or not lines[0].strip():
        raise CaptchaUnavailable("CAPTCHA_SOLVER_CMD không in ra mã captcha.")
    return lines[0].strip()
