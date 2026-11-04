# Crawl Tool

App Windows để chạy MediaCrawler mà không cần gõ lệnh. Giao diện viết bằng HTML (`ui.html`) nhưng hiển thị trong cửa sổ riêng của app qua pywebview/WebView2: không mở trình duyệt, không mở cổng mạng.

- **Crawl**: form bên trái (nền tảng, từ khóa/link, tùy chọn), kết quả hiện ngay bên phải và cập nhật dần trong lúc crawl; nhật ký là ngăn thu gọn phía dưới. Với kiểu Từ khóa có thể chọn **nhiều nền tảng cùng lúc** (mỗi nền tảng một cửa sổ Chrome riêng, mở cách nhau 8 giây). Nút **✨ Gợi ý bằng Gemini** tạo từ khóa tiếng Trung từ mô tả chủ đề.
- **Kết quả**: lưới ảnh bìa kèm like/view/bình luận/chia sẻ; lọc lần crawl gần nhất hoặc tất cả, theo nền tảng/từ khóa, tìm, sắp xếp; mở bài, copy link, **☆ Lưu** vào thư viện. Ảnh bìa tải về máy ngay sau khi crawl (link ảnh của Douyin hết hạn sau vài ngày).
- **Thư viện**: chỉ những video đã lưu (`library.json` trong thư mục kết quả), vẫn giữ khi file kết quả bị xóa.
- **Cài đặt**: cách đăng nhập (trong cửa sổ Chrome, hoặc cookie riêng từng nền tảng), Gemini API key + model, thư mục kết quả, cập nhật base code, đổi token, gỡ cài đặt. Token, cookie và API key đều lưu mã hóa bằng DPAPI.

Kết quả lưu dạng jsonl (không xuất Excel).

## Cách tách code

| Phần | Ở đâu | Vai trò |
|---|---|---|
| App (repo này) | `MrNMDien/crawl-tool-app` | Giao diện, tự cài đặt, tự cập nhật base, gọi crawler |
| Base code | `MrNMDien/MediaCrawler` (riêng tư) | Toàn bộ logic crawl |

App **không chứa** base code. Mỗi lần mở, app hỏi GitHub commit mới nhất của nhánh `main` trong repo base; nếu khác bản đang có thì tải zip về, chạy `uv sync`, rồi thay bản cũ. Đẩy code mới lên `main` của repo base là mọi máy cài app tự nhận ở lần mở sau, không cần build lại app.

## Cài trên máy người dùng

Máy cần có:
- **Google Chrome** bản mới nhất. App tự mở một cửa sổ Chrome riêng (hồ sơ riêng), không đụng vào Chrome cá nhân.
- **Node.js** (https://nodejs.org), bắt buộc với Douyin và Zhihu.
- **WebView2 Runtime**: có sẵn trên Windows 11 và Windows 10 bản cập nhật; thiếu thì tải ở https://developer.microsoft.com/microsoft-edge/webview2/
- **GitHub token** chỉ đọc repo base: GitHub → Settings → Developer settings → Fine-grained tokens → chọn repo `MediaCrawler`, quyền *Contents: Read-only*.

Các bước:
1. Chạy `CrawlTool.exe` và chọn **Có** để cài. App được chép vào `%LOCALAPPDATA%\Programs\CrawlTool`, kèm shortcut ở Desktop và Start Menu.
2. Lần mở đầu tiên, app hỏi token rồi tải base code. Lần này mất vài phút vì phải tải Python và thư viện.
3. Nhập từ khóa và bấm **Bắt đầu crawl**. Xem kết quả ở tab **Thư viện video**. File gốc lưu ở `Documents\CrawlTool`; đổi chỗ lưu trong tab *Cài đặt*.

App lưu dữ liệu của nó ở `%LOCALAPPDATA%\CrawlTool`: base code, venv và `settings.json`. Token trong `settings.json` được mã hóa bằng DPAPI của Windows, nên chỉ chính tài khoản Windows đó giải mã được.

Muốn gỡ app: tab *Cài đặt → Gỡ cài đặt*. App sẽ xóa chính nó, base code và token, còn thư mục kết quả được giữ lại.

## Cập nhật base code từ tác giả gốc

```bash
cd MediaCrawler
git fetch upstream
git merge upstream/main
git push origin main
```

## Build lại app

Chỉ cần build lại khi sửa `crawl_tool.py` hoặc `ui.html`.

```bash
powershell -ExecutionPolicy Bypass -File build.ps1
```

Kết quả là `dist/CrawlTool.exe`. Test logic không cần giao diện bằng lệnh `python test_crawl_tool.py`.
