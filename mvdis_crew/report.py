"""Lịch sử vi phạm + báo cáo dựng sẵn (dùng khi LLM lỗi hoặc chạy --no-llm)."""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from .scraper import QueryResult, violation_key

STATUS_LABEL = {
    "ok": "CÓ vi phạm",
    "no_violation": "Không có vi phạm",
    "unknown": "Cần kiểm tra thủ công",
    "error": "LỖI tra cứu",
}


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


def default_subject(summary: dict) -> str:
    day = datetime.now().strftime("%Y-%m-%d")
    if summary["status"] == "ok":
        return f"[MVDIS] {day} – {summary['total_count']} vi phạm ({summary['new_count']} mới)"
    return f"[MVDIS] {day} – {STATUS_LABEL.get(summary['status'], summary['status'])}"


def default_html(summary: dict, masked_uid: str) -> str:
    parts = [
        f"<h2>Kết quả tra cứu vi phạm MVDIS – {html.escape(masked_uid)}</h2>",
        f"<p><b>Trạng thái:</b> {STATUS_LABEL.get(summary['status'], summary['status'])}<br>",
        f"<b>Thời điểm:</b> {html.escape(summary['queried_at'])}</p>",
    ]
    if summary.get("message"):
        parts.append(f"<p><b>Ghi chú:</b> {html.escape(summary['message'])}</p>")
    rows = summary.get("all_violations") or []
    if rows:
        parts.append(f"<p>Tổng: {summary['total_count']} vi phạm, mới: {summary['new_count']}.</p>")
        headers = list(rows[0].keys())
        parts.append('<table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse">')
        parts.append("<tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in headers) + "</tr>")
        for r in rows:
            parts.append("<tr>" + "".join(f"<td>{html.escape(str(r.get(h, '')))}</td>" for h in headers) + "</tr>")
        parts.append("</table>")
    parts.append('<p><a href="https://www.mvdis.gov.tw/m3-emv-vil/vil/penaltyQueryPay">Mở trang MVDIS</a></p>')
    return "\n".join(parts)
