# Kế hoạch MVP: Chatbot tạo Jira ticket

Trạng thái: **M1, M2 và M3 đã hoàn thành. M4 đã xong Docker và đang chờ GitHub remote để push**. Checklist: [todo.md](todo.md).

## 1. Mục tiêu

Người dùng nhập một task bằng tiếng Việt hoặc tiếng Anh. Chatbot tạo title và description cùng ngôn ngữ, sau đó dùng Python MCP client gọi Atlassian Rovo MCP để tạo Jira Cloud ticket và trả về issue key cùng link.

Nếu yêu cầu chưa đủ rõ, chatbot hỏi thêm trước khi tạo ticket.

## 2. Phạm vi MVP

- Backend và web chat đơn giản bằng FastAPI.
- Nhận task tiếng Việt và tiếng Anh.
- Một Jira site, project và issue type cấu hình bằng biến môi trường.
- Dùng Atlassian Rovo MCP có sẵn; không tự viết MCP server.
- Không làm đăng nhập ứng dụng, nhiều người dùng, database lịch sử chat, sửa/xóa ticket hoặc CI/CD.
- Đóng gói Docker và push bản hoàn chỉnh lên GitHub nhánh `dev`.

## 3. Luồng chính

```text
Browser → FastAPI → LLM → Atlassian Rovo MCP → Jira Cloud
```

1. Người dùng gửi mô tả task.
2. LLM trả một trong ba action: `create`, `clarify`, `unsupported`.
3. Chỉ action `create` mới kết nối Jira.
4. Backend tạo ticket, đọc lại để xác minh rồi trả issue key/link.
5. Không tự retry khi chưa biết Jira đã tạo thành công hay chưa.

Quy tắc nội dung:

- Task tiếng Việt tạo ticket tiếng Việt; task tiếng Anh tạo ticket tiếng Anh.
- Làm theo ngôn ngữ đầu ra nếu người dùng chỉ định rõ.
- Không tự thêm deadline, assignee, nguyên nhân lỗi hoặc yêu cầu chưa được cung cấp.
- Hội thoại chỉ giữ trong memory và mất khi restart.

## 4. Các mốc thực hiện

### M1 — Kết nối Jira MCP — Hoàn thành

- Python client kết nối được Atlassian Rovo MCP bằng Basic auth.
- Đã xác minh site, cloud ID, project `KAN` và issue type `Task`.
- Đã tạo và đọc lại ticket [KAN-4](https://nghiaasi4n-1.atlassian.net/browse/KAN-4).

### M2 — Chatbot — Hoàn thành

- Tạo schema và prompt cho tiếng Việt/Anh.
- Xử lý `create`, `clarify`, `unsupported`.
- Tạo `POST /api/chat`, `GET /health` và trang web chat.
- Chỉ mở kết nối MCP khi cần tạo ticket.

Hoàn thành khi hai ngôn ngữ tạo đúng nội dung, yêu cầu mơ hồ được hỏi lại và một ticket thật được tạo qua chatbot.

### M3 — Kiểm tra và tài liệu — Hoàn thành

Chỉ giữ kiểm tra cần thiết:

- Unit test cho bốn luồng: tiếng Việt, tiếng Anh, hỏi lại và ngoài phạm vi.
- API test cho health và chat.
- Một smoke test Jira thật; không tạo nhiều ticket thử.
- README hướng dẫn cấu hình, chạy local và chạy test.

### M4 — Docker và GitHub

- Tạo Dockerfile và `compose.yaml`.
- Secret chỉ nằm trong `.env`, không đưa vào image hoặc Git.
- Kiểm tra clone mới có thể chạy bằng Docker Compose.
- Commit và push lên GitHub nhánh `dev`, không force push.

## 5. Cấu hình

```text
ATLASSIAN_AUTH_MODE=basic
ATLASSIAN_EMAIL=
ATLASSIAN_API_TOKEN=
ATLASSIAN_CLOUD_ID=
ATLASSIAN_SITE_URL=
JIRA_PROJECT_KEY=KAN
JIRA_ISSUE_TYPE=Task
LLM_BASE_URL=
LLM_MODEL=
LLM_API_KEY=
```

Không commit `.env`.

## 6. Hoàn thành project khi

- Chatbot nhận được tiếng Việt và tiếng Anh.
- Yêu cầu thiếu thông tin không tạo ticket.
- Ticket thật có đúng title, description, issue key và link.
- Test tối thiểu pass.
- Docker Compose chạy được từ bản clone mới.
- Source có trên GitHub nhánh `dev` và không chứa secret.
