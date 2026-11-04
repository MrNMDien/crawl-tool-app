# Crawl Tool

App Windows (Tkinter) để chạy MediaCrawler mà không cần dùng web hay gõ lệnh.

## Cách tách code

| Phần | Ở đâu | Vai trò |
|---|---|---|
| App (repo này) | `MrNMDien/crawl-tool-app` | Giao diện, tự cài đặt, tự cập nhật base, gọi crawler |
| Base code | `MrNMDien/MediaCrawler` (riêng tư) | Toàn bộ logic crawl |

App **không chứa** base code. Mỗi lần mở, app hỏi GitHub commit mới nhất của nhánh `main` trong repo base; nếu khác bản đang có thì tải zip về, chạy `uv sync`, rồi thay bản cũ. Đẩy code mới lên `main` của repo base là mọi máy cài app tự nhận ở lần mở sau, không cần build lại app.

## Cài trên máy người dùng

Máy cần có:
- **Google Chrome** bản mới nhất. Vào `chrome://inspect/#remote-debugging` và tick *Allow remote debugging for this browser instance*.
- **Node.js** (https://nodejs.org), bắt buộc với Douyin và Zhihu.
- **GitHub token** chỉ đọc repo base: GitHub → Settings → Developer settings → Fine-grained tokens → chọn repo `MediaCrawler`, quyền *Contents: Read-only*.

Các bước:
1. Chạy `CrawlTool.exe` và chọn **Có** để cài. App được chép vào `%LOCALAPPDATA%\Programs\CrawlTool`, kèm shortcut ở Desktop và Start Menu.
2. Lần mở đầu tiên, app hỏi token rồi tải base code. Lần này mất vài phút vì phải tải Python và thư viện.
3. Nhập từ khóa và bấm **Bắt đầu**. Kết quả lưu ở `Documents\CrawlTool`; đổi chỗ lưu trong menu *Công cụ*.

App lưu dữ liệu của nó ở `%LOCALAPPDATA%\CrawlTool`: base code, venv và `settings.json`. Token trong `settings.json` được mã hóa bằng DPAPI của Windows, nên chỉ chính tài khoản Windows đó giải mã được.

Muốn gỡ app: menu *Công cụ → Gỡ cài đặt*. App sẽ xóa chính nó, base code và token, còn thư mục kết quả được giữ lại.

## Cập nhật base code từ tác giả gốc

```bash
cd MediaCrawler
git fetch upstream
git merge upstream/main
git push origin main
```

## Build lại app

Chỉ cần build lại khi sửa `crawl_tool.py`.

```bash
powershell -ExecutionPolicy Bypass -File build.ps1
```

Kết quả là `dist/CrawlTool.exe`. Test logic không cần giao diện bằng lệnh `python test_crawl_tool.py`.
