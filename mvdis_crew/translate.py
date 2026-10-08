"""Dịch Trung -> Việt cho các trường vi phạm, phục vụ email song ngữ.

Thứ tự: (1) quy tắc cố định (ổn định, miễn phí) -> (2) cache output/translations.json
-> (3) OpenAI (một lượt gọi gộp, temperature=0) cho phần còn lại (chủ yếu tên đường/địa điểm).
Chỉ gửi cho OpenAI đoạn văn bản vi phạm (事由 / 違規地點), KHÔNG gửi UID, ngày sinh hay biển số.
Lỗi dịch không bao giờ làm hỏng việc gửi mail: bản dịch thiếu thì email chỉ hiện tiếng Trung.
"""
from __future__ import annotations

import json
import logging
import os
import re

from .config import Settings

log = logging.getLogger(__name__)

_CJK = re.compile(r"[㐀-鿿＀-￯]")
_FULLWIDTH = {c: chr(c - 0xFEE0) for c in range(0xFF01, 0xFF5F)}  # ！..～ -> ASCII

LAW_NAMES = {
    "道路交通管理處罰條例": "Điều lệ xử phạt quản lý giao thông đường bộ",
    "道路交通安全規則": "Quy tắc an toàn giao thông đường bộ",
    "強制汽車責任保險法": "Luật bảo hiểm trách nhiệm bắt buộc xe cơ giới",
}

# Tên huyện/thành phố -> âm Hán-Việt (dùng cho 到案處所)
HV_PLACES = {
    "臺北": "Đài Bắc", "台北": "Đài Bắc", "新北": "Tân Bắc", "基隆": "Cơ Long", "桃園": "Đào Viên",
    "新竹": "Tân Trúc", "苗栗": "Miêu Lật", "臺中": "Đài Trung", "台中": "Đài Trung", "彰化": "Chương Hóa",
    "南投": "Nam Đầu", "雲林": "Vân Lâm", "嘉義": "Gia Nghĩa", "臺南": "Đài Nam", "台南": "Đài Nam",
    "高雄": "Cao Hùng", "屏東": "Bình Đông", "宜蘭": "Nghi Lan", "花蓮": "Hoa Liên", "臺東": "Đài Đông",
    "台東": "Đài Đông", "澎湖": "Bành Hồ", "金門": "Kim Môn", "連江": "Liên Giang", "馬祖": "Mã Tổ",
    "板橋": "Bản Kiều", "士林": "Sĩ Lâm", "蘆洲": "Lô Châu", "樹林": "Thụ Lâm", "中壢": "Trung Lịch",
    "豐原": "Phong Nguyên", "東港": "Đông Cảng", "玉里": "Ngọc Lý", "北區": "Bắc Khu",
}

PARTY_TOKENS = [  # thay thế theo thứ tự, cụm dài trước
    ("逕行歸責車主", "Truy cứu trực tiếp chủ xe"),
    ("違規車輛", "xe vi phạm"),
    ("歸責駕駛人", "Truy cứu người lái xe"),
    ("車主", "chủ xe"),
    ("駕駛人", "người lái xe"),
]


def _ascii(text: str) -> str:
    return text.translate(_FULLWIDTH).replace("　", " ")


def _rule_reason(zh: str) -> str | None:
    vehicle = {"汽車": "ô tô", "機車": "xe máy"}
    if m := re.search(r"(汽車|機車)駕駛人行車速度，?超過規定之最高時速超過(\d+)公里至(\d+)公里", zh):
        return f"Người lái {vehicle[m[1]]} chạy quá tốc độ tối đa quy định từ {m[2]} đến {m[3]} km/h"
    if m := re.search(r"(汽車|機車)駕駛人行車速度，?超過規定之最高時速(\d+)公里以內", zh):
        return f"Người lái {vehicle[m[1]]} chạy quá tốc độ tối đa quy định trong vòng {m[2]} km/h"
    if m := re.search(r"(汽車|機車)駕駛人行車速度，?超過規定之最高時速超過(\d+)公里", zh):
        return f"Người lái {vehicle[m[1]]} chạy quá tốc độ tối đa quy định trên {m[2]} km/h"
    if "在道路收費停車處所停車經催繳不依規定繳費" in zh:
        return "Đỗ xe tại bãi thu phí trên đường, đã được nhắc nộp nhưng không nộp phí theo quy định"
    if "闖紅燈" in zh:
        return "Vượt đèn đỏ tại giao lộ có đèn tín hiệu"
    return None


def _rule_law(zh: str) -> str | None:
    m = re.fullmatch(r"(.+?)第\s*(\d+)\s*條(?:之(\d+))?(?:第\s*(\d+)\s*項)?(?:第\s*(\d+)\s*款)?", zh.strip())
    if not m or m[1] not in LAW_NAMES:
        return None
    article = f"Điều {m[2]}" + (f"-{m[3]}" if m[3] else "")
    parts = [article]
    if m[4]:
        parts.append(f"khoản {m[4]}")
    if m[5]:
        parts.append(f"điểm {m[5]}")
    return f"{', '.join(parts)} — {LAW_NAMES[m[1]]}"


def _rule_office(zh: str) -> str | None:
    m = re.fullmatch(r"(.+?)(?:市|縣)?(監理站|監理所|監理分所)", zh.strip())
    if not m:
        return None
    kind = {"監理站": "Trạm giám lý", "監理所": "Sở giám lý", "監理分所": "Phân sở giám lý"}[m[2]]
    name = HV_PLACES.get(m[1])
    return f"{kind} {name}" if name else None


def _rule_party(zh: str) -> str | None:
    out = _ascii(zh.replace("：", ": ").replace("（", " (").replace("）", ")"))
    for src, dst in PARTY_TOKENS:
        out = out.replace(src, dst)
    return None if _CJK.search(out) else re.sub(r"\s+", " ", out).strip()


_RULES = {"reason": _rule_reason, "law": _rule_law, "office": _rule_office, "party": _rule_party}


def _collect(summary: dict) -> list[tuple[str, str]]:
    """[(kind, chinese_text)] không trùng, lấy từ mọi vi phạm."""
    from .report import field_texts  # tránh import vòng

    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for v in summary.get("all_violations") or []:
        for kind, zh in field_texts(v):
            if zh and zh not in seen:
                seen.add(zh)
                out.append((kind, zh))
    return out


def translate_summary(summary: dict, settings: Settings) -> dict[str, str]:
    """Trả về {tiếng Trung: tiếng Việt}. Thiếu bản dịch -> không có khoá đó."""
    items = _collect(summary)
    cache_path = settings.output_dir / "translations.json"
    cache: dict[str, str] = {}
    if cache_path.is_file():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("translations.json hỏng, bỏ qua cache")

    result: dict[str, str] = {}
    pending: list[tuple[str, str]] = []
    for kind, zh in items:
        vi = _RULES[kind](zh) if kind in _RULES else None
        if vi is None:
            vi = cache.get(zh)
        if vi:
            result[zh] = vi
        else:
            pending.append((kind, zh))

    if pending:
        fresh = _llm_translate(pending, settings)
        result.update(fresh)
        if fresh:
            cache.update(fresh)
            try:
                cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                log.warning("Không ghi được translations.json")
    return result


def build_bilingual_email(summary: dict, settings: Settings):
    """Dựng email song ngữ; mọi lỗi dịch chỉ làm mất phần tiếng Việt, không làm mất email."""
    from .report import build_email

    try:
        tr = translate_summary(summary, settings)
    except Exception:  # noqa: BLE001
        log.exception("Dịch thất bại -> email chỉ hiện tiếng Trung cho phần nội dung vi phạm")
        tr = {}
    return build_email(summary, settings.masked_uid, tr)


_SYSTEM = (
    "Bạn là biên dịch viên Trung–Việt chuyên về giao thông Đài Loan. Dịch từng mục sang tiếng Việt tự nhiên, "
    "ngắn gọn, sát nghĩa. Với tên địa danh (thành phố, thị trấn, đường, phố) dùng âm Hán-Việt viết hoa chữ cái "
    "đầu; số nhà/số km giữ nguyên chữ số ASCII (đổi số toàn chiều rộng sang ASCII); '號前' = 'trước số N', "
    "'路' = 'đường', '街' = 'phố', '鎮' = 'thị trấn', '市' = 'thành phố', '鄉' = 'xã'. Nếu tên địa điểm là chữ "
    "viết tắt/không rõ nghĩa thì giữ nguyên chữ Hán. Không thêm giải thích. "
    'Trả về DUY NHẤT một JSON object dạng {"<id>": "<bản dịch>"}.'
)


def _llm_translate(pending: list[tuple[str, str]], settings: Settings) -> dict[str, str]:
    if not os.getenv("OPENAI_API_KEY"):
        log.info("Không có OPENAI_API_KEY -> bỏ qua dịch %d mục (email chỉ hiện tiếng Trung)", len(pending))
        return {}
    try:
        from openai import OpenAI

        payload = [{"id": str(i), "loai": kind, "zh": zh} for i, (kind, zh) in enumerate(pending)]
        resp = OpenAI(timeout=40).chat.completions.create(
            model=settings.openai_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:  # noqa: BLE001 - dịch lỗi không được chặn việc gửi mail
        log.warning("Dịch bằng OpenAI thất bại (%s) -> email chỉ hiện tiếng Trung cho các mục này", exc)
        return {}

    out: dict[str, str] = {}
    for i, (_, zh) in enumerate(pending):
        vi = str(data.get(str(i), "")).strip()
        if vi and vi != zh:
            out[zh] = vi
    return out
