"""Gom số token LLM đã dùng trong một lần chạy (CrewAI + dịch Trung→Việt) để báo cáo cuối phiên."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TokenUsage:
    prompt: int = 0
    completion: int = 0
    total: int = 0
    requests: int = 0


@dataclass
class UsageTracker:
    sources: dict[str, TokenUsage] = field(default_factory=dict)

    def add(self, source: str, prompt: int = 0, completion: int = 0, total: int | None = None, requests: int = 1) -> None:
        u = self.sources.setdefault(source, TokenUsage())
        u.prompt += prompt or 0
        u.completion += completion or 0
        u.total += total if total is not None else (prompt or 0) + (completion or 0)
        u.requests += requests or 0

    def totals(self) -> TokenUsage:
        t = TokenUsage()
        for u in self.sources.values():
            t.prompt += u.prompt
            t.completion += u.completion
            t.total += u.total
            t.requests += u.requests
        return t

    def report(self) -> str:
        t = self.totals()
        if not self.sources or t.total == 0:
            return "Token đã dùng: 0 (lần chạy này không gọi LLM)"
        lines = [
            f"Token đã dùng: {t.total:,} (prompt {t.prompt:,} + completion {t.completion:,}) — {t.requests} lượt gọi"
        ]
        for name, u in self.sources.items():
            lines.append(f"  - {name}: {u.total:,} (prompt {u.prompt:,} + completion {u.completion:,}, {u.requests} lượt gọi)")
        return "\n".join(lines)


TRACKER = UsageTracker()
