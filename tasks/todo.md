# Checklist MVP chatbot Jira

Trạng thái: **M1, M2 và M3 hoàn thành, tiếp theo là M4**.

## M1 — Jira MCP — Hoàn thành

- [x] Kết nối Python client với Atlassian Rovo MCP.
- [x] Xác minh site, cloud ID, project `KAN` và issue type `Task`.
- [x] Tạo và đọc lại [KAN-4](https://nghiaasi4n-1.atlassian.net/browse/KAN-4).

## M2 — Chatbot — Hoàn thành

- [x] Có schema và prompt cho `create`, `clarify`, `unsupported`.
- [x] Hỗ trợ nội dung tiếng Việt và tiếng Anh.
- [x] Có `POST /api/chat`, `GET /health` và web chat.
- [x] Chỉ kết nối Jira khi action là `create`.
- [x] Chạy chatbot với LLM thật cho một task tiếng Việt và một task tiếng Anh.
- [x] Xác nhận task mơ hồ chỉ hỏi lại, không tạo Jira ticket.
- [x] Tạo và đọc lại [KAN-5](https://nghiaasi4n-1.atlassian.net/browse/KAN-5) qua chatbot; title và description khớp.

## M3 — Kiểm tra tối thiểu và README — Hoàn thành

- [x] Giữ unit test cho bốn luồng chính: Việt, Anh, hỏi lại, ngoài phạm vi.
- [x] Giữ API test cho health và chat.
- [x] Chạy toàn bộ test một lần: 90 test pass.
- [x] Viết README: cấu hình `.env`, chạy local, test và giới hạn MVP.
- [x] Kiểm tra `.gitignore` không cho secret và artifact vào Git.

## M4 — Docker và GitHub dev

- [x] Tạo Dockerfile và `compose.yaml`.
- [x] Chạy `docker compose up --build`; container healthy.
- [ ] Kiểm tra từ bản clone mới.
- [ ] Commit và push lên GitHub nhánh `dev`.
- [ ] Ghi lại repository URL và commit SHA.

## Nghiệm thu

- [x] Tiếng Việt và tiếng Anh đều dùng được.
- [x] Task mơ hồ không tạo ticket.
- [x] Chatbot trả đúng Jira key/link.
- [x] Test tối thiểu pass.
- [x] Docker chạy được và Git không chứa secret.
