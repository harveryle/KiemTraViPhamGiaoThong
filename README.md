# Kiểm tra vi phạm giao thông MVDIS hàng tuần (CrewAI)

Tự động tra cứu https://www.mvdis.gov.tw/m3-emv-vil/vil/penaltyQueryPay (tab 個人)
bằng thông tin trong `.env`, rồi gửi email báo cáo **mỗi tuần** (kể cả khi không có
vi phạm hoặc khi tra cứu lỗi). Chương trình chạy hoàn toàn trên máy của bạn.

## Kiến trúc

```
main.py ──► CrewAI (Process.sequential, LLM = OpenAI)
            1. MVDIS Query Operator  ── tool mvdis_query      (Playwright, code cố định)
            2. Violation Analyst     ── tool violation_history (so với output/history.json)
            3. Report Writer         ── tool send_report_email (Gmail SMTP)
        └─► Nếu LLM/crew lỗi hoặc không gửi email: gửi báo cáo dựng sẵn (không qua LLM).
```

| File | Vai trò |
|---|---|
| `main.py` | Entrypoint, log, fallback email |
| `mvdis_crew/config.py` | Đọc & kiểm tra `.env` |
| `mvdis_crew/scraper.py` | Điền form, lấy ảnh captcha, đọc bảng kết quả |
| `mvdis_crew/captcha.py` | Captcha: nhập tay hoặc gọi lệnh ngoài của bạn |
| `mvdis_crew/tools.py`, `crew.py` | Tools / Agents / Tasks CrewAI |
| `mvdis_crew/report.py` | Lịch sử & email dựng sẵn |
| `mvdis_crew/mailer.py` | Gửi email |
| `scripts/register_task.ps1` | Đăng ký lịch chạy hàng tuần |

UID và ngày sinh **không** được gửi cho LLM — tool tự đọc từ `.env`; log/email chỉ hiện UID đã che (`A12****789`).

## 1. Cài đặt

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
```

## 2. Cấu hình

```powershell
copy .env.example .env
```

Điền trong `.env`:
- `MVDIS_UID`, `MVDIS_BIRTHDAY` (7 số Dân Quốc, ví dụ `0780702`).
- `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` ([Mật khẩu ứng dụng Gmail](https://myaccount.google.com/apppasswords)), `MAIL_TO`.
- `OPENAI_API_KEY`, `OPENAI_MODEL` (để trống key → chạy không LLM).

**Không commit / chia sẻ `.env`** (đã có trong `.gitignore`).

## 3. Captcha

Chương trình **không** tự giải captcha. Có hai chế độ:
- `CAPTCHA_MODE=manual` (mặc định): lưu ảnh vào `output/` và chờ bạn gõ mã trong terminal.
- `CAPTCHA_MODE=external`: gọi `CAPTCHA_SOLVER_CMD "<đường_dẫn_ảnh.png>"` — lệnh do **bạn tự cung cấp**
  và tự chịu trách nhiệm về điều khoản sử dụng của MVDIS; mã captcha lấy từ dòng đầu stdout.

Captcha sai sẽ thử lại tối đa `MAX_CAPTCHA_RETRIES` lần, hết lượt thì email báo lỗi.

## 4. Chạy thử

```powershell
# Chỉ tra cứu, in JSON (nên để HEADLESS=false, CAPTCHA_MODE=manual)
.venv\Scripts\python.exe -m mvdis_crew.scraper

# Gửi email thử
.venv\Scripts\python.exe -m mvdis_crew.mailer --test

# Chạy đầy đủ với CrewAI / hoặc không dùng LLM
.venv\Scripts\python.exe main.py
.venv\Scripts\python.exe main.py --no-llm
```

Kết quả, ảnh chụp, HTML, log và `history.json` nằm trong `output/`.

## 5. Lập lịch hàng tuần

Chạy PowerShell (Administrator):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
Start-ScheduledTask -TaskName MVDIS-WeeklyCheck   # chạy thử ngay
```

Mặc định thứ Hai 08:00 giờ Đài Bắc (script tự quy đổi sang giờ máy).

- MVDIS **chặn trình duyệt headless** (`302 Security Redirect`), nên chương trình mở Chrome thật có cửa sổ
  (`HEADLESS=false`, `BROWSER_CHANNEL=chrome`). Task vì thế được đăng ký kiểu *Interactive*: chỉ chạy khi
  tài khoản đang có phiên đăng nhập (RDP có thể ngắt kết nối nhưng **không sign out**).
- Task chạy nền không có terminal, nên cần `CAPTCHA_MODE=external`; nếu không, email báo
  "không có terminal để nhập captcha" sẽ được gửi thay cho kết quả.

## 6. Lưu ý

- Bảng kết quả được nhận diện theo tiêu đề cột (違規, 罰鍰, 單號…). Vì chưa có dữ liệu vi phạm thật
  để đối chiếu, nếu email báo "Cần kiểm tra thủ công", hãy xem `output/result_*.html` và chỉnh
  `_parse_result()` trong `mvdis_crew/scraper.py`.
- Chương trình **không bao giờ** bấm thanh toán. Việc nộp phạt bạn tự làm trên trang MVDIS.
