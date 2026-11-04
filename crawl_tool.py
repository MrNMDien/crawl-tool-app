"""Crawl Tool: app Windows điều khiển MediaCrawler.

Giao diện là HTML (ui.html) hiển thị trong cửa sổ riêng bằng pywebview/WebView2: không mở
trình duyệt, không mở cổng mạng. Base code (MediaCrawler) không đóng gói trong app: app tự tải
bản mới nhất từ repo GitHub riêng tư về %LOCALAPPDATA%\\CrawlTool\\base, cài thư viện bằng uv
rồi chạy main.py qua dòng lệnh. Kết quả crawl (jsonl) được gom thành thư viện video; ảnh bìa
tải về máy ngay sau khi crawl vì link ảnh của CDN (nhất là Douyin) hết hạn sau vài ngày.
"""
import base64
import ctypes
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path

APP_NAME = "CrawlTool"
BASE_REPO = "MrNMDien/MediaCrawler"
BASE_BRANCH = "main"

APP_HOME = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
BASE_DIR = APP_HOME / "base"
VENV_DIR = APP_HOME / "venv"
SETTINGS_FILE = APP_HOME / "settings.json"
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / APP_NAME
DEFAULT_DATA_DIR = Path.home() / "Documents" / APP_NAME

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: không bật cửa sổ console đen

PLATFORM_CODES = {"dy", "xhs", "ks", "bili", "wb", "tieba", "zhihu"}
TARGET_FLAG = {"search": "--keywords", "detail": "--specified_id", "creator": "--creator_id"}
LOGIN_TYPES = {"qrcode", "phone", "cookie"}
FORM_CHOICES = {"platform": PLATFORM_CODES, "crawl_type": set(TARGET_FLAG), "login": LOGIN_TYPES}
DEFAULT_FORM = {
    "platform": "xhs", "crawl_type": "search", "targets": "AI短剧\nAI漫剧", "login": "qrcode",
    "max_notes": 20, "comments": False, "max_comments": 10, "media": False, "headless": False,
}

# Thư mục con mà base ghi kết quả của từng nền tảng -> mã nền tảng trong app
DATA_DIRS = {"douyin": "dy", "xhs": "xhs", "kuaishou": "ks", "bili": "bili", "weibo": "wb"}
# Tên trường trong file jsonl của từng nền tảng (None = nền tảng không có trường đó)
_KEYS = ("id", "url", "cover", "likes", "views", "comments", "shares", "published", "title")
FIELDS = {
    "dy": ("aweme_id", "aweme_url", "cover_url", "liked_count", None, "comment_count", "share_count", "create_time", "title"),
    "xhs": ("note_id", "note_url", "image_list", "liked_count", None, "comment_count", "share_count", "time", "title"),
    "ks": ("video_id", "video_url", "video_cover_url", "liked_count", "viewd_count", None, None, "create_time", "title"),
    "bili": ("video_id", "video_url", "video_cover_url", "liked_count", "video_play_count", "video_comment", "video_share_count", "create_time", "title"),
    "wb": ("note_id", "note_url", None, "liked_count", None, "comments_count", "shared_count", "create_time", "content"),
}
# Ảnh bìa có chống hotlink: phải gửi Referer của đúng nền tảng
THUMB_REFERER = {
    "dy": "https://www.douyin.com/", "xhs": "https://www.xiaohongshu.com/",
    "ks": "https://www.kuaishou.com/", "bili": "https://www.bilibili.com/",
}
MAX_THUMB_BYTES = 5 * 1024 * 1024
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"


# ---------- Lưu token GitHub: mã hóa bằng DPAPI của Windows, chỉ user hiện tại giải được ----------

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def encrypt_token(token: str) -> str:
    return base64.b64encode(_dpapi(token.encode(), True)).decode()


def decrypt_token(blob: str) -> str:
    return _dpapi(base64.b64decode(blob), False).decode()


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    APP_HOME.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- Cập nhật base code từ GitHub ----------

def github_get(url: str, token: str) -> bytes:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": APP_NAME,
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def latest_sha(token: str) -> str:
    data = github_get(f"https://api.github.com/repos/{BASE_REPO}/commits/{BASE_BRANCH}", token)
    return json.loads(data)["sha"]


def extract_zipball(zip_bytes: bytes, dest: Path) -> None:
    """Giải nén zipball GitHub (có 1 thư mục gốc kiểu owner-repo-sha/) vào dest."""
    with tempfile.TemporaryDirectory(dir=APP_HOME) as tmp:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(tmp)
        roots = [p for p in Path(tmp).iterdir() if p.is_dir()]
        if len(roots) != 1:
            raise RuntimeError("File zip từ GitHub không đúng định dạng")
        shutil.move(str(roots[0]), str(dest))


def swap_base(new_dir: Path) -> None:
    """Thay base cũ bằng base mới, giữ lại dữ liệu đăng nhập trình duyệt (browser_data)."""
    if BASE_DIR.exists():
        old_profile = BASE_DIR / "browser_data"
        if old_profile.exists():
            shutil.move(str(old_profile), str(new_dir / "browser_data"))
        old_dir = APP_HOME / "base_old"
        shutil.rmtree(old_dir, ignore_errors=True)
        BASE_DIR.rename(old_dir)
        shutil.rmtree(old_dir, ignore_errors=True)
    new_dir.rename(BASE_DIR)


def uv_path() -> str:
    if getattr(sys, "frozen", False):
        return str(Path(sys._MEIPASS) / "uv.exe")
    found = shutil.which("uv")
    if not found:
        raise RuntimeError("Không tìm thấy uv")
    return found


def uv_env() -> dict:
    env = dict(os.environ)
    env.update({
        "UV_PROJECT_ENVIRONMENT": str(VENV_DIR),  # venv để ngoài base, cập nhật base không phải cài lại từ đầu
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    })
    return env


def build_args(values: dict, data_dir: str) -> list:
    """Chuyển giá trị trên form thành tham số dòng lệnh cho main.py của base."""
    for key, allowed in FORM_CHOICES.items():
        if values.get(key) not in allowed:
            raise ValueError("Lựa chọn không hợp lệ, hãy chọn lại nền tảng / kiểu crawl / cách đăng nhập")
    targets = [t.strip() for t in str(values.get("targets", "")).replace(",", "\n").splitlines() if t.strip()]
    if not targets:
        raise ValueError("Chưa nhập từ khóa hoặc ID/URL")
    try:
        max_notes, max_comments = int(values["max_notes"]), int(values["max_comments"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Số bài và số bình luận phải là số") from None
    args = [
        "--platform", values["platform"],
        "--type", values["crawl_type"],
        TARGET_FLAG[values["crawl_type"]], ",".join(targets),
        "--lt", values["login"],
        "--crawler_max_notes_count", str(max(1, max_notes)),
        "--get_comment", "true" if values.get("comments") else "false",
        "--max_comments_count_singlenotes", str(max(1, max_comments)),
        "--get_media", "true" if values.get("media") else "false",
        "--save_data_option", "jsonl",  # thư viện video đọc jsonl, không xuất Excel
        "--save_data_path", data_dir,
        "--headless", "true" if values.get("headless") else "false",
    ]
    if values["login"] == "cookie":
        cookies = str(values.get("cookies", "")).strip()
        if not cookies:
            raise ValueError("Đăng nhập bằng cookie nhưng chưa dán cookie")
        args += ["--cookies", cookies]
    return args


def redact_args(args: list) -> list:
    """Bản để in ra log: giấu giá trị cookie."""
    return ["***" if i and args[i - 1] == "--cookies" else a for i, a in enumerate(args)]


def find_chrome() -> bool:
    candidates = [
        Path(os.environ.get(var, "")) / "Google/Chrome/Application/chrome.exe"
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
    ]
    return any(p.is_file() for p in candidates)


# ---------- Thư viện video ----------

def parse_count(value) -> int:
    """'1.2万' -> 12000, '10万+' -> 100000, '3,456' -> 3456; không đọc được thì 0."""
    text = str(value or "").strip().replace(",", "").rstrip("+")
    multiplier = 1
    if text.endswith("万"):
        multiplier, text = 10_000, text[:-1]
    elif text.endswith("亿"):
        multiplier, text = 100_000_000, text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return 0


def parse_time(value) -> int:
    """Timestamp giây; nền tảng trả mili-giây thì đổi về giây; không đọc được thì 0."""
    try:
        ts = int(float(value))
    except (TypeError, ValueError):
        return 0
    return ts // 1000 if ts > 10**12 else ts


def safe_key(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", text)[:80]


def normalize(platform: str, row: dict):
    """Gom một dòng jsonl của từng nền tảng về cùng một dạng cho giao diện."""
    fields = dict(zip(_KEYS, FIELDS[platform]))

    def get(key):
        return row.get(fields[key]) if fields[key] else None

    item_id = safe_key(str(get("id") or ""))
    if not item_id:
        return None
    url = str(get("url") or "")
    cover = str(get("cover") or "").split(",")[0].strip()
    if cover.startswith("http://"):
        cover = "https://" + cover[len("http://"):]
    return {
        "key": f"{platform}_{item_id}",
        "platform": platform,
        "title": str(get("title") or row.get("desc") or "").strip()[:300],
        "url": url if url.startswith(("https://", "http://")) else "",
        "cover": cover if cover.startswith("https://") else "",
        "likes": parse_count(get("likes")),
        "views": parse_count(get("views")),
        "comments": parse_count(get("comments")),
        "shares": parse_count(get("shares")),
        "published": parse_time(get("published")),
        "crawled": parse_time(row.get("last_modify_ts")),
        "keyword": str(row.get("source_keyword") or ""),
        "author": str(row.get("nickname") or ""),
    }


def load_library(data_dir: str) -> list:
    """Đọc mọi file *_contents_*.jsonl, gộp trùng theo nền tảng + ID (giữ bản crawl mới nhất)."""
    items = {}
    for dirname, platform in DATA_DIRS.items():
        for path in sorted((Path(data_dir) / dirname / "jsonl").glob("*_contents_*.jsonl")):
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    item = normalize(platform, row) if isinstance(row, dict) else None
                    if item and item["crawled"] >= items.get(item["key"], {}).get("crawled", -1):
                        items[item["key"]] = item
    return list(items.values())


def image_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[4:12] in (b"ftypavif", b"ftypheic"):
        return "image/avif"
    return ""


def thumb_file(data_dir: str, key: str) -> Path:
    return Path(data_dir) / "thumbs" / f"{safe_key(key)}.img"


def download_thumb(item: dict, data_dir: str):
    """Tải ảnh bìa về máy (một lần). Trả về đường dẫn file, hoặc None nếu không tải được."""
    path = thumb_file(data_dir, item["key"])
    if path.exists():
        return path
    if not item.get("cover"):
        return None
    req = urllib.request.Request(item["cover"], headers={
        "Referer": THUMB_REFERER.get(item["platform"], ""), "User-Agent": BROWSER_UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read(MAX_THUMB_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if len(data) > MAX_THUMB_BYTES or not image_mime(data):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


def thumb_data_uri(path: Path) -> str:
    # ponytail: gửi nguyên ảnh gốc sang giao diện; nếu thư viện lên vài nghìn video thì thu nhỏ ảnh khi tải về
    data = path.read_bytes()
    return f"data:{image_mime(data)};base64,{base64.b64encode(data).decode()}"


# ---------- Tự cài đặt / gỡ cài đặt (khi chạy từ file .exe) ----------

def ask_yes_no(text: str) -> bool:
    MB_YESNO, MB_ICONQUESTION, IDYES = 0x04, 0x20, 6
    return ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, MB_YESNO | MB_ICONQUESTION) == IDYES


def _ps_quote(path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def create_shortcuts(target: Path) -> None:
    script = "; ".join(
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('{folder}')) '{APP_NAME}.lnk')); "
        f"$s.TargetPath={_ps_quote(target)}; $s.WorkingDirectory={_ps_quote(target.parent)}; $s.Save()"
        for folder in ("Desktop", "Programs")
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, creationflags=NO_WINDOW)


def self_install_if_needed() -> bool:
    """Trả về True nếu đã chuyển sang chạy bản vừa cài (bản hiện tại cần thoát)."""
    if not getattr(sys, "frozen", False):
        return False
    exe = Path(sys.executable)
    if exe.parent.resolve() == INSTALL_DIR.resolve():
        return False
    if not ask_yes_no(f"Cài {APP_NAME} vào máy?\n\nThư mục: {INSTALL_DIR}\nTạo shortcut ở Desktop và Start Menu."):
        return False
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    target = INSTALL_DIR / f"{APP_NAME}.exe"
    shutil.copy2(exe, target)
    create_shortcuts(target)
    subprocess.Popen([str(target)], cwd=str(INSTALL_DIR))
    return True


def uninstall() -> None:
    script = "; ".join(
        f"Remove-Item -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath('{folder}')) '{APP_NAME}.lnk')"
        for folder in ("Desktop", "Programs")
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], creationflags=NO_WINDOW)
    # Chờ app thoát rồi mới xóa được file .exe đang chạy
    subprocess.Popen(
        f'cmd /c timeout /t 3 /nobreak >nul & rmdir /s /q "{INSTALL_DIR}" & rmdir /s /q "{APP_HOME}"',
        creationflags=NO_WINDOW,
    )


# ---------- Cầu nối cho giao diện HTML ----------
# Mọi method public của Api được gọi từ JavaScript (window.pywebview.api.<tên>).
# Trạng thái nội bộ đặt tên bắt đầu bằng "_" để pywebview không đưa sang JavaScript.

class Api:
    def __init__(self):
        self._settings = load_settings()
        self._proc = None
        self._state = "idle"  # idle | updating | crawling
        self._lines = []
        self._dropped = 0
        self._lock = threading.Lock()
        self._library = {}
        self._lib_rev = 0
        self._window = None

    # --- tiện ích nội bộ ---
    def _data_dir(self) -> str:
        return self._settings.get("data_dir", str(DEFAULT_DATA_DIR))

    def _write(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)
            if len(self._lines) > 5000:
                del self._lines[:1000]
                self._dropped += 1000

    def _run_logged(self, cmd: list, cwd: Path) -> int:
        self._proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=uv_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
        )
        for raw in self._proc.stdout:
            self._write(raw.decode("utf-8", errors="replace").rstrip())
        code = self._proc.wait()
        self._proc = None
        return code

    def _token(self) -> str:
        blob = self._settings.get("token")
        if not blob:
            return ""
        try:
            return decrypt_token(blob)
        except OSError:
            self._write("[LỖI] Không giải mã được token đã lưu, hãy nhập lại trong tab Cài đặt.")
            return ""

    def _update_worker(self, token: str) -> None:
        try:
            self._write("Đang kiểm tra bản mới của base code...")
            sha = latest_sha(token)
            if sha == self._settings.get("base_sha") and BASE_DIR.exists():
                self._write(f"Base code đã là bản mới nhất ({sha[:7]}).")
                return
            self._write(f"Đang tải base code {sha[:7]}...")
            zip_bytes = github_get(f"https://api.github.com/repos/{BASE_REPO}/zipball/{sha}", token)
            new_dir = APP_HOME / "base_new"
            shutil.rmtree(new_dir, ignore_errors=True)
            extract_zipball(zip_bytes, new_dir)
            self._write("Đang cài thư viện (lần đầu sẽ lâu vì phải tải Python và thư viện)...")
            if self._run_logged([uv_path(), "sync"], new_dir) != 0:
                raise RuntimeError("uv sync thất bại, xem log ở trên")
            swap_base(new_dir)
            self._settings["base_sha"] = sha
            save_settings(self._settings)
            self._write(f"Đã cập nhật base code lên {sha[:7]}.")
        except urllib.error.HTTPError as e:
            hint = " (token sai, hết hạn hoặc không có quyền vào repo: đổi token trong tab Cài đặt)" if e.code in (401, 403, 404) else ""
            self._write(f"[LỖI] GitHub trả về {e.code}{hint}")
        except Exception as e:  # noqa: BLE001 - lỗi nào cũng phải hiện cho người dùng thấy
            self._write(f"[LỖI] Cập nhật thất bại: {e}")
        finally:
            self._state = "idle"

    def _crawl_worker(self, args: list) -> None:
        try:
            code = self._run_logged([uv_path(), "run", "--no-sync", "python", "main.py", *args], BASE_DIR)
            self._write("Hoàn tất." if code == 0 else f"[LỖI] Crawler dừng với mã {code}.")
            self._prefetch_thumbs()
        except Exception as e:  # noqa: BLE001
            self._write(f"[LỖI] Không chạy được crawler: {e}")
        finally:
            self._state = "idle"
            self._lib_rev += 1

    def _prefetch_thumbs(self) -> None:
        data_dir = self._data_dir()
        todo = [i for i in load_library(data_dir) if i["cover"] and not thumb_file(data_dir, i["key"]).exists()]
        if not todo:
            return
        self._write(f"Đang tải {len(todo)} ảnh bìa về máy...")
        with ThreadPoolExecutor(max_workers=8) as pool:
            ok = sum(1 for p in pool.map(lambda i: download_thumb(i, data_dir), todo) if p)
        self._write(f"Đã tải {ok}/{len(todo)} ảnh bìa.")

    def _on_closed(self) -> None:
        self.stop()

    # --- gọi từ giao diện ---
    def init(self) -> dict:
        saved = self._settings.get("form", {})
        form = dict(DEFAULT_FORM)
        for key, value in saved.items():
            # form cũ (bản Tkinter) lưu nhãn thay vì mã: bỏ qua giá trị không hợp lệ
            if key in form and (key not in FORM_CHOICES or value in FORM_CHOICES[key]):
                form[key] = value
        warnings = []
        if not find_chrome():
            warnings.append("Không thấy Google Chrome. Hãy cài Chrome bản mới nhất.")
        if not shutil.which("node"):
            warnings.append("Không thấy Node.js. Douyin và Zhihu cần Node.js để tính chữ ký: https://nodejs.org")
        return {
            "form": form, "data_dir": self._data_dir(), "has_token": bool(self._settings.get("token")),
            "warnings": warnings, "repo": BASE_REPO,
        }

    def poll(self, since=0) -> dict:
        with self._lock:
            start = max(int(since) - self._dropped, 0)
            lines = self._lines[start:]
            next_index = self._dropped + len(self._lines)
        return {
            "lines": lines, "next": next_index, "state": self._state,
            "base": self._settings.get("base_sha", "")[:7], "lib_rev": self._lib_rev,
        }

    def set_token(self, token) -> dict:
        token = str(token or "").strip()
        if not token:
            return {"error": "Chưa nhập token"}
        self._settings["token"] = encrypt_token(token)
        save_settings(self._settings)
        return self.check_update()

    def check_update(self) -> dict:
        if self._state != "idle":
            return {"error": "App đang bận, đợi xong rồi thử lại."}
        token = self._token()
        if not token:
            return {"error": "need_token"}
        self._state = "updating"
        threading.Thread(target=self._update_worker, args=(token,), daemon=True).start()
        return {"ok": True}

    def start(self, values) -> dict:
        if self._state != "idle":
            return {"error": "App đang bận, đợi xong rồi thử lại."}
        if not BASE_DIR.exists():
            return {"error": "Chưa có base code. Vào tab Cài đặt → Kiểm tra cập nhật."}
        try:
            args = build_args(values or {}, self._data_dir())
        except ValueError as e:
            return {"error": str(e)}
        self._settings["form"] = {k: v for k, v in values.items() if k in DEFAULT_FORM}
        save_settings(self._settings)
        Path(self._data_dir()).mkdir(parents=True, exist_ok=True)
        self._state = "crawling"
        self._write("─" * 60)
        self._write("Bắt đầu crawl: " + " ".join(redact_args(args)))
        threading.Thread(target=self._crawl_worker, args=(args,), daemon=True).start()
        return {"ok": True}

    def stop(self) -> dict:
        if self._proc:
            subprocess.run(["taskkill", "/PID", str(self._proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=NO_WINDOW)
            self._write("Đã dừng.")
        return {"ok": True}

    def library(self) -> list:
        data_dir = self._data_dir()
        items = load_library(data_dir)
        self._library = {i["key"]: i for i in items}
        return items

    def thumb(self, key) -> str:
        item = self._library.get(key)
        path = download_thumb(item, self._data_dir()) if item else None
        return thumb_data_uri(path) if path else ""

    def open_url(self, url) -> None:
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            os.startfile(url)

    def copy(self, text) -> None:
        # Truyền qua biến môi trường để nội dung không bị PowerShell hiểu thành lệnh
        env = dict(os.environ, CRAWLTOOL_CLIP=str(text))
        subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $env:CRAWLTOOL_CLIP"],
                       env=env, creationflags=NO_WINDOW)

    def open_data_dir(self) -> None:
        Path(self._data_dir()).mkdir(parents=True, exist_ok=True)
        os.startfile(self._data_dir())

    def choose_data_dir(self) -> str:
        import webview
        dialog = getattr(webview, "FileDialog", None)
        kind = dialog.FOLDER if dialog else webview.FOLDER_DIALOG
        chosen = self._window.create_file_dialog(kind, directory=self._data_dir())
        if not chosen:
            return ""
        self._settings["data_dir"] = chosen[0]
        save_settings(self._settings)
        self._lib_rev += 1
        return chosen[0]

    def uninstall_app(self) -> None:
        self.stop()
        uninstall()
        self._window.destroy()


def ui_html() -> str:
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).parent
    return (root / "ui.html").read_text(encoding="utf-8")


def main() -> None:
    if self_install_if_needed():
        return
    import webview

    api = Api()
    window = webview.create_window(
        APP_NAME, html=ui_html(), js_api=api, width=1320, height=860, min_size=(1000, 640),
    )
    api._window = window
    window.events.closed += api._on_closed
    webview.start()


if __name__ == "__main__":
    main()
