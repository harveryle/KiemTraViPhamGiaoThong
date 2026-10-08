#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mvdis_check.py
===============
Script Python DOC LAP (ban tu chay tren may cua ban) de tu dong hoa quy trinh:
tra cuu vi pham giao thong ca nhan tren he thong MVDIS (Dai Loan) va gui email
canh bao neu phat hien vi pham moi.

Trang muc tieu: https://www.mvdis.gov.tw/m3-emv-vil/vil/penaltyQueryPay

CACH DUNG
---------
1. Cai dat:
       pip install -r requirements.txt
       playwright install chromium
2. Sao chep .env.example -> .env va dien:
       MVDIS_UID, MVDIS_BIRTHDAY (bat buoc)
       GMAIL_ADDRESS, GMAIL_APP_PASSWORD (can khi muon gui email)
3. Chay thu (co nguoi ngoi truoc man hinh de tu go captcha):
       python mvdis_check.py
4. Lap lich chay tu dong hang tuan bang Windows Task Scheduler (xem README.md).

AN TOAN & RIENG TU (DOC KY TRUOC KHI DUNG)
-------------------------------------------
- MVDIS_UID va MVDIS_BIRTHDAY CHI duoc doc tu file .env cuc bo va dien truc tiep
  vao form dang nhap MVDIS. Script KHONG ghi hai gia tri nay ra log, KHONG dua
  vao noi dung email, KHONG luu vao bat ky noi nao khac.
- Script se dung O CHE DO "chi xem" doi voi buoc thanh toan: neu tim thay ma
  QR de thanh toan, script CHI chup anh de ban tu xac minh - KHONG bao gio
  nhap so the/tai khoan ngan hang, KHONG bao gio bam nut xac nhan/hoan tat
  giao dich. Viec thanh toan (neu can) la trach nhiem cua ban, thuc hien thu
  cong sau khi doc email canh bao.
- Script KHONG tu dong "giai" captcha bang OCR/AI theo mac dinh (viec bypass
  co che chong bot khong nam trong pham vi script nay cung cap san). Mac dinh
  (CAPTCHA_MODE=manual) can nguoi ngoi truoc man hinh go ma captcha khi duoc
  hoi. Neu ban muon chay hoan toan khong nguoi truc, tu cau hinh
  CAPTCHA_MODE=external va CAPTCHA_SOLVER_CMD tro toi co che rieng cua ban -
  ban tu chiu trach nhiem ve dieu khoan su dung cua MVDIS khi lam dieu nay.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from smtplib import SMTP_SSL
from typing import Optional

from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright

MVDIS_URL = "https://www.mvdis.gov.tw/m3-emv-vil/vil/penaltyQueryPay"
NO_DATA_TEXT = "查無可線上繳納罰單資料"


def log(msg: str) -> None:
    """Log an toan: KHONG bao gio truyen uid/birthday vao ham nay."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


@dataclass
class Config:
    uid: str
    birthday: str
    captcha_mode: str
    captcha_solver_cmd: str
    max_captcha_retries: int
    headless: bool
    gmail_address: str
    gmail_app_password: str
    mail_to: list[str]
    output_dir: Path


def load_config() -> Config:
    load_dotenv()

    def req(name: str) -> str:
        val = os.environ.get(name, "").strip()
        if not val:
            log(f"LOI: thieu bien moi truong bat buoc '{name}' trong file .env")
            sys.exit(1)
        return val

    uid = req("MVDIS_UID")
    birthday = req("MVDIS_BIRTHDAY")

    output_dir = Path(os.environ.get("OUTPUT_DIR", "./output")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    mail_to_raw = os.environ.get("MAIL_TO", "")
    mail_to = [x.strip() for x in mail_to_raw.split(",") if x.strip()]

    return Config(
        uid=uid,
        birthday=birthday,
        captcha_mode=os.environ.get("CAPTCHA_MODE", "manual").strip().lower(),
        captcha_solver_cmd=os.environ.get("CAPTCHA_SOLVER_CMD", "").strip(),
        max_captcha_retries=int(os.environ.get("MAX_CAPTCHA_RETRIES", "3")),
        headless=os.environ.get("HEADLESS", "false").strip().lower() == "true",
        gmail_address=os.environ.get("GMAIL_ADDRESS", "").strip(),
        gmail_app_password=os.environ.get("GMAIL_APP_PASSWORD", "").strip(),
        mail_to=mail_to,
        output_dir=output_dir,
    )


@dataclass
class QueryResult:
    has_violation: bool
    violations: list[list[str]] = field(default_factory=list)  # moi phan tu: cac o (cell) cua 1 dong
    table_headers: list[str] = field(default_factory=list)
    qr_screenshot: Optional[Path] = None
    raw_text_snapshot_path: Optional[Path] = None


_ddddocr_instance = None


def ocr_captcha(image_path: Path) -> str:
    """Tu giai captcha bang ddddocr (nap model mot lan roi tai su dung)."""
    global _ddddocr_instance
    if _ddddocr_instance is None:
        try:
            import ddddocr
        except ImportError:
            log("LOI: CAPTCHA_MODE=auto can thu vien ddddocr. Cai bang: pip install ddddocr")
            sys.exit(1)
        _ddddocr_instance = ddddocr.DdddOcr(show_ad=False)
    return _ddddocr_instance.classification(image_path.read_bytes()).strip()


def solve_captcha(page: Page, cfg: Config) -> str:
    """Chup anh captcha va lay ma (tu dong OCR neu CAPTCHA_MODE=auto)."""
    captcha_path = cfg.output_dir / f"captcha_{int(time.time())}.png"
    page.locator("#pickimg1").screenshot(path=str(captcha_path))
    log(f"Da chup anh captcha: {captcha_path}")

    if cfg.captcha_mode == "auto":
        code = ocr_captcha(captcha_path)
        if not code:
            log("CANH BAO: ddddocr khong nhan dien duoc ma captcha.")
        else:
            log("ddddocr da giai captcha tu dong.")
        return code

    if cfg.captcha_mode == "external":
        if not cfg.captcha_solver_cmd:
            log("LOI: CAPTCHA_MODE=external nhung chua cau hinh CAPTCHA_SOLVER_CMD")
            sys.exit(1)
        result = subprocess.run(
            [*cfg.captcha_solver_cmd.split(), str(captcha_path)],
            capture_output=True, text=True, timeout=30,
        )
        code = (result.stdout or "").strip().splitlines()[0].strip() if result.stdout else ""
        if not code:
            log(f"CANH BAO: CAPTCHA_SOLVER_CMD khong tra ve ma nao. stderr={result.stderr!r}")
        return code

    # manual mode: can nguoi ngoi truoc terminal
    print(f"\n>>> Vui long mo file anh captcha va go ma ban thay: {captcha_path}")
    code = input(">>> Nhap ma captcha: ").strip()
    return code


def close_datepicker_if_open(page: Page) -> None:
    """Dong lich chon ngay (jQuery UI datepicker) neu no dang hien."""
    page.keyboard.press("Escape")
    # jQuery UI datepicker container thuong co id #ui-datepicker-div
    try:
        page.locator("#ui-datepicker-div").wait_for(state="hidden", timeout=2000)
    except Exception:
        pass


def fill_and_submit_form(page: Page, cfg: Config) -> None:
    page.goto(MVDIS_URL, wait_until="domcontentloaded")

    # Dam bao dang o tab "個人" (ca nhan) - day la tab mac dinh, click lai cho chac
    page.locator("#person").click()

    page.locator("#id1").click()
    page.locator("#id1").fill(cfg.uid)

    page.locator("#birthday").click()
    page.locator("#birthday").fill(cfg.birthday)
    close_datepicker_if_open(page)

    # Chup man hinh xac nhan lich da dong va form san sang truoc khi go captcha
    confirm_path = cfg.output_dir / "form_before_captcha.png"
    page.screenshot(path=str(confirm_path))
    log(f"Da xac nhan form san sang (xem {confirm_path.name} neu can debug)")

    attempt = 0
    while True:
        attempt += 1
        code = solve_captcha(page, cfg)
        # Luu y: trang MVDIS dung trung id="validateStr" cho ca form ca nhan (#form1)
        # va form phap nhan (#form2), nen phai chi dinh ro pham vi #form1 de tranh
        # Playwright bao loi "strict mode violation: resolved to 2 elements".
        page.locator("#form1 #validateStr").click()
        page.locator("#form1 #validateStr").fill(code)

        page.locator("#search1").click()

        # Cho phan hoi: hoac popup loi (#showmsg) hoac noi dung ket qua thay doi
        error_text = wait_for_submit_result(page)

        if error_text is None:
            return  # thanh cong, khong co loi -> thoat vong lap

        log(f"Phan hoi loi tu he thong: {error_text!r} (lan thu {attempt})")
        if attempt >= cfg.max_captcha_retries:
            log("Da vuot qua so lan thu captcha toi da. Dung lai.")
            sys.exit(2)

        # Doi captcha moi va thu lai
        page.locator("a[onclick*='pickimg1']").click()
        page.wait_for_timeout(800)


def wait_for_submit_result(page: Page, timeout_ms: int = 15000) -> Optional[str]:
    """
    Sau khi bam '查詢', cho he thong phan hoi.
    Tra ve None neu thanh cong (co ket qua / thong bao khong co du lieu).
    Tra ve chuoi loi neu he thong bao loi (vd captcha sai) de vong lap ben ngoai retry.
    """
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        # Neu popup thong bao loi dang hien
        showmsg = page.locator("#showmsg")
        if showmsg.count() > 0 and showmsg.is_visible():
            text = showmsg.inner_text().strip()
            if text:
                return text

        body_text = page.locator("body").inner_text()
        if NO_DATA_TEXT in body_text:
            return None
        # Heuristic: neu xuat hien bang du lieu ket qua (co nhieu tu khoa lien quan vi pham)
        if any(k in body_text for k in ("違規日期", "違規地點", "應繳罰鍰", "罰鍰金額")):
            return None

        page.wait_for_timeout(500)

    log("CANH BAO: het thoi gian cho phan hoi tu MVDIS sau khi bam Tra cuu.")
    return None  # coi nhu khong loi captcha ro rang, de luong xu ly ket qua ben ngoai tu quyet dinh


def extract_violations(page: Page, cfg: Config) -> QueryResult:
    body_text = page.locator("body").inner_text()

    snapshot_path = cfg.output_dir / f"result_snapshot_{int(time.time())}.txt"
    snapshot_path.write_text(body_text, encoding="utf-8")

    if NO_DATA_TEXT in body_text:
        log("Ket qua: KHONG co vi pham giao thong nao can dong (noop).")
        return QueryResult(has_violation=False, raw_text_snapshot_path=snapshot_path)

    log("Ket qua: PHAT HIEN co du lieu vi pham. Dang trich xuat bang du lieu...")

    # Heuristic: chon bang (table) co nhieu dong nhat lam bang vi pham
    tables = page.locator("table").all()
    best_rows: list[list[str]] = []
    best_headers: list[str] = []
    for t in tables:
        rows = t.locator("tr").all()
        parsed: list[list[str]] = []
        headers: list[str] = []
        for i, r in enumerate(rows):
            cells = r.locator("th, td").all()
            texts = [c.inner_text().strip() for c in cells]
            texts = [x for x in texts if x != ""]
            if not texts:
                continue
            if i == 0 and r.locator("th").count() > 0:
                headers = texts
            else:
                parsed.append(texts)
        if len(parsed) > len(best_rows):
            best_rows = parsed
            best_headers = headers

    qr_path = try_capture_qr(page, cfg)

    return QueryResult(
        has_violation=True,
        violations=best_rows,
        table_headers=best_headers,
        qr_screenshot=qr_path,
        raw_text_snapshot_path=snapshot_path,
    )


def try_capture_qr(page: Page, cfg: Config) -> Optional[Path]:
    """
    Thu tim luong 'xem QR de thanh toan'. CHI XEM, khong nhap thong tin thanh
    toan, khong bam xac nhan/hoan tat giao dich nao.
    """
    try:
        candidates = page.get_by_text("線上繳費", exact=False)
        if candidates.count() == 0:
            log("Khong tim thay lien ket/nut 'thanh toan truc tuyen' - bo qua buoc QR.")
            return None
        candidates.first.click()
        page.wait_for_timeout(1500)

        # Tim anh co ve la QR code (thuong nho, vuong)
        imgs = page.locator("img").all()
        for img in imgs:
            src = (img.get_attribute("src") or "").lower()
            if "qr" in src or "qrcode" in src:
                qr_path = cfg.output_dir / f"qr_{int(time.time())}.png"
                img.screenshot(path=str(qr_path))
                log(f"Da chup ma QR thanh toan (chi de xem): {qr_path}")
                return qr_path

        log("Khong thay anh QR ro rang trong buoc nay - bo qua, van tiep tuc gui email.")
        return None
    except Exception as e:  # khong de loi o buoc phu (QR) lam hong toan bo tac vu
        log(f"Bo qua buoc chup QR do gap loi khong nghiem trong: {e}")
        return None


def format_violations_text(result: QueryResult) -> str:
    if not result.violations:
        return "(Khong trich xuat duoc chi tiet tung dong - xem file snapshot dinh kem duong dan trong log de kiem tra thu cong.)"
    lines = []
    headers = result.table_headers
    for i, row in enumerate(result.violations, 1):
        if headers and len(headers) == len(row):
            pairs = ", ".join(f"{h}: {v}" for h, v in zip(headers, row))
            lines.append(f"{i}. {pairs}")
        else:
            lines.append(f"{i}. {' | '.join(row)}")
    return "\n".join(lines)


def send_alert_email(cfg: Config, result: QueryResult, checked_at: str) -> None:
    if not cfg.gmail_address or not cfg.gmail_app_password:
        log("CANH BAO: chua cau hinh GMAIL_ADDRESS/GMAIL_APP_PASSWORD - khong the gui email.")
        return
    if not cfg.mail_to:
        log("CANH BAO: MAIL_TO rong - khong co nguoi nhan, bo qua gui email.")
        return

    subject = "Cảnh báo: Phát hiện vi phạm giao thông mới trên MVDIS"
    body = (
        f"Thoi diem kiem tra: {checked_at}\n"
        f"Nguon: {MVDIS_URL}\n\n"
        f"Danh sach vi pham phat hien duoc:\n\n"
        f"{format_violations_text(result)}\n\n"
    )
    if result.qr_screenshot:
        body += (
            "Da tim thay ma QR de thanh toan, dinh kem trong email nay.\n"
            "LUU Y: day chi la anh chup de tham khao - vui long TU XAC MINH lai "
            "thong tin tren trang MVDIS truoc khi quet/thanh toan. Khong ai da "
            "thuc hien thanh toan thay ban.\n"
        )
    else:
        body += "(Khong tim thay/chup duoc ma QR thanh toan trong lan chay nay.)\n"

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = cfg.gmail_address
    msg["To"] = ", ".join(cfg.mail_to)
    msg.attach(MIMEText(body, "plain", "utf-8"))

    if result.qr_screenshot and result.qr_screenshot.exists():
        with open(result.qr_screenshot, "rb") as f:
            part = MIMEApplication(f.read(), Name=result.qr_screenshot.name)
        part["Content-Disposition"] = f'attachment; filename="{result.qr_screenshot.name}"'
        msg.attach(part)

    log(f"Dang gui email canh bao toi: {', '.join(cfg.mail_to)}")
    with SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(cfg.gmail_address, cfg.gmail_app_password)
        smtp.sendmail(cfg.gmail_address, cfg.mail_to, msg.as_string())
    log("Da gui email thanh cong.")


def main() -> None:
    cfg = load_config()
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log("Bat dau tac vu kiem tra vi pham giao thong MVDIS...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=cfg.headless)
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        try:
            fill_and_submit_form(page, cfg)
            result = extract_violations(page, cfg)

            if result.has_violation:
                send_alert_email(cfg, result, checked_at)
            else:
                log("Khong can gui email (noop).")
        finally:
            page.close()
            browser.close()
            log("Da dong trinh duyet.")

    log("Hoan tat tac vu.")


if __name__ == "__main__":
    main()
