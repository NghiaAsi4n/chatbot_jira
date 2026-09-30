# Chatbot Jira

Ứng dụng web nhỏ nhận mô tả task bằng tiếng Việt hoặc tiếng Anh, dùng LLM tạo title và description, rồi tạo Jira Cloud ticket qua Atlassian Rovo MCP.

## Yêu cầu

- Python 3.12 trở lên.
- Jira Cloud project và tài khoản có quyền tạo issue.
- Atlassian API token.
- Ollama Cloud API key hoặc một API tương thích OpenAI.

Project dùng Atlassian Rovo MCP có sẵn và không tự triển khai MCP server.

## Chạy local

### 1. Clone nhánh dev

```powershell
git clone --branch dev https://github.com/NghiaAsi4n/chatbot_jira.git
cd chatbot_jira
```

### 2. Tạo môi trường Python

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Trên macOS hoặc Linux, dùng `source .venv/bin/activate`.

### 3. Tạo file cấu hình

PowerShell:

```powershell
Copy-Item .env.example .env
```

macOS hoặc Linux:

```bash
cp .env.example .env
```

Điền các biến bắt buộc trong `.env`:

```dotenv
ATLASSIAN_AUTH_MODE=basic
ATLASSIAN_EMAIL=your-email@example.com
ATLASSIAN_API_TOKEN=your-atlassian-api-token
ATLASSIAN_CLOUD_ID=your-cloud-id
ATLASSIAN_SITE_URL=https://your-site.atlassian.net
JIRA_PROJECT_KEY=KAN
JIRA_ISSUE_TYPE=Task

LLM_BASE_URL=https://ollama.com/v1
LLM_MODEL=gemma4:31b
LLM_API_KEY=your-ollama-api-key
```

Có thể kiểm tra cấu hình Jira mà không gọi mạng:

```powershell
python -m chatbot_jira.smoke check-config
```

Nếu chưa có `ATLASSIAN_CLOUD_ID`, chạy lệnh đọc thông tin Jira:

```powershell
python -m chatbot_jira.smoke inspect
```

## 4. Khởi động ứng dụng

```powershell
python -m uvicorn chatbot_jira.api:app --host 0.0.0.0 --port 8000 --env-file .env
```

Mở <http://localhost:8000> và nhập task, ví dụ:

- `Tạo task: khi OTP hết hạn, hiển thị nút Gửi lại sau 60 giây.`
- `Create a task: refresh the profile after a successful update.`

Health check: <http://localhost:8000/health>.

## API

Gửi `POST /api/chat`:

```json
{
  "message": "Tạo task: hiển thị thông báo sau khi lưu thành công",
  "conversation_id": "demo-conversation"
}
```

Các trạng thái chính:

- `created`: ticket đã được tạo và đọc lại thành công.
- `clarification_required`: cần thêm thông tin; Jira chưa được gọi.
- `unsupported`: yêu cầu nằm ngoài phạm vi tạo ticket.
- `failed`: request thất bại trước khi xác nhận tạo ticket.
- `unknown`: kết quả ghi chưa rõ; kiểm tra Jira trước khi thử lại.

## Chạy test

```powershell
python -m pytest
```

Unit test dùng fake LLM và fake Jira gateway nên không tạo ticket thật.

Smoke test tạo Jira thật là lệnh riêng và chỉ nên chạy khi chủ động muốn tạo một ticket:

```powershell
python -m chatbot_jira.smoke create --summary "Smoke test" --description-file description.md
```

Lệnh create không tự retry. Nếu kết quả là `unknown`, kiểm tra Jira trước khi chạy lại.

## Chạy bằng Docker

Tạo `.env` từ `.env.example` và điền credential, sau đó chạy:

```powershell
docker compose up --build
```

Mở <http://localhost:8000>. Kiểm tra trạng thái container:

```powershell
docker compose ps
```

Dừng ứng dụng:

```powershell
docker compose down
```

Database chống gửi trùng được lưu trong Docker volume `chatbot-jira-data`.
Không dùng `docker compose down -v` nếu muốn giữ dữ liệu này.

## Kiến trúc

```text
Browser
  → FastAPI
    → LLM tạo summary và description
    → Python MCP client
      → Atlassian Rovo MCP
        → Jira Cloud
```

- Project key và issue type lấy từ biến môi trường.
- Chỉ kết nối Jira khi LLM trả action `create`.
- Ticket được đọc lại sau khi tạo.
- Hội thoại giữ trong memory và mất khi ứng dụng restart.
- Request đã xử lý được lưu trong SQLite local để tránh tạo trùng khi gửi lại cùng `request_id`.

## Giới hạn MVP

- Chỉ tạo một ticket mỗi request.
- Không sửa, xóa, assign, comment hoặc đính kèm file.
- Chỉ cấu hình một Jira site, project và issue type.
- Không có đăng nhập ứng dụng hoặc lưu lịch sử chat lâu dài.
- Không tự retry khi kết quả tạo ticket chưa rõ.

## Bảo mật

- Không commit `.env`, API token, API key, database local hoặc thư mục `artifacts`.
- Credential chỉ được đọc ở backend.
- Khi thay credential, restart ứng dụng để nạp cấu hình mới.
