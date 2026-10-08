"""Tra cứu vi phạm trên MVDIS bằng Playwright (tab 個人).

Chỉ đọc kết quả, KHÔNG bao giờ bấm thanh toán.
Chạy thử độc lập:  python -m mvdis_crew.scraper
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

from .captcha import get_captcha_code
from .config import Settings, load_settings

log = logging.getLogger(__name__)

URL = "https://www.mvdis.gov.tw/m3-emv-vil/vil/penaltyQueryPay"
NOPAY_URL = URL + "?method=nopayPagination"
SKIP_DETAIL_KEYS = ("QR",)
QR_PATH_KEY = "_qr_path"  # khoá nội bộ: đường dẫn file PNG của mã QR thanh toán (không phải dữ liệu MVDIS)

# Dấu hiệu nhận biết trên trang kết quả
NO_DATA_MARKERS = ("查無", "無違規", "無應繳", "沒有")
CAPTCHA_ERROR_MARKERS = ("驗證碼",)
RESULT_HEADER_HINTS = ("違規", "罰鍰", "金額", "單號", "車號", "到案", "期限", "處所", "事實")


@dataclass
class QueryResult:
    status: str  # "ok" | "no_violation" | "unknown" | "error"
    violations: list[dict] = field(default_factory=list)
    message: str = ""
    queried_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    attachments: list[str] = field(default_factory=list)  # đường dẫn screenshot/html

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def violation_key(row: dict) -> str:
    """Khoá ổn định để so sánh với lần chạy trước: ưu tiên số biên bản (單號)."""
    for k, v in row.items():
        if "單號" in k and str(v).strip():
            return str(v).strip()
    raw = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return "h:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def query_violations(settings: Settings) -> QueryResult:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = settings.output_dir
    try:
        with sync_playwright() as pw:
            # MVDIS chặn trình duyệt headless (302 Security Redirect) -> nên dùng Chrome có cửa sổ.
            browser = pw.chromium.launch(headless=settings.headless, channel=settings.browser_channel or None)
            context = browser.new_context(locale="zh-TW", viewport={"width": 1280, "height": 1000})
            page = context.new_page()
            page.set_default_timeout(30_000)
            dialogs: list[str] = []

            def _on_dialog(dialog):
                dialogs.append(dialog.message)
                dialog.accept()

            page.on("dialog", _on_dialog)
            page.goto(URL, wait_until="domcontentloaded")

            for attempt in range(1, settings.max_captcha_retries + 1):
                log.info("Lượt tra cứu %d/%d (UID %s)", attempt, settings.max_captcha_retries, settings.masked_uid)
                _fill_form(page, settings)

                img_path = out / f"captcha_{ts}_{attempt}.png"
                page.locator("#pickimg1").screenshot(path=str(img_path))
                code = get_captcha_code(img_path, settings)

                page.fill("#form1 #validateStr", code)
                dialogs.clear()
                page.click("#search1")
                try:
                    page.wait_for_load_state("networkidle", timeout=45_000)
                except PWTimeout:
                    log.warning("Trang tải chậm, vẫn tiếp tục đọc kết quả")

                message = " | ".join([*dialogs, _visible_message(page)]).strip(" |")
                if message and any(m in message for m in CAPTCHA_ERROR_MARKERS):
                    log.warning("Captcha sai: %s", message)
                    _reload_captcha(page)
                    continue

                attachments = _snapshot(page, out, ts)
                result = _parse_result(page, message, payable="可線上繳納", out=out, ts=ts)

                # Tab 不可繳 (không nộp online được) là trang riêng trong cùng phiên.
                if result.status in {"ok", "no_violation"}:
                    page.goto(NOPAY_URL, wait_until="domcontentloaded")
                    try:
                        page.wait_for_load_state("networkidle", timeout=30_000)
                    except PWTimeout:
                        pass
                    attachments += _snapshot(page, out, ts + "_nopay")
                    nopay = _parse_result(page, "", payable="不可線上繳納", out=out, ts=ts + "_nopay")
                    if nopay.violations:
                        result.violations += nopay.violations
                        result.status = "ok"

                result.attachments = attachments
                browser.close()
                return result

            browser.close()
            return QueryResult(
                status="error",
                message=f"Captcha sai {settings.max_captcha_retries} lần liên tiếp.",
            )
    except Exception as exc:  # noqa: BLE001 - mọi lỗi đều được báo qua email
        log.exception("Tra cứu thất bại")
        return QueryResult(status="error", message=f"{type(exc).__name__}: {exc}")


def _fill_form(page: Page, settings: Settings) -> None:
    if page.locator("#tab1_cont").is_hidden():
        page.click("span.tab1")
    page.fill("#id1", settings.uid)
    page.fill("#birthday", settings.birthday)
    page.keyboard.press("Escape")  # đóng date-picker nếu bật
    page.evaluate("() => { const d = document.getElementById('ui-datepicker-div'); if (d) d.style.display = 'none'; }")


def _reload_captcha(page: Page) -> None:
    if page.locator("#pickimg1").count() == 0:
        page.goto(URL, wait_until="domcontentloaded")
        return
    page.evaluate("() => $('#pickimg1').attr('src','/m3-emv-vil/captchaImg.jpg?'+new Date())")
    page.wait_for_timeout(1500)


def _visible_message(page: Page) -> str:
    texts: list[str] = []
    for sel in ("#showmsg", "#validateStr1"):
        loc = page.locator(sel)
        if loc.count() and loc.first.is_visible():
            t = loc.first.inner_text().strip()
            if t:
                texts.append(t)
    return " | ".join(texts)


def _snapshot(page: Page, out: Path, ts: str) -> list[str]:
    png = out / f"result_{ts}.png"
    html = out / f"result_{ts}.html"
    page.screenshot(path=str(png), full_page=True)
    html.write_text(page.content(), encoding="utf-8")
    return [str(png), str(html)]


def _save_qr(data_uri: str, out: Path | None, ts: str, index: int) -> str:
    """Lưu ảnh QR 台灣Pay (data URI base64 nhúng trong bảng chi tiết) ra file PNG, trả về đường dẫn."""
    if not data_uri or out is None or "base64," not in data_uri:
        return ""
    try:
        raw = base64.b64decode("".join(data_uri.split("base64,", 1)[1].split()))
    except ValueError:
        log.warning("Không giải mã được ảnh QR của vi phạm #%d", index)
        return ""
    path = out / f"qr_{ts}_{index}.png"
    path.write_bytes(raw)
    return str(path)


def _parse_result(page: Page, message: str, payable: str = "", out: Path | None = None, ts: str = "") -> QueryResult:
    # 1) Mỗi vi phạm có bảng 罰鍰明細資訊 dạng dọc: mỗi dòng một <th> nhãn + một <td> giá trị.
    #    Ảnh QR thanh toán (nếu có) nằm trong cùng bảng dưới dạng <img src="data:image/...;base64,...">.
    details = page.evaluate(
        """() => Array.from(document.querySelectorAll('table'))
            .filter(t => !t.querySelector('table'))
            .map(t => ({
                rows: Array.from(t.rows).map(r => {
                    const th = r.querySelector(':scope > th'), td = r.querySelector(':scope > td');
                    return (r.cells.length === 2 && th && td)
                        ? [th.textContent.replace(/\\s+/g, ' ').trim(), td.textContent.replace(/\\s+/g, ' ').trim()]
                        : null;
                }),
                qr: (t.querySelector('img[src^="data:image"]') || document.createElement('img')).getAttribute('src') || ''
            }))
            .filter(d => d.rows.length > 1 && d.rows.every(x => x))"""
    )
    violations = []
    for item in details:
        row = {k: v for k, v in item["rows"] if k and not any(s in k for s in SKIP_DETAIL_KEYS)}
        if any("單號" in k for k in row) or "違規日" in row:
            if payable:
                row["繳納方式"] = payable
            qr_path = _save_qr(item.get("qr", ""), out, ts, len(violations) + 1)
            if qr_path:
                row[QR_PATH_KEY] = qr_path
            violations.append(row)
    if violations:
        return QueryResult(status="ok", violations=violations, message=message)

    # 2) Dự phòng: bảng ngang có dòng tiêu đề.
    tables = page.evaluate(
        """() => Array.from(document.querySelectorAll('table'))
            .filter(t => !t.querySelector('table') && !t.classList.contains('form_table'))
            .map(t => Array.from(t.rows).map(r =>
                Array.from(r.cells).map(c => c.textContent.replace(/\\s+/g, ' ').trim())))"""
    )
    best: list[dict] = []
    for rows in tables:
        if len(rows) < 2:
            continue
        header = rows[0]
        hits = sum(any(h in cell for h in RESULT_HEADER_HINTS) for cell in header)
        if hits < 2:
            continue
        parsed = []
        for r in rows[1:]:
            if len(r) != len(header) or not any(r):
                continue
            parsed.append({(header[i] or f"col{i}"): r[i] for i in range(len(header))})
        if len(parsed) > len(best):
            best = parsed

    if best:
        return QueryResult(status="ok", violations=best, message=message)

    body = page.inner_text("body")
    if any(m in body for m in NO_DATA_MARKERS) or any(m in message for m in NO_DATA_MARKERS):
        return QueryResult(status="no_violation", message=message or "查無資料")

    return QueryResult(
        status="unknown",
        message=(message or "Không nhận diện được bảng kết quả") + " — kiểm tra ảnh/HTML đính kèm.",
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")
    print(query_violations(load_settings(require_mail=False)).to_json())
