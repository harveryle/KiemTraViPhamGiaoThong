"""Lịch sử vi phạm + dựng email báo cáo theo mẫu CỐ ĐỊNH (tiếng Việt, không phụ thuộc LLM)."""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .scraper import QR_PATH_KEY, QueryResult, violation_key

STATUS_LABEL = {
    "ok": "CÓ vi phạm",
    "no_violation": "Không có vi phạm",
    "unknown": "Cần kiểm tra thủ công",
    "error": "LỖI tra cứu",
}

MVDIS_URL = "https://www.mvdis.gov.tw/m3-emv-vil/vil/penaltyQueryPay"
MISSING = "—"

# (nhãn Việt, nhãn Trung, từ khoá tên trường MVDIS, loại cần dịch) — thứ tự hiển thị trong thẻ vi phạm.
DETAIL_ROWS_OPTIONAL = [
    ("Biển số xe", "車號", ("車號",), None),
    ("Số biên bản", "告發單號", ("告發單號", "單號"), None),
    ("Căn cứ pháp lý", "違反法規及法條", ("違反法規",), "law"),
    ("Nơi đến xử lý", "到案處所", ("到案處所", "處所"), "office"),
    ("Đối tượng chịu trách nhiệm", "歸責對象", ("歸責對象",), "party"),
]

_ROC_DATE = re.compile(r"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


# --------------------------------------------------------------------------- lịch sử
def diff_with_history(result: QueryResult, history_path: Path, save: bool = True) -> dict:
    """So sánh với lần chạy trước, trả về tóm tắt; chỉ ghi lịch sử khi tra cứu thành công."""
    previous: set[str] = set()
    if history_path.is_file():
        previous = set(json.loads(history_path.read_text(encoding="utf-8")).get("keys", []))

    keys = [violation_key(v) for v in result.violations]
    new = [v for v, k in zip(result.violations, keys) if k not in previous]

    if save and result.status in {"ok", "no_violation"}:
        history_path.write_text(
            json.dumps({"updated_at": result.queried_at, "keys": keys}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return {
        "status": result.status,
        "message": result.message,
        "queried_at": result.queried_at,
        "total_count": len(result.violations),
        "new_count": len(new),
        "new_violations": new,
        "all_violations": result.violations,
    }


# --------------------------------------------------------------------------- trích xuất trường
def _pick(v: dict, *names: str) -> str:
    """Lấy giá trị đầu tiên không rỗng có tên trường chứa một trong các từ khoá (bỏ qua khoá nội bộ '_...')."""
    for name in names:
        for k, val in v.items():
            if not k.startswith("_") and name in k and str(val).strip():
                return str(val).strip()
    return ""


def _roc_date(text: str) -> date | None:
    m = _ROC_DATE.search(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _amount(text: str) -> int | None:
    m = re.search(r"\d[\d,]*", text or "")
    return int(m.group(0).replace(",", "")) if m else None


def _fmt_money(n: int | None) -> str:
    return f"NT$ {n:,}" if n is not None else MISSING


def _fmt_date(text: str) -> str:
    """'115年9月8日' -> '08/09/2026'  (kèm bản gốc dân quốc ở dòng nhỏ)."""
    d = _roc_date(text)
    return d.strftime("%d/%m/%Y") if d else (text or MISSING)


def _normalize(v: dict) -> dict:
    fine_text = _pick(v, "罰鍰金額", "罰鍰", "金額")
    return {
        "date_raw": _pick(v, "違規日"),
        "reason": _pick(v, "事由"),
        "place": _pick(v, "違規地點", "地點"),
        "fine_raw": fine_text,
        "fine": _amount(fine_text),
        "due_raw": _pick(v, "應到案日"),
        "payable": _pick(v, "繳納方式"),
        "qr_path": v.get(QR_PATH_KEY, ""),
        "extra": [(vi, zh, _pick(v, *keys), kind) for vi, zh, keys, kind in DETAIL_ROWS_OPTIONAL],
    }


def field_texts(v: dict) -> list[tuple[str, str]]:
    """[(loại, văn bản tiếng Trung)] cần dịch của một vi phạm — dùng bởi translate.py."""
    n = _normalize(v)
    out = [("reason", n["reason"]), ("place", n["place"])]
    out += [(kind, val) for _, _, val, kind in n["extra"] if kind and val]
    return [(k, t) for k, t in out if t]


def _stats(summary: dict) -> dict:
    items = [_normalize(v) for v in summary.get("all_violations") or []]
    dues = [(d, i) for i, it in enumerate(items) if (d := _roc_date(it["due_raw"]))]
    nearest = min(dues)[0] if dues else None
    return {
        "items": items,
        "total_fine": sum(it["fine"] or 0 for it in items),
        "nearest": nearest,
        "days_left": (nearest - date.today()).days if nearest else None,
    }


# --------------------------------------------------------------------------- tiêu đề
def default_subject(summary: dict) -> str:
    day = datetime.now().strftime("%Y-%m-%d")
    if summary["status"] == "ok":
        total = _stats(summary)["total_fine"]
        money = f" – {_fmt_money(total)}" if total else ""
        return f"[MVDIS] {day} – {summary['total_count']} vi phạm ({summary['new_count']} mới){money}"
    return f"[MVDIS] {day} – {STATUS_LABEL.get(summary['status'], summary['status'])}"


# --------------------------------------------------------------------------- HTML
_FONT = "font-family:'Segoe UI',Roboto,Arial,'Microsoft JhengHei','PingFang TC','Noto Sans TC',sans-serif;"
_C = {
    "bg": "#f1f4f8", "card": "#ffffff", "ink": "#1c2733", "muted": "#6b7785", "line": "#e3e8ee",
    "navy": "#17375e", "red": "#c62828", "red_bg": "#fdecea", "amber": "#b26a00", "amber_bg": "#fff4e0",
    "green": "#1b7f3b", "green_bg": "#e7f6ec", "grey_bg": "#eef1f5", "vi": "#1f5fa8",
}


def _e(x: object) -> str:
    return html.escape(str(x))


def _bilabel(vi: str, zh: str) -> str:
    """Nhãn song ngữ: tiếng Việt trên, tiếng Trung nhỏ bên dưới."""
    return f'{_e(vi)}<div style="font-size:11.5px;color:#98a2ae;margin-top:1px;">{_e(zh)}</div>'


def _bi(zh: str, vi: str | None) -> str:
    """Giá trị song ngữ: bản gốc tiếng Trung, bản dịch tiếng Việt ngay bên dưới (nếu có)."""
    if not zh:
        return MISSING
    out = _e(zh)
    if vi and vi != zh:
        out += f'<div style="font-size:13px;font-weight:400;color:{_C["vi"]};margin-top:3px;">{_e(vi)}</div>'
    return out


def _row(label_vi: str, label_zh: str, value_html: str, emphasize: bool = False) -> str:
    weight = "700" if emphasize else "400"
    return (
        f'<tr><td style="padding:9px 14px;width:34%;color:{_C["muted"]};font-size:13px;vertical-align:top;'
        f'border-top:1px solid {_C["line"]};">{_bilabel(label_vi, label_zh)}</td>'
        f'<td style="padding:9px 14px;color:{_C["ink"]};font-size:14px;font-weight:{weight};vertical-align:top;'
        f'border-top:1px solid {_C["line"]};">{value_html}</td></tr>'
    )


def _stat_cell(label_vi: str, label_zh: str, value: str, color: str) -> str:
    return (
        f'<td align="center" style="padding:14px 6px;border-right:1px solid {_C["line"]};">'
        f'<div style="font-size:12px;color:{_C["muted"]};">{_e(label_vi)} · {_e(label_zh)}</div>'
        f'<div style="font-size:20px;font-weight:700;color:{color};margin-top:4px;">{_e(value)}</div></td>'
    )


def _banner(summary: dict, st: dict) -> str:
    status = summary["status"]
    if status == "ok":
        fg, bg = _C["red"], _C["red_bg"]
        text = f"Phát hiện {summary['total_count']} vi phạm giao thông cần xử lý"
        text_zh = f"發現 {summary['total_count']} 筆交通違規需處理"
        if summary["new_count"]:
            text += f" — {summary['new_count']} vi phạm MỚI"
            text_zh += f"（新增 {summary['new_count']} 筆）"
    elif status == "no_violation":
        fg, bg = _C["green"], _C["green_bg"]
        text, text_zh = "Không có vi phạm nào cần nộp phạt", "查無需繳納之交通違規"
    else:
        fg, bg = _C["amber"], _C["amber_bg"]
        text, text_zh = (
            ("Cần kiểm tra thủ công", "需人工確認") if status == "unknown" else ("Tra cứu thất bại", "查詢失敗")
        )
    note = (
        f'<div style="font-weight:400;font-size:13px;margin-top:6px;color:{_C["ink"]};">{_e(summary["message"])}</div>'
        if summary.get("message") and status != "ok" else ""
    )
    return (
        f'<tr><td style="padding:0 24px;"><div style="background:{bg};border-left:5px solid {fg};'
        f'border-radius:6px;padding:14px 16px;font-size:16px;font-weight:700;color:{fg};">{_e(text)}'
        f'<div style="font-size:13px;font-weight:400;margin-top:3px;">{_e(text_zh)}</div>{note}</div></td></tr>'
    )


def _summary_block(summary: dict, st: dict) -> str:
    if summary["status"] != "ok":
        return ""
    if st["nearest"]:
        left = st["days_left"]
        due_txt = st["nearest"].strftime("%d/%m/%Y")
        due_color = _C["red"] if left is not None and left <= 14 else _C["ink"]
        due_sub = "đã quá hạn" if left < 0 else f"còn {left} ngày"
        due_value = f"{due_txt} ({due_sub})"
    else:
        due_value, due_color = MISSING, _C["ink"]
    cells = (
        _stat_cell("Số vi phạm", "違規筆數", str(summary["total_count"]), _C["ink"])
        + _stat_cell("Vi phạm mới", "新增", str(summary["new_count"]), _C["red"] if summary["new_count"] else _C["ink"])
        + _stat_cell("Tổng tiền phạt", "罰鍰總額", _fmt_money(st["total_fine"]), _C["red"])
    )
    return (
        f'<tr><td style="padding:16px 24px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border:1px solid {_C["line"]};border-radius:8px;border-collapse:separate;overflow:hidden;">'
        f"<tr>{cells}</tr>"
        f'<tr><td colspan="3" style="padding:10px 14px;border-top:1px solid {_C["line"]};background:{_C["grey_bg"]};'
        f'font-size:13px;color:{_C["muted"]};">Hạn đến xử lý gần nhất · 最近應到案日: '
        f'<b style="color:{due_color};">{_e(due_value)}</b></td></tr></table></td></tr>'
    )


def _card(idx: int, it: dict, is_new: bool, cid: str | None, tr: dict[str, str]) -> str:
    badge = (
        f'<span style="background:{_C["red"]};color:#fff;font-size:11px;font-weight:700;border-radius:10px;'
        f'padding:2px 8px;margin-left:8px;vertical-align:middle;">MỚI · 新</span>' if is_new else ""
    )
    date_cell = (
        f'{_e(_fmt_date(it["date_raw"]))}'
        f'<div style="font-size:12px;color:{_C["muted"]};font-weight:400;">{_e(it["date_raw"] or MISSING)}</div>'
    )
    due_cell = (
        f'{_e(_fmt_date(it["due_raw"]))}'
        f'<div style="font-size:12px;color:{_C["muted"]};font-weight:400;">{_e(it["due_raw"] or MISSING)}</div>'
    )
    fine_cell = (
        f'<span style="color:{_C["red"]};font-size:18px;">{_e(_fmt_money(it["fine"]))}</span>'
        if it["fine"] is not None else _e(it["fine_raw"] or MISSING)
    )
    rows = [
        _row("Ngày vi phạm", "違規日", date_cell, True),
        _row("Nguyên nhân bị phạt", "事由", _bi(it["reason"], tr.get(it["reason"])), True),
        _row("Địa điểm vi phạm", "違規地點", _bi(it["place"], tr.get(it["place"])), True),
        _row("Số tiền phạt", "罰鍰金額", fine_cell, True),
        _row("Hạn đến xử lý", "應到案日", due_cell, True),
    ]
    rows += [_row(vi, zh, _bi(val, tr.get(val))) for vi, zh, val, _kind in it["extra"] if val]
    if it["payable"]:
        online = "不可" not in it["payable"]
        pay = (
            f'<span style="color:{_C["green"] if online else _C["amber"]};font-weight:600;">'
            f'{"Có thể nộp online" if online else "Không nộp online được — nộp trực tiếp tại trạm/sở giám lý"}</span>'
            f'<div style="font-size:13px;font-weight:400;color:{_C["muted"]};">{_e(it["payable"])}</div>'
        )
        rows.append(_row("Hình thức nộp", "繳納方式", pay))

    if cid:
        qr = (
            f'<div style="font-size:13px;color:{_C["muted"]};margin-bottom:8px;">'
            f"Mã QR thanh toán · 台灣Pay QR Code</div>"
            f'<img src="cid:{cid}" width="200" height="200" alt="Mã QR thanh toán vi phạm #{idx}" '
            f'style="display:block;margin:0 auto;border:1px solid {_C["line"]};border-radius:6px;background:#fff;">'
            f'<div style="font-size:12px;color:{_C["muted"]};margin-top:8px;">Quét bằng ứng dụng ngân hàng hỗ trợ 台灣Pay. '
            f"Hãy tự đối chiếu thông tin trên trang MVDIS trước khi thanh toán."
            f'<br>請以支援台灣Pay之行動銀行App掃描；付款前請自行至監理服務網核對資料。</div>'
        )
    else:
        qr = (
            f'<div style="font-size:13px;color:{_C["muted"]};">Không có mã QR thanh toán cho khoản này '
            f"(không nộp online được hoặc trang không cung cấp).<br>此筆無線上繳費QR碼。</div>"
        )

    return (
        f'<tr><td style="padding:16px 24px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border:1px solid {_C["line"]};border-radius:8px;border-collapse:separate;overflow:hidden;background:{_C["card"]};">'
        f'<tr><td colspan="2" style="background:{_C["navy"]};color:#fff;padding:11px 14px;font-size:15px;font-weight:700;">'
        f"Vi phạm #{idx}{badge}</td></tr>"
        f'{"".join(rows)}'
        f'<tr><td colspan="2" align="center" style="padding:16px 14px;border-top:1px solid {_C["line"]};">{qr}</td></tr>'
        f"</table></td></tr>"
    )


@dataclass
class EmailContent:
    subject: str
    html: str
    text: str
    inline_images: dict[str, str] = field(default_factory=dict)  # cid -> đường dẫn PNG


def build_email(summary: dict, masked_uid: str, translations: dict[str, str] | None = None) -> EmailContent:
    """translations: {tiếng Trung: tiếng Việt} từ translate.translate_summary; thiếu thì chỉ hiện tiếng Trung."""
    tr = translations or {}
    st = _stats(summary)
    new_keys = {violation_key(v) for v in summary.get("new_violations") or []}
    raw = summary.get("all_violations") or []

    inline: dict[str, str] = {}
    cards = []
    for i, (v, it) in enumerate(zip(raw, st["items"]), 1):
        cid = None
        if it["qr_path"] and Path(it["qr_path"]).is_file():
            cid = f"qr{i}"
            inline[cid] = it["qr_path"]
        cards.append(_card(i, it, violation_key(v) in new_keys, cid, tr))

    try:
        when = datetime.fromisoformat(summary["queried_at"]).strftime("%H:%M %d/%m/%Y")
    except (KeyError, ValueError):
        when = str(summary.get("queried_at", ""))

    warn = (
        f'<tr><td style="padding:18px 24px 0;"><div style="background:{_C["amber_bg"]};border-radius:6px;padding:12px 14px;'
        f'font-size:12.5px;line-height:1.55;color:{_C["ink"]};"><b>Lưu ý:</b> '
        "MVDIS không gửi email nhắc nợ phạt — email giả mạo kiểu này đều là lừa đảo. "
        "Mỗi khoản nộp online bị thu thêm một khoản phí thủ tục. "
        "Báo cáo này chỉ để tham khảo; việc thanh toán do bạn tự thực hiện và tự xác minh trên trang chính thức. "
        "Bản dịch tiếng Việt chỉ mang tính tham khảo — nội dung tiếng Trung là bản gốc chính thức."
        '<br><span style="color:#6b7785;">監理服務網不會以電子郵件通知未繳罰鍰，收到者皆為詐騙；線上繳費每筆另收手續費。'
        "本通知僅供參考，繳費請自行至官方網站確認。越文翻譯僅供參考，以中文原文為準。</span></div></td></tr>"
    )
    link = (
        f'<tr><td align="center" style="padding:18px 24px 6px;">'
        f'<a href="{MVDIS_URL}" style="background:{_C["navy"]};color:#fff;text-decoration:none;font-size:14px;'
        f'font-weight:600;padding:11px 22px;border-radius:6px;display:inline-block;">Mở trang tra cứu MVDIS · 開啟監理服務網</a></td></tr>'
    )
    footer = (
        f'<tr><td align="center" style="padding:14px 24px 22px;font-size:11.5px;color:{_C["muted"]};">'
        f"Email tự động — gửi lúc {_e(when)}. Vui lòng không trả lời email này.<br>自動通知信，請勿回覆。</td></tr>"
    )
    header = (
        f'<tr><td style="background:{_C["navy"]};padding:22px 24px;border-radius:10px 10px 0 0;">'
        f'<div style="font-size:12px;letter-spacing:1px;color:#9fb6d3;">MVDIS · 監理服務網</div>'
        f'<div style="font-size:21px;font-weight:700;color:#fff;margin-top:4px;">Báo cáo vi phạm giao thông</div>'
        f'<div style="font-size:15px;color:#c9d7ea;margin-top:2px;">交通違規查詢通知</div>'
        f'<div style="font-size:13px;color:#c9d7ea;margin-top:8px;">Tài khoản tra cứu · 查詢帳號: {_e(masked_uid)} · {_e(when)}</div></td></tr>'
    )
    spacer = '<tr><td style="height:16px;"></td></tr>'

    body = header + spacer + _banner(summary, st) + _summary_block(summary, st) + "".join(cards)
    body += warn + link + footer
    html_doc = (
        '<!doctype html><html lang="vi"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        f'<body style="margin:0;padding:0;background:{_C["bg"]};{_FONT}">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_C["bg"]};">'
        '<tr><td align="center" style="padding:20px 10px;">'
        f'<table role="presentation" width="640" cellpadding="0" cellspacing="0" '
        f'style="width:100%;max-width:640px;background:{_C["card"]};border-radius:10px;{_FONT}color:{_C["ink"]};">'
        f"{body}</table></td></tr></table></body></html>"
    )

    return EmailContent(
        subject=default_subject(summary),
        html=html_doc,
        text=_plain_text(summary, st, masked_uid, when, tr),
        inline_images=inline,
    )


def _plain_text(summary: dict, st: dict, masked_uid: str, when: str, tr: dict[str, str]) -> str:
    def bi(zh: str) -> str:
        vi = tr.get(zh)
        return f"{zh or MISSING}" + (f"\n                 → {vi}" if vi and vi != zh else "")

    lines = [
        "BÁO CÁO VI PHẠM GIAO THÔNG MVDIS",
        f"Tài khoản: {masked_uid} | Thời điểm: {when}",
        f"Trạng thái: {STATUS_LABEL.get(summary['status'], summary['status'])}",
    ]
    if summary.get("message") and summary["status"] != "ok":
        lines.append(f"Ghi chú: {summary['message']}")
    if summary["status"] == "ok":
        lines.append(
            f"Tổng: {summary['total_count']} vi phạm ({summary['new_count']} mới), tổng tiền phạt {_fmt_money(st['total_fine'])}"
        )
        for i, it in enumerate(st["items"], 1):
            lines += [
                "",
                f"--- Vi phạm #{i} ---",
                f"Ngày vi phạm : {_fmt_date(it['date_raw'])} ({it['date_raw']})",
                f"Nguyên nhân  : {bi(it['reason'])}",
                f"Địa điểm     : {bi(it['place'])}",
                f"Số tiền phạt : {_fmt_money(it['fine'])}",
                f"Hạn xử lý    : {_fmt_date(it['due_raw'])} ({it['due_raw']})",
            ]
            lines += [f"{vi}: {bi(val)}" for vi, _zh, val, _kind in it["extra"] if val]
    lines += ["", f"Trang tra cứu: {MVDIS_URL}", "Việc thanh toán do bạn tự thực hiện và tự xác minh."]
    return "\n".join(lines)
