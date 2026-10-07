"""Định nghĩa Agents / Tasks / Crew."""
from __future__ import annotations

from datetime import date

from crewai import LLM, Agent, Crew, Process, Task

from .tools import HistoryTool, MVDISQueryTool, RunState, SendEmailTool


def build_crew(state: RunState) -> Crew:
    llm = LLM(model=state.settings.openai_model, temperature=0)
    common = dict(llm=llm, allow_delegation=False, max_iter=4, verbose=True)

    scraper_agent = Agent(
        role="MVDIS Query Operator",
        goal="Lấy kết quả tra cứu vi phạm giao thông mới nhất từ MVDIS.",
        backstory="Bạn vận hành công cụ tra cứu MVDIS. Bạn không bịa dữ liệu, chỉ báo đúng kết quả công cụ trả về.",
        tools=[MVDISQueryTool(state=state)],
        **common,
    )
    analyst_agent = Agent(
        role="Violation Analyst",
        goal="Tóm tắt các vi phạm, tổng tiền phạt, hạn nộp gần nhất và chỉ ra vi phạm mới.",
        backstory="Bạn hiểu thuật ngữ giao thông Đài Loan (違規日期, 罰鍰金額, 應到案日期, 單號...).",
        tools=[HistoryTool(state=state)],
        **common,
    )
    reporter_agent = Agent(
        role="Report Writer",
        goal="Soạn và gửi email báo cáo tuần rõ ràng bằng tiếng Việt.",
        backstory="Bạn viết email ngắn gọn, giữ nguyên thuật ngữ tiếng Trung kèm giải nghĩa tiếng Việt.",
        tools=[SendEmailTool(state=state)],
        **common,
    )

    query_task = Task(
        description="Gọi công cụ mvdis_query đúng MỘT lần và trả lại nguyên văn JSON kết quả.",
        expected_output="JSON kết quả tra cứu (status, violations, message, queried_at).",
        agent=scraper_agent,
    )
    analyze_task = Task(
        description=(
            "Gọi công cụ violation_history đúng MỘT lần. Từ kết quả, viết bản tóm tắt JSON gồm: "
            "status, total_count, new_count, total_fine (cộng các cột số tiền nếu có, đơn vị TWD), "
            "nearest_deadline (nếu có), highlights (danh sách ngắn các vi phạm mới), message. "
            "Không bịa thêm dữ liệu không có trong kết quả."
        ),
        expected_output="JSON tóm tắt vi phạm.",
        agent=analyst_agent,
        context=[query_task],
    )
    report_task = Task(
        description=(
            f"Hôm nay là {date.today():%Y-%m-%d}. "
            "Soạn email HTML bằng tiếng Việt báo cáo kết quả tra cứu tuần này và gọi send_report_email "
            "đúng MỘT lần (tiêu đề sẽ được hệ thống tự đặt). "
            "Nội dung: trạng thái, bảng vi phạm (nếu có, đánh dấu MỚI), "
            "tổng tiền phạt, hạn nộp gần nhất, lời nhắc rằng việc nộp phạt phải tự làm trên trang MVDIS, "
            "và link https://www.mvdis.gov.tw/m3-emv-vil/vil/penaltyQueryPay. Nếu status là error/unknown "
            "thì nêu rõ lý do từ message."
        ),
        expected_output="Xác nhận đã gửi email.",
        agent=reporter_agent,
        context=[analyze_task],
    )

    return Crew(
        agents=[scraper_agent, analyst_agent, reporter_agent],
        tasks=[query_task, analyze_task, report_task],
        process=Process.sequential,
        verbose=True,
    )
