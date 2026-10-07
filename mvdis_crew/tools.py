"""CrewAI tools. Dữ liệu cá nhân (UID/ngày sinh) chỉ nằm trong RunState, không truyền cho LLM."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .mailer import send_email
from .report import default_subject, diff_with_history
from .scraper import QueryResult, query_violations


@dataclass
class RunState:
    settings: Settings
    result: QueryResult | None = None
    summary: dict | None = None
    email_sent: bool = False
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
    subject: str = Field(..., description="Tiêu đề email")
    html_body: str = Field(..., description="Nội dung email dạng HTML")


class SendEmailTool(_StateTool):
    name: str = "send_report_email"
    description: str = (
        "Gửi email báo cáo tới danh sách người nhận đã cấu hình (ảnh chụp kết quả tự đính kèm). "
        "Chỉ gọi MỘT lần."
    )
    args_schema: Type[BaseModel] = _EmailArgs

    def _run(self, subject: str, html_body: str) -> str:
        if self.state.email_sent:
            return "Email đã được gửi trước đó, không gửi lại."
        # Tiêu đề do code tạo (LLM hay bịa ngày tháng).
        if self.state.summary is not None:
            subject = default_subject(self.state.summary)
        send_email(self.state.settings, subject, html_body, self.state.attachments)
        self.state.email_sent = True
        return f"Đã gửi email tới {len(self.state.settings.mail_to)} người nhận."
