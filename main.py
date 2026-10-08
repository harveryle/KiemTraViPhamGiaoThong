"""Entrypoint chạy hàng tuần.

    python main.py            # chạy CrewAI (OpenAI) đầy đủ
    python main.py --no-llm   # bỏ qua LLM: tra cứu + email dựng sẵn
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from datetime import datetime

from mvdis_crew.config import ConfigError, load_settings
from mvdis_crew.mailer import send_email
from mvdis_crew.report import diff_with_history, should_skip_email
from mvdis_crew.scraper import QueryResult, query_violations
from mvdis_crew.translate import build_bilingual_email
from mvdis_crew.usage import TRACKER

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

log = logging.getLogger("mvdis")


def setup_logging(output_dir) -> None:
    log_file = output_dir / f"run_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )


def run_without_llm(state) -> None:
    if state.result is None:
        state.result = query_violations(state.settings)
        state.attachments = list(state.result.attachments)
    if state.summary is None:
        state.summary = diff_with_history(state.result, state.settings.output_dir / "history.json")
    if should_skip_email(state.summary, state.settings.skip_email_if_no_violation):
        state.email_skipped = True
        log.info("Không có vi phạm và SKIP_EMAIL_IF_NO_VIOLATION đang bật -> không gửi email")
        return
    mail = build_bilingual_email(state.summary, state.settings)
    send_email(state.settings, mail.subject, mail.html, state.attachments, mail.inline_images, mail.text)
    state.email_sent = True


def record_crew_usage(crew) -> None:
    """Cộng token của các agent CrewAI vào bộ đếm chung; lỗi đọc số liệu không được làm hỏng lần chạy."""
    try:
        m = crew.calculate_usage_metrics()
        TRACKER.add("CrewAI (agents)", m.prompt_tokens, m.completion_tokens, m.total_tokens, m.successful_requests)
    except Exception:  # noqa: BLE001
        log.warning("Không đọc được số token của CrewAI", exc_info=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-llm", action="store_true", help="Không dùng CrewAI/OpenAI")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    setup_logging(settings.output_dir)

    from mvdis_crew.tools import RunState  # import muộn: crewai nặng

    state = RunState(settings=settings)
    try:
        if args.no_llm or not os.getenv("OPENAI_API_KEY"):
            if not args.no_llm:
                log.warning("Thiếu OPENAI_API_KEY -> chạy chế độ không LLM")
            run_without_llm(state)
        else:
            from mvdis_crew.crew import build_crew

            crew = build_crew(state)
            try:
                crew.kickoff()
            finally:
                record_crew_usage(crew)  # ghi cả khi crew lỗi giữa chừng
            if not state.email_sent and not state.email_skipped:
                log.warning("Crew không gửi email -> gửi báo cáo dựng sẵn")
                run_without_llm(state)
    except Exception:  # noqa: BLE001
        log.exception("Lỗi khi chạy crew")
        if not state.email_sent and not state.email_skipped:
            try:
                if state.result is None:
                    state.result = QueryResult(status="error", message=traceback.format_exc()[-1500:])
                run_without_llm(state)
            except Exception:  # noqa: BLE001
                log.exception("Không gửi được email báo lỗi")
                log.info(TRACKER.report())
                return 1

    status = state.result.status if state.result else "error"
    log.info("Hoàn tất. status=%s, email_sent=%s, email_skipped=%s", status, state.email_sent, state.email_skipped)
    log.info(TRACKER.report())
    return 0 if status in {"ok", "no_violation"} else 1


if __name__ == "__main__":
    sys.exit(main())
