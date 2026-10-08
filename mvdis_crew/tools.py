"""CrewAI tools. Dữ liệu cá nhân (UID/ngày sinh) chỉ nằm trong RunState, không truyền cho LLM."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .mailer import send_email
from .report import diff_with_history, should_skip_email
from .scraper import QueryResult, query_violations
from .translate import build_bilingual_email


@dataclass
class RunState:
    settings: Settings
    result: QueryResult | None = None
    summary: dict | None = None
    email_sent: bool = False
    email_skipped: bool = False  # cố ý không gửi (SKIP_EMAIL_IF_NO_VIOLATION + không có vi phạm)
    attachments: list[str] = field(default_factory=list)


class _NoArgs(BaseModel):
    pass


class _StateTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    state: RunState


class MVDISQueryTool(_StateTool):
    name: str = "mvdis_query"
    description: str = (
        "Tra cứu vi phạm giao thông trên MVDIS cho người dùng đã cấu hình sẵn. "
        "Không cần tham số. Trả về JSON gồm status, violations, message."
    )
    args_schema: Type[BaseModel] = _NoArgs

    def _run(self) -> str:
        # Mỗi lần tra cứu tốn một captcha -> chỉ chạy một lần mỗi phiên.
        if self.state.result is None:
            self.state.result = query_violations(self.state.settings)
            self.state.attachments = list(self.state.result.attachments)
        data = json.loads(self.state.result.to_json())
        data.pop("attachments", None)
        return json.dumps(data, ensure_ascii=False)


class HistoryTool(_StateTool):
    name: str = "violation_history"
    description: str = (
        "So sánh kết quả tra cứu vừa rồi với lần chạy trước. Không cần tham số. "
        "Trả về JSON: status, total_count, new_count, new_violations, all_violations, message."
    )
    args_schema: Type[BaseModel] = _NoArgs

    def _run(self) -> str:
        if self.state.result is None:
            return json.dumps({"error": "Chưa tra cứu. Hãy chạy mvdis_query trước."}, ensure_ascii=False)
        if self.state.summary is None:
            self.state.summary = diff_with_history(
                self.state.result, self.state.settings.output_dir / "history.json"
            )
        return json.dumps(self.state.summary, ensure_ascii=False)


class _EmailArgs(BaseModel):
    subject: str = Field("", description="Bỏ qua — tiêu đề do hệ thống tự đặt")
    html_body: str = Field("", description="Bỏ qua — nội dung do hệ thống dựng theo mẫu cố định")


class SendEmailTool(_StateTool):
    name: str = "send_report_email"
    description: str = (
        "Gửi email báo cáo tới danh sách người nhận đã cấu hình. Tiêu đề và nội dung được hệ thống "
        "dựng theo mẫu cố định (kèm ảnh QR, ảnh chụp kết quả) nên KHÔNG cần truyền nội dung. Chỉ gọi MỘT lần."
    )
    args_schema: Type[BaseModel] = _EmailArgs

    def _run(self, subject: str = "", html_body: str = "") -> str:
        if self.state.email_sent:
            return "Email đã được gửi trước đó, không gửi lại."
        # Tiêu đề + nội dung do code dựng (LLM hay bịa ngày tháng / mỗi lần một kiểu trình bày).
        if self.state.summary is None:
            result = self.state.result or QueryResult(status="error", message="Chưa tra cứu.")
            self.state.summary = diff_with_history(result, self.state.settings.output_dir / "history.json")
        if should_skip_email(self.state.summary, self.state.settings.skip_email_if_no_violation):
            self.state.email_skipped = True
            return "Không có vi phạm và SKIP_EMAIL_IF_NO_VIOLATION đang bật nên KHÔNG gửi email. Kết thúc."
        mail = build_bilingual_email(self.state.summary, self.state.settings)
        send_email(
            self.state.settings, mail.subject, mail.html, self.state.attachments, mail.inline_images, mail.text
        )
        self.state.email_sent = True
        return f"Đã gửi email tới {len(self.state.settings.mail_to)} người nhận."
