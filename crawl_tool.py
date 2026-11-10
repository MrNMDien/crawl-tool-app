"""Crawl Tool: app Windows điều khiển MediaCrawler.

Giao diện là HTML (ui.html) hiển thị trong cửa sổ riêng bằng pywebview/WebView2: không mở
trình duyệt, không mở cổng mạng. Base code (MediaCrawler) không đóng gói trong app: app tự tải
bản mới nhất từ repo GitHub riêng tư về %LOCALAPPDATA%\\CrawlTool\\base, cài thư viện bằng uv
rồi chạy main.py qua dòng lệnh. Kết quả crawl (jsonl) được gom thành thư viện video; ảnh bìa
tải về máy ngay sau khi crawl vì link ảnh của CDN (nhất là Douyin) hết hạn sau vài ngày.
"""
import base64
import ctypes
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unicodedata
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from datetime import date, datetime, timedelta
from pathlib import Path

APP_NAME = "CrawlTool"
APP_VERSION = "1.9.2"  # tăng mỗi lần phát hành (release.ps1 đọc số này)
APP_RELEASE_PREFIX = "app-v"  # tag dạng app-v1.2.3
APP_REPO = "MrNMDien/CrawlTool"  # repo public: Releases chứa CrawlTool.exe (đã gói sẵn base code), không cần token

APP_HOME = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
BASE_DIR = APP_HOME / "base"  # chỗ cài base của bản app cũ; bản mới cài vào base-<sha>
BROWSER_DATA_DIR = APP_HOME / "browser_data"  # hồ sơ Chrome + file đánh dấu đã đăng nhập
VENV_DIR = APP_HOME / "venv"
SETTINGS_FILE = APP_HOME / "settings.json"
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / APP_NAME
DEFAULT_DATA_DIR = Path.home() / "Documents" / APP_NAME

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: không bật cửa sổ console đen

PLATFORM_CODES = {"dy", "xhs", "ks", "bili", "wb", "tieba", "zhihu"}
PLATFORM_NAMES = {"dy": "Douyin", "xhs": "Xiaohongshu", "ks": "Kuaishou", "bili": "Bilibili",
                  "wb": "Weibo", "tieba": "Tieba", "zhihu": "Zhihu"}
# ponytail: mở Chrome của từng nền tảng cách nhau vài giây để không giành cùng một cổng điều khiển
# (base chọn cổng trống bằng cách thử bind); nếu vẫn đụng cổng thì cho base nhận cổng cố định theo nền tảng
PARALLEL_STAGGER_SEC = 8
TARGET_FLAG = {"search": "--keywords", "detail": "--specified_id", "creator": "--creator_id"}
LOGIN_TYPES = {"qrcode", "phone", "cookie"}
FORM_CHOICES = {"crawl_type": set(TARGET_FLAG), "login": LOGIN_TYPES}
DEFAULT_FORM = {
    "platforms": ["xhs"], "crawl_type": "search", "targets": "AI短剧\nAI漫剧",
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
UI_LOGIN_TYPES = ("qrcode", "cookie")  # "qrcode" = đăng nhập trong cửa sổ Chrome (QR, SMS, mật khẩu đều được)
GEMINI_DEFAULT_MODEL = "gemini-flash-latest"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TMDB_API = "https://api.themoviedb.org/3"
MOVIES_FILE = APP_HOME / "movies.json"  # cache lịch phim, lấy lại khi cũ hơn MOVIES_MAX_AGE
MOVIES_MAX_AGE = 6 * 3600
MOVIES_CACHE_VERSION = 2  # đổi khi dòng lịch phim có thêm trường: cache cũ bị bỏ, lấy lại
MOVIE_DAYS_BACK, MOVIE_DAYS_AHEAD = 7, 60
MOVIE_PAGES, KR_PAGES = 5, 2  # mỗi trang discover 20 phim, xếp theo độ phổ biến
# Nền tảng streaming (Mỹ + Hàn): tên hiển thị -> tên TMDB/JustWatch dùng (khớp đầu chuỗi, vì có cả
# "Netflix Standard with Ads", "Paramount+ Amazon Channel"...). vn: có chính thức ở Việt Nam.
# drop: giờ lên sóng thường lệ, "pt" = 00:00 giờ Thái Bình Dương, "gmt" = 00:00 GMT, None = không rõ giờ.
STREAMERS = {
    "Netflix": {"names": ("netflix",), "vn": True, "drop": "pt"},
    "HBO Max": {"names": ("hbo max", "max"), "vn": True, "drop": "pt"},
    "Prime Video": {"names": ("amazon prime video", "prime video"), "vn": True, "drop": "gmt"},
    "Apple TV": {"names": ("apple tv+", "apple tv plus", "apple tv"), "vn": True, "drop": "pt"},
    "Disney+": {"names": ("disney plus", "disney+"), "vn": False, "drop": "pt"},
    "Hulu": {"names": ("hulu",), "vn": False, "drop": "pt"},
    "Peacock": {"names": ("peacock",), "vn": False, "drop": "pt"},
    "Paramount+": {"names": ("paramount plus", "paramount+"), "vn": False, "drop": "pt"},
    "TVING": {"names": ("tving",), "vn": False, "drop": None},
    "Coupang Play": {"names": ("coupang play",), "vn": False, "drop": None},
    "Wavve": {"names": ("wavve",), "vn": False, "drop": None},
}
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


# ---------- Cập nhật app từ GitHub Releases (repo public) ----------

def github_get(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": APP_NAME,
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_version(text: str) -> tuple:
    """"app-v1.2.3" / "1.2.3" -> (1, 2, 3); không đúng dạng thì ()."""
    match = re.fullmatch(r"(?:app-)?v?(\d+)\.(\d+)\.(\d+)", str(text or "").strip())
    return tuple(int(x) for x in match.groups()) if match else ()


def latest_app_release() -> dict:
    """Bản app mới nhất trên GitHub Releases (tag app-v*, có file CrawlTool.exe). Không có thì {}.

    API GitHub chỉ cho 60 lượt/giờ mỗi IP (cả văn phòng dùng chung 1 IP là hết): hết lượt thì đọc
    releases.atom, trang này không tính lượt."""
    try:
        releases = json.loads(github_get(f"https://api.github.com/repos/{APP_REPO}/releases?per_page=30"))
    except urllib.error.HTTPError as e:
        if e.code not in (403, 429):
            raise
        return latest_app_release_from_feed(github_get(f"https://github.com/{APP_REPO}/releases.atom"))
    best = {}
    for rel in releases if isinstance(releases, list) else []:
        tag = str(rel.get("tag_name") or "")
        version = parse_version(tag)
        if rel.get("draft") or rel.get("prerelease") or not tag.startswith(APP_RELEASE_PREFIX) or not version:
            continue
        asset = next((a for a in rel.get("assets") or [] if a.get("name") == f"{APP_NAME}.exe"), None)
        if asset and version > best.get("version_tuple", ()):
            body = str(rel.get("body") or "")
            sha = re.search(r"sha256:\s*([0-9a-fA-F]{64})", body)
            notes = re.sub(r"sha256:\s*[0-9a-fA-F]{64}", "", body).strip()
            best = {"version_tuple": version, "version": ".".join(map(str, version)), "notes": notes[:500],
                    "asset_url": asset["browser_download_url"], "size": asset.get("size", 0), "sha256": sha.group(1).lower() if sha else ""}
    return best


def latest_app_release_from_feed(feed: bytes) -> dict:
    """Như latest_app_release nhưng đọc từ releases.atom (không có danh sách file, nên chỉ nhận bản
    có sha256 trong ghi chú: release.ps1 luôn đăng exe kèm sha256)."""
    ns = {"a": "http://www.w3.org/2005/Atom"}
    best = {}
    for entry in ET.fromstring(feed).findall("a:entry", ns):
        tag = (entry.findtext("a:id", "", ns) or "").rsplit("/", 1)[-1]
        version = parse_version(tag)
        if not tag.startswith(APP_RELEASE_PREFIX) or not version or version <= best.get("version_tuple", ()):
            continue
        body = html.unescape(re.sub(r"<[^>]+>", "", entry.findtext("a:content", "", ns) or ""))
        sha = re.search(r"sha256:\s*([0-9a-fA-F]{64})", body)
        if not sha:
            continue
        notes = re.sub(r"sha256:\s*[0-9a-fA-F]{64}", "", body).strip()
        best = {"version_tuple": version, "version": ".".join(map(str, version)), "notes": notes[:500],
                "asset_url": f"https://github.com/{APP_REPO}/releases/download/{tag}/{APP_NAME}.exe",
                "size": 0, "sha256": sha.group(1).lower()}
    return best


def github_download_asset(url: str) -> bytes:
    if not url.startswith("https://github.com/"):
        raise RuntimeError("Địa chỉ tải bản cập nhật không hợp lệ")
    return github_get(url, timeout=600)


def apply_app_update(data: bytes, exe: Path, sha256: str) -> None:
    """Thay file exe đang chạy bằng bản mới. Windows cho đổi tên (không cho xóa) file exe đang chạy,
    nên: exe -> .old.exe, bản mới -> exe. Lần mở sau app tự xóa .old.exe."""
    if len(data) < 1_000_000 or not data.startswith(b"MZ"):
        raise ValueError("File cập nhật không phải chương trình Windows hợp lệ")
    if not sha256 or hashlib.sha256(data).hexdigest() != sha256.lower():
        raise ValueError("File cập nhật không khớp checksum, đã hủy để an toàn")
    new, old = exe.with_name(f"{exe.stem}.new.exe"), exe.with_name(f"{exe.stem}.old.exe")
    new.write_bytes(data)
    old.unlink(missing_ok=True)
    exe.rename(old)
    try:
        new.rename(exe)
    except OSError:
        old.rename(exe)  # trả lại bản cũ nếu không thay được
        raise


def launch_fresh(exe: Path) -> None:
    """Mở một bản CrawlTool khác. Phải reset môi trường PyInstaller: nếu không, bản mới dùng chung thư mục
    tạm _MEI của bản đang chạy, bản này thoát là thư mục bị xóa (mất uv.exe -> [WinError 2])."""
    subprocess.Popen([str(exe)], cwd=str(exe.parent), env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"})


def remove_old_exe(exe: Path) -> None:
    try:
        exe.with_name(f"{exe.stem}.old.exe").unlink(missing_ok=True)
    except OSError:
        pass  # bản cũ còn đang thoát, lần sau xóa


def bundled_base():
    """Base code gói sẵn trong exe (build.ps1 tạo base.zip + base.sha). Trả về (đường dẫn zip, sha) hoặc None."""
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).parent
    zip_path, sha_path = root / "base.zip", root / "base.sha"
    if not (zip_path.is_file() and sha_path.is_file()):
        return None
    return zip_path, sha_path.read_text(encoding="utf-8").strip()


def extract_zipball(zip_bytes: bytes, dest: Path) -> None:
    """Giải nén zip có 1 thư mục gốc (vd. MediaCrawler/) vào dest."""
    with tempfile.TemporaryDirectory(dir=APP_HOME) as tmp:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(tmp)
        roots = [p for p in Path(tmp).iterdir() if p.is_dir()]
        if len(roots) != 1:
            raise RuntimeError("File zip base code không đúng định dạng")
        shutil.move(str(roots[0]), str(dest))


# Base code gốc (MediaCrawler) cài thư viện qua mirror Tsinghua: mirror này chặn IP ngoài Trung Quốc (403).
PYPI_MIRRORS = {
    b"https://pypi.tuna.tsinghua.edu.cn/simple": b"https://pypi.org/simple",
    b"https://pypi.tuna.tsinghua.edu.cn/packages/": b"https://files.pythonhosted.org/packages/",
}


def use_official_pypi(base: Path) -> None:
    """Đổi địa chỉ mirror PyPI Trung Quốc trong pyproject.toml và uv.lock sang PyPI chính thức.
    Cùng file, cùng hash, chỉ khác máy chủ, nên uv.lock vẫn khớp và không phải giải lại thư viện."""
    for name in ("pyproject.toml", "uv.lock"):
        path = base / name
        if not path.is_file():
            continue
        data = original = path.read_bytes()
        for mirror, official in PYPI_MIRRORS.items():
            data = data.replace(mirror, official)
        if data != original:
            path.write_bytes(data)


def base_dir_for(sha: str) -> Path:
    """Mỗi phiên bản base một thư mục riêng: cập nhật không phải đổi tên/xóa thư mục đang bị dùng
    (Chrome hay tiến trình cũ còn giữ thư mục làm thư mục hiện hành thì Windows không cho đổi tên)."""
    return APP_HOME / f"base-{sha[:12]}"


def current_base(settings: dict):
    path = settings.get("base_dir")
    if path and Path(path).is_dir():
        return Path(path)
    return BASE_DIR if BASE_DIR.is_dir() else None  # bản app cũ cài base vào thư mục "base" cố định


def browser_data_dir() -> Path:
    """Hồ sơ Chrome (cookie, phiên đăng nhập) của crawler, nằm ngoài thư mục base."""
    legacy = BASE_DIR / "browser_data"
    # chưa chuyển được hồ sơ cũ ra ngoài thì vẫn dùng chỗ cũ, để không phải đăng nhập lại
    return legacy if legacy.is_dir() and not BROWSER_DATA_DIR.exists() else BROWSER_DATA_DIR


def migrate_browser_data() -> list:
    """Gom hồ sơ Chrome từ các thư mục base cũ ra BROWSER_DATA_DIR. Trả về các dòng thông báo.

    Bản app trước để hồ sơ trong base/browser_data, và khi cập nhật lỗi giữa chừng có thể đã dời
    hồ sơ sang base_new/browser_data. Ưu tiên base (lần dùng gần nhất), rồi base_new, base_old.
    """
    messages = []
    for source in (BASE_DIR, APP_HOME / "base_new", APP_HOME / "base_old"):
        src = source / "browser_data"
        if not src.is_dir():
            continue
        BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            target = BROWSER_DATA_DIR / item.name
            if target.exists():
                shutil.rmtree(item, ignore_errors=True)  # đã giữ bản mới hơn, bản cũ trùng thì bỏ
                continue
            try:
                shutil.move(str(item), str(target))
                messages.append(f"Đã giữ lại hồ sơ đăng nhập {item.name} (từ {source.name}).")
            except OSError as e:
                messages.append(f"[CẢNH BÁO] Chưa chuyển được hồ sơ đăng nhập {item.name}: {e}. "
                                "Đóng hết cửa sổ Chrome của CrawlTool rồi mở lại app.")
        try:
            src.rmdir()  # chỉ xóa khi đã chuyển hết
        except OSError:
            pass
    return messages


def remove_old_bases(keep: Path) -> None:
    """Dọn các bản base cũ. Thư mục nào còn bị khóa hoặc còn hồ sơ đăng nhập thì để lần sau."""
    for path in APP_HOME.glob("base*"):
        if path == keep or not path.is_dir() or (path / "browser_data").exists():
            continue
        shutil.rmtree(path, ignore_errors=True)


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
        "MEDIACRAWLER_BROWSER_DATA_DIR": str(browser_data_dir()),  # phiên đăng nhập không nằm trong base
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    })
    return env


def build_jobs(values: dict, data_dir: str, cookies: dict = None) -> dict:
    """Mỗi nền tảng được chọn -> tham số dòng lệnh của một lần chạy main.py.

    cookies: cookie đã lưu của từng nền tảng (chỉ dùng khi đăng nhập bằng cookie).
    """
    platforms = values.get("platforms")
    if not isinstance(platforms, list) or not platforms or not set(platforms) <= PLATFORM_CODES:
        raise ValueError("Chọn ít nhất một nền tảng")
    platforms = list(dict.fromkeys(platforms))  # bỏ trùng, giữ thứ tự
    if len(platforms) > 1 and values.get("crawl_type") != "search":
        raise ValueError("Chọn nhiều nền tảng chỉ dùng được với kiểu Từ khóa (link/ID là của riêng từng nền tảng)")
    jobs = {}
    for p in platforms:
        job = {**values, "platform": p}
        if values.get("login") == "cookie":
            job["cookies"] = (cookies or {}).get(p, "")
            if not job["cookies"].strip():
                raise ValueError(f"Chưa có cookie cho {PLATFORM_NAMES[p]}: vào Cài đặt → Đăng nhập để dán cookie")
        jobs[p] = build_args(job, data_dir)
    return jobs


def build_args(values: dict, data_dir: str) -> list:
    """Chuyển giá trị trên form (một nền tảng) thành tham số dòng lệnh cho main.py của base."""
    if values.get("platform") not in PLATFORM_CODES:
        raise ValueError("Nền tảng không hợp lệ")
    for key, allowed in FORM_CHOICES.items():
        if values.get(key) not in allowed:
            raise ValueError("Lựa chọn không hợp lệ, hãy chọn lại kiểu crawl / cách đăng nhập")
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


TOPIC_FIELDS = {"desc": 1500, "include": 200, "exclude": 200, "audience": 40}  # trường chữ -> độ dài tối đa
TOPIC_LISTS = ("genres", "styles")


def clean_topic(raw) -> dict:
    """Chủ đề tìm kiếm từ giao diện: chỉ giữ các trường biết trước, cắt độ dài."""
    if isinstance(raw, str):  # bản cũ chỉ gửi một câu mô tả
        raw = {"desc": raw}
    raw = raw if isinstance(raw, dict) else {}
    topic = {k: str(raw.get(k) or "").strip()[:limit] for k, limit in TOPIC_FIELDS.items()}
    for k in TOPIC_LISTS:
        values = raw.get(k) if isinstance(raw.get(k), list) else []
        topic[k] = [str(v).strip()[:40] for v in values if str(v).strip()][:15]
    return topic


def topic_prompt(topic: dict, count: int, platforms: list) -> str:
    names = ", ".join(PLATFORM_NAMES.get(p, p) for p in platforms) or "Douyin, Xiaohongshu, Bilibili, Kuaishou"
    lines = [f"- Mô tả chi tiết: {topic['desc'] or '(không có)'}"]
    if topic["genres"]:
        lines.append("- Thể loại: " + ", ".join(topic["genres"]))
    if topic["styles"]:
        lines.append("- Phong cách hình ảnh: " + ", ".join(topic["styles"]))
    if topic["audience"]:
        lines.append(f"- Khán giả chính: {topic['audience']}")
    if topic["include"]:
        lines.append(f"- Từ/ý bắt buộc liên quan: {topic['include']}")
    if topic["exclude"]:
        lines.append(f"- Loại trừ (không tạo từ khóa dẫn tới nội dung này): {topic['exclude']}")
    return (
        f"Bạn là chuyên gia SEO tìm kiếm nội dung video trên mạng xã hội Trung Quốc ({names}).\n"
        f"Nhiệm vụ: tạo đúng {count} từ khóa tìm kiếm bằng tiếng Trung giản thể để tìm đúng loại video sau:\n"
        + "\n".join(lines) + "\n\n"
        "Yêu cầu:\n"
        "1. Dùng đúng cách người dùng Trung Quốc thực sự gõ tìm trên các nền tảng này: từ khóa tìm kiếm, "
        "tên hashtag/话题 phổ biến, cách gọi và tiếng lóng đang thịnh hành của thể loại.\n"
        "2. Phối hợp: vài từ khóa rộng có lượng tìm cao, còn lại là từ khóa dài (long-tail) bám sát mô tả, "
        "thể loại, phong cách và khán giả ở trên.\n"
        "3. Mỗi từ khóa 2-12 chữ, không trùng ý nhau, không chứa dấu phẩy, không đánh số.\n"
        "4. Kèm nghĩa tiếng Việt ngắn gọn (dưới 12 chữ) cho mỗi từ khóa.\n"
        'Trả về mảng JSON dạng [{"keyword": "...", "meaning_vi": "..."}].'
    )


def clean_keywords(raw, count: int) -> list:
    """Lọc từ khóa Gemini trả về -> [{"keyword", "meaning"}]: ngắn, không xuống dòng/dấu phẩy
    (dấu phẩy là ký tự tách từ khóa của base), không trùng."""
    result, seen = [], set()
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, str):
            entry = {"keyword": entry}
        if not isinstance(entry, dict) or not isinstance(entry.get("keyword"), str):
            continue
        kw = re.sub(r"[\s,，]+", " ", entry["keyword"]).strip()
        meaning = re.sub(r"\s+", " ", str(entry.get("meaning_vi") or "")).strip()[:60]
        if kw and len(kw) <= 40 and kw not in seen:
            seen.add(kw)
            result.append({"keyword": kw, "meaning": meaning})
    return result[:count]


def gemini_text(api_key: str, model: str, prompt: str, schema: dict, temperature: float) -> str:
    """Gọi Gemini, trả về chữ JSON của câu trả lời ("" nếu Gemini không trả gì)."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", model or ""):
        raise ValueError("Tên model Gemini không hợp lệ")
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema, "temperature": temperature},
    }
    req = urllib.request.Request(
        GEMINI_URL.format(model=urllib.parse.quote(model)), data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return ""


def gemini_keywords(api_key: str, model: str, topic, count: int, platforms: list) -> list:
    """Nhờ Gemini gợi ý từ khóa tìm kiếm tiếng Trung (kèm nghĩa tiếng Việt) cho một chủ đề."""
    schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {"keyword": {"type": "STRING"}, "meaning_vi": {"type": "STRING"}},
            "required": ["keyword", "meaning_vi"],
        },
    }
    text = gemini_text(api_key, model, topic_prompt(clean_topic(topic), count, platforms), schema, 0.9)
    try:
        return clean_keywords(json.loads(text), count)
    except (TypeError, ValueError):
        raise ValueError("Gemini không trả về danh sách từ khóa, thử lại hoặc đổi cách mô tả chủ đề") from None


def gemini_vi_titles(api_key: str, model: str, name: str) -> list:
    """Tên tiếng Việt (Hán Việt / dịch nghĩa) mà kênh Việt reup hay đặt cho một phim Trung. Tối đa 3 tên."""
    prompt = (f"Tên phim Trung Quốc: «{name}». Liệt kê tối đa 3 tên tiếng Việt mà kênh Việt Nam đăng lại phim này "
              "hay đặt: tên Hán Việt (phim cổ trang, tiên hiệp, huyền huyễn) và tên dịch nghĩa. Chỉ trả tên, không giải thích.")
    try:
        raw = json.loads(gemini_text(api_key, model, prompt, {"type": "ARRAY", "items": {"type": "STRING"}}, 0.2))
    except ValueError:
        raise ValueError("Gemini không trả về tên tiếng Việt") from None
    names = []
    for n in raw if isinstance(raw, list) else []:
        n = " ".join(str(n).split())[:80]
        if n and n != name and n not in names:
            names.append(n)
    return names[:3]


SERIES_VI_BATCH = 40  # số tên series gửi Gemini mỗi lần dịch
SERIES_VI_PARALLEL = 3  # số lần gọi Gemini cùng lúc (lần đầu có vài trăm tên: dịch song song cho nhanh)


def gemini_translate_names(api_key: str, model: str, names: dict) -> dict:
    """Dịch tên phim ngắn Trung sang tiếng Việt, một lần cho cả lô. names = {tên gốc: một câu mô tả video làm ngữ cảnh}.
    Trả về {tên gốc: tên Việt}, bỏ dòng Gemini trả sai tên gốc."""
    rows = [{"zh": n, "mo_ta": s} for n, s in names.items()]
    prompt = (
        "Bạn đặt tên tiếng Việt cho phim ngắn / phim AI Trung Quốc, như các kênh Việt Nam đăng lại hay đặt.\n"
        "Mỗi dòng có tên gốc (zh) và vài câu mô tả video của phim (mo_ta, cách nhau bằng /) để biết thể loại, bối cảnh, ý nghĩa.\n"
        "Quy tắc:\n"
        "- Hiểu nghĩa cả cụm rồi đặt tên tiếng Việt tự nhiên, dễ hiểu; KHÔNG dịch từng chữ, KHÔNG Hán Việt hóa từ thường "
        "(vd. 落榜 là thi trượt, không phải 'Lạc Bảng').\n"
        "- Phim cổ trang, tiên hiệp, huyền huyễn, tu tiên: dùng tên Hán Việt quen thuộc (vd. 傲娇师尊 → Sư Tôn Ngạo Kiều).\n"
        "- Tên riêng người / địa danh Trung giữ Hán Việt; tên Hàn, Nhật, Anh giữ cách gọi quen ở Việt Nam (梨泰院 → Itaewon).\n"
        "- Tiếng lóng mạng thì dịch theo nghĩa lóng.\n"
        "- Chỉ dịch tên phim, không thêm chữ như 'trọn bộ', 'tuyển tập', không thêm ngoặc hay giải thích.\n"
        "- Tên vốn không phải tiếng Trung thì giữ nguyên.\n"
        "Giữ nguyên tên gốc ở trường zh.\n" + json.dumps(rows, ensure_ascii=False))
    schema = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"zh": {"type": "STRING"}, "vi": {"type": "STRING"}},
                                         "required": ["zh", "vi"]}}
    try:
        raw = json.loads(gemini_text(api_key, model, prompt, schema, 0.2))
    except ValueError:
        raise ValueError("Gemini không trả về bản dịch tên series") from None
    wanted, out = set(names), {}
    for row in raw if isinstance(raw, list) else []:
        zh = str(row.get("zh", "")) if isinstance(row, dict) else ""
        vi = " ".join(str(row.get("vi", "")).split())[:80] if isinstance(row, dict) else ""
        if zh in wanted and vi and vi != zh:
            out[zh] = vi
    return out


SERIES_VI_VERSION = 3  # đổi cách dịch thì tăng số này: bản Gemini dịch cũ bị bỏ, app dịch lại một lần (bản sửa tay giữ nguyên)


def series_vi_file(data_dir: str) -> Path:
    return Path(data_dir) / "series_vi.json"  # tên series đã dịch, dịch rồi thì không gọi Gemini lại


def _str_dict(value) -> dict:
    return {str(k): str(v) for k, v in value.items() if isinstance(v, str)} if isinstance(value, dict) else {}


def read_series_vi(data_dir: str) -> tuple:
    """(bản Gemini dịch, bản người dùng sửa tay), mỗi bản {tên gốc: tên Việt}.
    Bản Gemini của cách dịch cũ (khác SERIES_VI_VERSION) coi như chưa dịch; bản sửa tay luôn giữ."""
    try:
        data = json.loads(series_vi_file(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {}
    if not isinstance(data, dict):
        return {}, {}
    auto = _str_dict(data.get("names")) if data.get("version") == SERIES_VI_VERSION else {}
    return auto, _str_dict(data.get("manual"))


def load_series_vi(data_dir: str) -> dict:
    """{tên gốc: tên Việt} để hiện: bản sửa tay thắng bản Gemini."""
    auto, manual = read_series_vi(data_dir)
    return {**auto, **manual}


def save_series_vi(data_dir: str, auto: dict = None, manual: dict = None) -> None:
    old_auto, old_manual = read_series_vi(data_dir)
    write_json(series_vi_file(data_dir), {"version": SERIES_VI_VERSION, "names": old_auto if auto is None else auto,
                                          "manual": old_manual if manual is None else manual})


def resolve_series_vi(series: dict, auto: dict, manual: dict) -> dict:
    """{tên hiện của series: tên Việt}. Tên hiện của một nhóm có thể đổi khi crawl thêm video, nên tra theo mọi tên
    trong nhóm: bản sửa tay (của tên nào trong nhóm cũng được) thắng, rồi bản Gemini của tên hiện, rồi của tên khác."""
    out = {**auto, **manual}
    for s in series.values():
        names = [s["name"], *s.get("members", {}).values()]
        hit = next((manual[n] for n in names if n in manual), None) or next((auto[n] for n in names if n in auto), None)
        if s["name"] and hit:
            out[s["name"]] = hit
    return out


def series_alias_file(data_dir: str) -> Path:
    return Path(data_dir) / "series_alias.json"  # gộp / tách series tay: {khóa series: khóa đích}


def load_series_alias(data_dir: str) -> dict:
    try:
        return _str_dict(json.loads(series_alias_file(data_dir).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def gemini_error(e: urllib.error.HTTPError) -> str:
    try:
        detail = json.loads(e.read())["error"]["message"]
    except (ValueError, KeyError, TypeError, OSError):
        detail = ""
    hint = {400: "API key sai hoặc yêu cầu không hợp lệ", 403: "API key không có quyền dùng Gemini API",
            404: "Không có model này, kiểm tra tên model trong Cài đặt", 429: "Hết hạn mức Gemini, thử lại sau"}
    return f"Gemini báo lỗi {e.code}: {hint.get(e.code, 'lỗi không rõ')}" + (f" ({detail[:200]})" if detail else "")


# ---------- Kiểm tra phim đã có kênh Việt đăng lại chưa (YouTube) ----------
# Đọc trang tìm kiếm công khai của YouTube (không cần đăng nhập, không cần API key), chỉ khi người dùng bấm.
YT_SEARCH_URL = "https://www.youtube.com/results?search_query={q}&sp=EgIQAQ%253D%253D"  # sp: chỉ lấy video
_VI_CHARS = re.compile(r"[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]")


def youtube_page(query: str) -> str:
    req = urllib.request.Request(YT_SEARCH_URL.format(q=urllib.parse.quote(query)),
                                 headers={"User-Agent": BROWSER_UA, "Accept-Language": "vi-VN,vi;q=0.9"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read(5_000_000).decode("utf-8", errors="replace")


def _yt_text(node) -> str:
    if not isinstance(node, dict):
        return ""
    return str(node.get("simpleText") or "".join(str(r.get("text", "")) for r in node.get("runs", []) if isinstance(r, dict)))


def _video_renderers(node):
    if isinstance(node, dict):
        if isinstance(node.get("videoRenderer"), dict):
            yield node["videoRenderer"]
        for value in node.values():
            yield from _video_renderers(value)
    elif isinstance(node, list):
        for value in node:
            yield from _video_renderers(value)


def parse_youtube_results(page: str) -> list:
    """HTML trang tìm kiếm YouTube -> [{id, url, title, channel, views, published, vi}] theo thứ tự YouTube xếp.
    vi = tiêu đề có chữ tiếng Việt (dấu hiệu kênh Việt đã đăng lại)."""
    start = page.find("{", page.find("ytInitialData")) if "ytInitialData" in page else -1
    if start < 0:
        raise ValueError("YouTube không trả danh sách video (có thể đang bị chặn), thử lại sau")
    try:
        data, _ = json.JSONDecoder().raw_decode(page, start)
    except ValueError:
        raise ValueError("Không đọc được kết quả YouTube") from None
    results = []
    for v in _video_renderers(data):
        vid = str(v.get("videoId", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            continue
        title = _yt_text(v.get("title"))[:200]
        results.append({
            "id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": title,
            "channel": _yt_text(v.get("ownerText") or v.get("longBylineText"))[:80],
            "views": _yt_text(v.get("viewCountText"))[:40], "published": _yt_text(v.get("publishedTimeText"))[:40],
            "vi": bool(_VI_CHARS.search(title.lower())),
        })
    return results


# ---------- Lịch ra mắt phim trên các nền tảng streaming (TMDB) ----------

def streamer_of(name):
    """Tên nhà cung cấp/ghi chú của TMDB -> tên nền tảng trong STREAMERS, không khớp thì None."""
    n = re.sub(r"\s+", " ", str(name or "").lower()).strip()
    for platform, info in STREAMERS.items():
        if any(n == a or n.startswith(a + " ") for a in info["names"]):
            return platform
    return None


def pacific_offset(day: date) -> int:
    """Lệch giờ Thái Bình Dương lúc 00:00 ngày đó: -7 nếu đang giờ mùa hè, ngược lại -8.
    Giờ mùa hè đổi lúc 2 giờ sáng: từ Chủ nhật thứ 2 tháng 3 đến Chủ nhật đầu tháng 11."""
    start = date(day.year, 3, 8 + (6 - date(day.year, 3, 8).weekday()) % 7)
    end = date(day.year, 11, 1 + (6 - date(day.year, 11, 1).weekday()) % 7)
    return -7 if start < day <= end else -8


def vn_release(day: str, platform) -> tuple:
    """Ngày phát hành ("2026-11-06...") -> (ngày, giờ) theo giờ Việt Nam (GMT+7).
    Nền tảng không rõ giờ lên sóng (rạp, TVING, kênh TV...) thì giữ ngày, giờ để trống."""
    d = date.fromisoformat(day[:10])
    drop = STREAMERS.get(platform, {}).get("drop")
    if not drop:
        return d.isoformat(), ""
    offset = 0 if drop == "gmt" else pacific_offset(d)
    vn = datetime(d.year, d.month, d.day) + timedelta(hours=7 - offset)
    return vn.date().isoformat(), vn.strftime("%H:%M")


def _flatrate(m: dict, *regions) -> list:
    """Nền tảng (trong STREAMERS) đang có phim theo gói thuê bao ở các nước đã cho."""
    results = (m.get("watch/providers") or {}).get("results", {})
    return [streamer_of(p.get("provider_name")) for r in regions for p in (results.get(r) or {}).get("flatrate", [])]


def _calendar_item(m: dict, kind: str, day: str, platforms: list, **extra) -> dict:
    """Một dòng lịch phim (phim lẻ hoặc phim bộ) từ chi tiết TMDB."""
    is_tv = extra.get("type") == "tv"
    title = (m.get("name") or m.get("original_name")) if is_tv else (m.get("title") or m.get("original_title"))
    title = title or ""
    vn_date, vn_time = vn_release(day, platforms[0] if platforms else None)
    videos = [v for v in (m.get("videos") or {}).get("results", [])
              if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser")
              and re.fullmatch(r"[\w-]{6,20}", str(v.get("key", "")))]
    videos.sort(key=lambda v: (v.get("type") != "Trailer", not v.get("official")))
    trailer = (f"https://www.youtube.com/watch?v={videos[0]['key']}" if videos else
               "https://www.youtube.com/results?search_query=" + urllib.parse.quote(f"{title} {' '.join(platforms[:1])} trailer"))
    return {
        "id": m.get("id"), "type": "movie", "title": title,
        "year": str(m.get("first_air_date" if is_tv else "release_date") or "")[:4],
        "date": vn_date, "time": vn_time, "platforms": platforms, "kind": kind, "region": "US",
        "lang": m.get("original_language") or "", "channel": "", "episodes": 0,
        "vn": any(STREAMERS[p]["vn"] for p in platforms), "overview": str(m.get("overview") or "")[:300],
        "trailer": trailer, "url": f"https://www.themoviedb.org/{'tv' if is_tv else 'movie'}/{m.get('id')}",
        "popularity": m.get("popularity") or 0, **extra,
    }


def parse_tmdb_movie(m: dict, start: str, end: str, region: str = "US") -> list:
    """Chi tiết phim TMDB (kèm release_dates, watch/providers, videos) -> các dòng lịch trong [start, end]:
    lên streaming/digital ở nước đó (type 4), và với Hàn Quốc thêm cả ngày chiếu rạp (type 3)."""
    dates = next((r.get("release_dates", []) for r in (m.get("release_dates") or {}).get("results", [])
                  if r.get("iso_3166_1") == region), [])
    items = []
    for rtype in (4, 3) if region == "KR" else (4,):
        hits = sorted((r for r in dates if r.get("type") == rtype and start <= str(r.get("release_date", ""))[:10] <= end),
                      key=lambda r: r["release_date"])
        if not hits:
            continue
        rel = hits[0]
        if rtype == 3:
            items.append(_calendar_item(m, "theater", rel["release_date"], [], region=region))
            continue
        platforms = list(dict.fromkeys(p for p in [streamer_of(rel.get("note"))] + _flatrate(m, region) if p))
        theatrical = any(r.get("type") in (2, 3) and r.get("release_date", "") < rel["release_date"] for r in dates)
        kind = "rent" if not platforms else "premiere" if theatrical else "original"
        items.append(_calendar_item(m, kind, rel["release_date"], platforms, region=region))
    return items


def parse_tmdb_tv(t: dict, start: str, end: str):
    """Chi tiết phim bộ TMDB (kèm watch/providers, videos) -> 1 dòng lịch theo ngày lên sóng tập đầu,
    None nếu ngoài khoảng [start, end]. Kênh/nền tảng lấy từ networks (tvN, JTBC, Netflix...)."""
    day = str(t.get("first_air_date") or "")[:10]
    if not (day and start <= day <= end):
        return None
    networks = [str(n.get("name") or "") for n in t.get("networks") or []]
    found = [streamer_of(n) for n in networks] + _flatrate(t, "KR", "US")
    platforms = list(dict.fromkeys(p for p in found if p))
    return _calendar_item(t, "series", day, platforms, type="tv", region="KR",
                          channel=", ".join(n for n in networks if n and not streamer_of(n)),
                          episodes=t.get("number_of_episodes") or 0)


def tmdb_get(key: str, path: str, params: dict) -> dict:
    headers = {"Accept": "application/json", "User-Agent": APP_NAME}
    if key.startswith("eyJ"):  # API Read Access Token (v4) đi qua header; API key (v3) đi qua tham số
        headers["Authorization"] = f"Bearer {key}"
    else:
        params = {**params, "api_key": key}
    req = urllib.request.Request(f"{TMDB_API}{path}?{urllib.parse.urlencode(params)}", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def fetch_movie_calendar(key: str, today: date, get=tmdb_get) -> list:
    """Lịch từ MOVIE_DAYS_BACK ngày trước đến MOVIE_DAYS_AHEAD ngày tới: phim lên streaming/digital ở Mỹ,
    phim Hàn chiếu rạp/lên streaming ở Hàn Quốc, và phim bộ Hàn (K-drama) lên sóng tập đầu."""
    start = (today - timedelta(days=MOVIE_DAYS_BACK)).isoformat()
    end = (today + timedelta(days=MOVIE_DAYS_AHEAD)).isoformat()

    def discover(kind, params, pages):
        ids = []
        for page in range(1, pages + 1):
            data = get(key, f"/discover/{kind}", {**params, "sort_by": "popularity.desc", "include_adult": "false", "page": page})
            ids += [m["id"] for m in data.get("results", []) if isinstance(m.get("id"), int)]
            if page >= data.get("total_pages", 0):
                break
        return ids

    def detail(kind, item_id):
        extra = "release_dates,watch/providers,videos" if kind == "movie" else "watch/providers,videos"
        try:
            return get(key, f"/{kind}/{item_id}", {"append_to_response": extra})
        except (urllib.error.URLError, OSError, ValueError):
            return None  # lỗi 1 phim thì bỏ phim đó

    dates = {"release_date.gte": start, "release_date.lte": end}
    movie_ids = dict.fromkeys(
        discover("movie", {"region": "US", "with_release_type": 4, **dates}, MOVIE_PAGES)
        + discover("movie", {"region": "KR", "with_release_type": "3|4", "with_original_language": "ko", **dates}, KR_PAGES))
    tv_ids = dict.fromkeys(discover("tv", {"with_original_language": "ko", "first_air_date.gte": start,
                                           "first_air_date.lte": end}, KR_PAGES))
    with ThreadPoolExecutor(max_workers=8) as pool:
        movies = [m for m in pool.map(lambda i: detail("movie", i), movie_ids) if m]
        shows = [t for t in pool.map(lambda i: detail("tv", i), tv_ids) if t]
    items = []
    for m in movies:
        us = parse_tmdb_movie(m, start, end, "US")
        kr = parse_tmdb_movie(m, start, end, "KR") if m.get("original_language") == "ko" else []
        # phim Hàn ra toàn cầu (Netflix...) đã có lịch Mỹ: bỏ bản trùng ở Hàn, chỉ giữ ngày chiếu rạp
        items += us + [i for i in kr if i["kind"] == "theater" or not us]
    items += [i for i in (parse_tmdb_tv(t, start, end) for t in shows) if i]
    return sorted(items, key=lambda i: (i["date"], i["time"] or "99", -i["popularity"]))


def tmdb_error(e: urllib.error.HTTPError) -> str:
    hint = {401: "API key sai hoặc đã bị thu hồi, kiểm tra lại trong Cài đặt", 404: "TMDB không tìm thấy dữ liệu",
            429: "Gọi TMDB quá nhiều, thử lại sau ít phút"}
    return f"TMDB báo lỗi {e.code}: {hint.get(e.code, 'lỗi không rõ')}"


def redact_args(args: list) -> list:
    """Bản để in ra log: giấu giá trị cookie."""
    return ["***" if i and args[i - 1] == "--cookies" else a for i, a in enumerate(args)]


def find_chrome() -> bool:
    candidates = [
        Path(os.environ.get(var, "")) / "Google/Chrome/Application/chrome.exe"
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
    ]
    return any(p.is_file() for p in candidates)


# ---------- Thẩm định series (theo quy trình chọn phim AI trên Douyin) ----------
# Bản quyền: chỉ loại khi dính nền tảng / IP lớn (không xin được). Bản quyền nhỏ vẫn nhận, người dùng tự xin.
# Dò trên mô tả video (kể cả hashtag). ponytail: chưa đọc bio kênh vì base không lưu bio; thêm khi base lưu.
RIGHTS_BIG = ("红果", "上🔴果", "番茄小说", "番茄免费", "@番茄", "起点", "阅文", "QQ阅读", "腾讯视频", "爱奇艺",
              "优酷", "哔哩哔哩", "B站", "书旗", "没版权", "西游", "三国演义", "红楼梦", "水浒", "封神")
RIGHTS_UNCLEAR = ("请勿爆书名", "不要发原书名", "请勿爆📖名", "小说改编", "改编自", "原著")
RIGHTS_SMALL = ("作登字", "作品登记证书", "原创剧", "原创短剧", "原创漫剧", "已购买官方版权")
RIGHTS_ORDER = {"big": 3, "unclear": 2, "small": 1, "": 0}
# "没有上架红果", "没上番茄" là lời khẳng định KHÔNG thuộc nền tảng lớn: bỏ trước khi dò
_RIGHTS_NEGATED = re.compile(r"(?:没有|没|未|并非|非|不是|不在)\s*(?:上架|上了|上|在)?\s*@?(?:红果|番茄)")
# "全集" không tính: tập lẻ hay ghi "看全集". "未完结", "还没完结" là chưa kết thúc.
_FINALE = re.compile(r"大结局|全剧终|(?<![未没还不])完结")
COMPILATION_WORDS = ("纯享版", "合集", "加长版", "加长篇")
_EP_RANGE = re.compile(r"(?:第\s*|(?<![A-Za-z])[Ee][Pp]\s*)?(\d{1,4})\s*[-~～—–至到]\s*(\d{1,4})\s*(?:集|话|話|合)"
                       r"|(?<![A-Za-z])[Ee][Pp]\s*(\d{1,4})\s*[-~～—–]\s*(\d{1,4})(?!\d)")
_EP_ONE = re.compile(r"第\s*(\d{1,4})\s*[集话話]|(?<![A-Za-z])[Ee][Pp]\s*\.?\s*(\d{1,4})(?!\d)")
# Tổng số tập ghi trong caption: 共30集, 全30集, 8/30集, 第8集/共30集
_EP_TOTAL = re.compile(r"[共全]\s*(\d{1,4})\s*[集话話]|(?<!\d)(\d{1,4})\s*/\s*(\d{1,4})\s*[集话話]"
                       r"|第\s*\d{1,4}\s*[集话話]?\s*/\s*共?\s*(\d{1,4})")
SERIES_ACTIVE_DAYS = 21  # tập mới nhất trong 21 ngày = còn ra; quá mà chưa có 大结局 = có thể đã drop
_SERIES_TITLE = re.compile(r"《([^《》\n]{1,40})》")  # kênh không dùng 合集 thì tên phim nằm trong 《...》 ở mô tả


# Phần thừa ở cuối tên hợp tập, không thuộc tên phim: "点开看全集", "正片", "合集", "剧宣花絮合集", "第一季"...
_NAME_TAIL = re.compile(r"(?:点开看全集|点击看全集|看全集|正片|完整版|全集|合集|纯享版|加长版|剧宣|花絮|第一季|[\s\-—_~～·•])+$")


def clean_series_name(name: str) -> str:
    """Tên series để hiện và để dịch: bỏ ngoặc 《》【】（）, bỏ khẩu hiệu sau "|", bỏ phần thừa ở cuối và emoji đầu."""
    name = " ".join(str(name or "").split())
    head = re.split(r"[|｜]", name)[0].strip()
    if len(re.sub(r"[\W_]+", "", head)) >= 2:  # "替罪重生|重生复仇港风AI短剧" -> "替罪重生"
        name = head
    name = re.sub(r"[《》【】「」『』〈〉]", "", name).strip()
    if re.fullmatch(r"[（(].+[)）]", name):
        name = name[1:-1].strip()
    name = re.sub(r"^[^\w]+", "", name)  # emoji / ký hiệu đầu tên
    return _NAME_TAIL.sub("", name).strip() or name


# Tên chung chung (thể loại, khẩu hiệu), không phải tên phim: không dùng để gom series (gom nhầm nhiều phim khác nhau)
_GENERIC_NAME = re.compile(r"(?:ai|aigc)?(?:短剧|漫剧|微短剧|动漫|动画|新剧|好剧|独家|原创|合集|全集|推荐|热播|精选|更新|预告|"
                           r"剧情|电视剧|电影|连续剧|小剧场|ai)+(?:推荐|合集|来袭)?", re.I)
# Kênh không dùng 《》: tên phim đứng ngay trước số tập ở đầu caption ("爱意瞒不住第5集", "总裁小闺女（第102集）", "gui夫EP03")
_NAME_BEFORE_EP = re.compile(r"^\s*([^\s#《》【】|｜:：,，。!！?？（）()\[\]]{2,20}?)\s*[（(]?\s*"
                             r"(?:第\s*\d{1,4}\s*[集话話]|(?<![A-Za-z])[Ee][Pp]\s*\d{1,4})")
_NAME_BRACKET = re.compile(r"【([^【】\n]{2,30})】")


def series_norm(name: str) -> str:
    """Tên đã làm sạch -> chuỗi so sánh (bỏ dấu câu, khoảng trắng, chữ hoa thường, full-width)."""
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", name)).lower()


def caption_series_name(title: str) -> str:
    """Tên phim đọc từ caption: 《tên》; không có thì 【tên】 hoặc tên đứng trước số tập (chỉ khi caption có số tập)."""
    title = str(title or "")
    m = _SERIES_TITLE.search(title)
    if m:
        return m.group(1)
    plain = re.sub(r"#\S+", " ", title)
    if not (_EP_ONE.search(plain) or _EP_RANGE.search(plain)):
        return ""
    m = _NAME_BRACKET.search(plain) or _NAME_BEFORE_EP.search(plain)
    return m.group(1) if m else ""


def series_key(series_id: str, series_name: str, title: str) -> tuple:
    """(khóa series, tên series đã làm sạch). Tên lấy từ 合集 Douyin, không có thì đọc từ caption (caption_series_name).
    Gom theo TÊN (khóa "n:<series_norm>"), để cùng một phim không bị tách đôi khi vừa nằm trong 合集 vừa có video lẻ.
    Tên chung chung ("AI漫剧", "短剧合集") hoặc không có tên: gom theo ID 合集 nếu có. Không nhận ra series thì ("", "")."""
    name = clean_series_name(series_name or caption_series_name(title))
    norm = series_norm(name)
    if norm and not _GENERIC_NAME.fullmatch(norm):
        return f"n:{norm}", name
    return (series_id, name) if series_id else ("", "")


def link_series(items: list, aliases: dict = None) -> None:
    """Nối các khóa series của cùng một phim (sửa trực tiếp series_key / series_name của từng video).

    1. Video trong 合集 mà caption ghi tên khác (合集 "瑞草学院", caption 《瑞草学院捉鬼师》): tên trong caption là cùng phim.
    2. Cùng một kênh, tên này chứa trọn tên kia ("无赦之罪" và "无赦之罪第一季警察篇"): cùng phim, gom về tên ngắn.
       Tên ngắn dưới 3 chữ không tính (dễ trùng nhầm, vd. "家").
    3. Người dùng gộp / tách tay (aliases: {khóa: khóa đích}; khóa đích = chính nó nghĩa là giữ riêng, không gom tự động).
    Khóa gốc trước khi nối được giữ ở series_key_raw để giao diện cho tách lại."""
    aliases = aliases or {}
    pinned = {k for k, t in aliases.items() if k == t}
    parent, names = {}, {}

    def find(k):
        while parent.get(k, k) != k:
            k = parent[k]
        return k

    def join(a, b, manual=False):  # đưa nhóm của a về nhóm của b
        if not manual and (a in pinned or b in pinned):
            return
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for item in items:
        raw = item.get("series_key_raw") or item.get("series_key") or ""
        item["series_key_raw"] = raw
        item["series_name_raw"] = item.get("series_name_raw") or item.get("series_name", "")
        if raw:
            names.setdefault(raw, item["series_name_raw"])
    for item in items:  # 1. tên trong caption của video thuộc 合集
        cap = item.get("caption_key")
        if cap and item["series_key_raw"] and cap != item["series_key_raw"]:
            join(cap, item["series_key_raw"])
    by_channel = {}
    for item in items:  # 2. cùng kênh, tên chứa tên
        channel = item.get("creator") or item.get("source_creator")
        if channel and item["series_key_raw"].startswith("n:"):
            by_channel.setdefault(channel, set()).add(item["series_key_raw"])
    for keys in by_channel.values():
        ordered = sorted(keys, key=len)
        for i, short in enumerate(ordered):
            if len(short) - 2 < 3:
                continue
            for long in ordered[i + 1:]:
                if short[2:] in long[2:]:
                    join(long, short)
    for key, target in aliases.items():  # 3. gộp tay
        if key != target:
            join(key, target, manual=True)
    # tên hiện cho cả nhóm: tên có nhiều video nhất (vd. "首尔今天有点鲁" 16 video, không phải "有点鲁" 1 video);
    # bằng nhau thì ưu tiên tên ngắn hơn
    counts = {}
    for item in items:
        if item["series_key_raw"]:
            counts[item["series_key_raw"]] = counts.get(item["series_key_raw"], 0) + 1
    best = {}
    for raw, n in counts.items():
        root = find(raw)
        if root not in best or (n, -len(names[raw])) > (counts[best[root]], -len(names[best[root]])):
            best[root] = raw
    for item in items:
        raw = item["series_key_raw"]
        if raw:
            root = find(raw)
            item["series_key"] = root
            item["series_name"] = names[best[root]] or item.get("series_name", "")


def rights_flag(text: str) -> tuple:
    """Mô tả video -> (mức bản quyền "big" | "unclear" | "small" | "", từ khóa khớp)."""
    text = _RIGHTS_NEGATED.sub("", str(text or ""))
    for level, words in (("big", RIGHTS_BIG), ("unclear", RIGHTS_UNCLEAR), ("small", RIGHTS_SMALL)):
        hit = next((w for w in words if w in text), None)
        if hit:
            return level, hit
    return "", ""


def episode_info(text: str) -> dict:
    """Số tập đọc từ mô tả (第N集, EP12, 1-8合...). Số tập Douyin trả về hay lệch nên mô tả được ưu tiên.
    episode/episode_end = 0 khi không đọc được; compilation = video gộp nhiều tập; finale = có 大结局/完结."""
    plain = re.sub(r"#\S+", " ", str(text or ""))  # hashtag kiểu #短剧合集 không phải nội dung video
    finale = bool(_FINALE.search(plain))
    t = _EP_TOTAL.search(plain)
    total = int(t.group(1) or t.group(3) or t.group(4)) if t else 0
    m = _EP_RANGE.search(plain)
    if m:
        a, b = (int(x) for x in (m.group(1, 2) if m.group(1) else m.group(3, 4)))
        if 0 < a < b:
            return {"episode": a, "episode_end": b, "compilation": True, "finale": finale, "total": total}
    m = _EP_ONE.search(plain)
    ep = int(m.group(1) or m.group(2)) if m else int(t.group(2)) if t and t.group(2) else 0
    # có số tập lẻ thì chữ "合集" chỉ là lời mời xem hợp tập, không phải video gộp
    return {"episode": ep, "episode_end": ep, "compilation": not ep and any(w in plain for w in COMPILATION_WORDS),
            "finale": finale, "total": total}


def series_summary(items: list, now: int) -> dict:
    """Gom video theo series -> {series_key: thẩm định}. status:
    "ended": có 大结局, hoặc đã thấy đủ số tập caption ghi (共30集 mà đã tới tập 30);
    "airing": có tập mới trong 21 ngày;
    "dropped": quá 21 ngày và chắc đã thấy tập mới nhất (tới tập Douyin báo đã ra, hoặc series của kênh đang theo dõi:
               mỗi lần kiểm tra lấy video mới nhất của kênh, series không xuất hiện nữa tức là kênh không up tiếp);
    "unknown": chưa crawl tới tập mới nhất (vd. chỉ gặp qua tìm kiếm) nên chưa kết luận được.
    ponytail: kênh up hơn 18 video giữa 2 lần kiểm tra thì có thể sót tập; kiểm tra dày hơn nếu cần chắc."""
    groups = {}
    for item in items:
        sid = item.get("series_key") or item.get("series_id")
        if sid:
            groups.setdefault(sid, {})[item["key"]] = item  # cùng video ở thư viện + kết quả: 1 lần
    out = {}
    for sid, videos in groups.items():
        vids = list(videos.values())
        singles = sorted((v for v in vids if v.get("episode") and not v.get("compilation")), key=lambda v: v["episode"])
        max_ep = max((v.get("episode_end") or v.get("episode") or 0 for v in vids), default=0)
        total = max((v.get("episodes_total") or 0 for v in vids), default=0)
        planned = max((v.get("episodes_planned") or 0 for v in vids), default=0)
        latest = max((v.get("published") or 0 for v in vids), default=0)
        channel = next((v["source_creator"] for v in vids if v.get("source_creator")), "")
        caught_up = (total and max_ep >= total) or (channel and not total)
        if any(v.get("finale") for v in vids) or (planned and max_ep >= planned):
            status = "ended"
        elif latest and now - latest <= SERIES_ACTIVE_DAYS * 86400:
            status = "airing"
        elif latest and caught_up:  # chỉ kết luận drop khi chắc đã thấy tập mới nhất
            status = "dropped"
        else:
            status = "unknown"
        # bản quyền tính cho cả series: một tập ghi 红果 là cả series bản quyền lớn
        worst = max((rights_flag(v.get("title", "")) for v in vids), key=lambda r: RIGHTS_ORDER[r[0]])
        newest = max(vids, key=lambda v: v.get("published") or 0)
        collection_id = next((v["series_id"] for v in vids if v.get("series_id")), "")
        out[sid] = {
            "name": next((v["series_name"] for v in vids if v.get("series_name")), ""),
            "channel": channel,  # link kênh theo dõi
            "creator": next((v["creator"] for v in vids if v.get("creator")), ""),  # mã ẩn danh kênh đăng (mọi video)
            "author": newest.get("author", ""),
            "series_id": collection_id, "collection": bool(collection_id),  # có 合集 trên Douyin: mở được link series
            # vài câu mô tả video (bỏ hashtag, khác nhau) làm ngữ cảnh khi nhờ Gemini dịch tên phim
            "sample": " / ".join(list(dict.fromkeys(
                s for s in (" ".join(re.sub(r"#\S+", " ", v.get("title", "")).split())[:70] for v in vids) if s))[:3]),
            # các tên gốc đã được nối vào series này (tự động hoặc gộp tay): {khóa gốc: tên}, để giao diện cho tách lại
            "members": dict(sorted({v.get("series_key_raw") or sid: v.get("series_name_raw") or v.get("series_name", "")
                                    for v in vids}.items())),
            "status": status, "rights": worst[0], "rights_hit": worst[1],
            # episodes_total: tổng để hiện "tập 8/30" (caption ghi tổng thì dùng, không thì số Douyin báo đã ra)
            "max_episode": max_ep, "episodes_total": planned or total, "planned": planned,
            "videos": len(vids), "latest": latest,
            "first": {"episode": singles[0]["episode"], "likes": singles[0]["likes"]} if singles else None,
            "last": {"episode": singles[-1]["episode"], "likes": singles[-1]["likes"]} if singles else None,
            **{k: sum(v.get(k) or 0 for v in vids) for k in ("likes", "comments", "collects", "shares")},
        }
    return out


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
    title = str(get("title") or row.get("desc") or "").strip()[:300]
    ep = episode_info(title)
    rights, rights_hit = rights_flag(title)
    sid = re.sub(r"\D", "", str(row.get("series_id") or ""))[:30]
    skey, sname = series_key(sid, str(row.get("series_name") or "").strip()[:120], title)
    # video thuộc 合集 mà caption ghi tên phim khác tên 合集: link_series nối hai tên này về một series
    cap = series_key("", "", title)[0] if sid else ""
    return {
        "key": f"{platform}_{item_id}",
        "platform": platform,
        "title": title,
        "url": url if url.startswith(("https://", "http://")) else "",
        "cover": cover if cover.startswith("https://") else "",
        "likes": parse_count(get("likes")),
        "views": parse_count(get("views")),
        "comments": parse_count(get("comments")),
        "shares": parse_count(get("shares")),
        "collects": parse_count(row.get("collected_count")),  # lượt lưu (Douyin, Xiaohongshu)
        "published": parse_time(get("published")),
        "crawled": parse_time(row.get("last_modify_ts")),
        "keyword": str(row.get("source_keyword") or ""),
        "source_creator": str(row.get("source_creator") or ""),  # link kênh người dùng theo dõi (chế độ creator)
        "creator": str(row.get("creator_hash") or "")[:64],  # mã ẩn danh của kênh đăng: biết hai video cùng một kênh
        "caption_key": cap,
        "author": str(row.get("nickname") or ""),
        # series (合集 của Douyin): ID chỉ gồm số, app ghép thành link douyin.com/collection/<id>
        "series_id": sid,  # chỉ có khi là 合集 thật (mở được link collection)
        "series_key": skey,  # khóa gom series: "n:<tên chuẩn hóa>" (合集 hoặc 《tên》 trong mô tả), không có tên thì ID 合集
        "series_name": sname,
        # số tập: ưu tiên số trong mô tả (第N集), số Douyin trả về hay lệch với hợp tập
        "episode": ep["episode"] or parse_count(row.get("episode")),
        "episode_end": ep["episode_end"] or ep["episode"] or parse_count(row.get("episode")),
        "episodes_total": parse_count(row.get("episodes_total")),  # 合集 Douyin: đã ra tới tập mấy
        "episodes_planned": ep["total"],  # caption ghi tổng số tập của phim (共30集, 8/30集)
        "compilation": ep["compilation"], "finale": ep["finale"],
        "rights": rights, "rights_hit": rights_hit,
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


def fetch_image(url: str, referer: str):
    """Tải một ảnh (https, tối đa 5 MB, đúng định dạng ảnh). Không tải được thì None."""
    if not str(url).startswith("https://"):
        return None
    req = urllib.request.Request(url, headers={"Referer": referer, "User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read(MAX_THUMB_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return data if len(data) <= MAX_THUMB_BYTES and image_mime(data) else None


def parse_user_results(line: str) -> list:
    """Dòng USER_SEARCH_RESULT <json> do base in ra -> danh sách kênh đã kiểm tra kiểu dữ liệu."""
    try:
        raw = json.loads(line.split("USER_SEARCH_RESULT ", 1)[1])
    except (IndexError, ValueError):
        return []
    results = []
    for user in raw if isinstance(raw, list) else []:
        sec_uid = str(user.get("sec_uid", "")) if isinstance(user, dict) else ""
        if not re.fullmatch(r"MS4wLjABAAAA[A-Za-z0-9_-]+", sec_uid):
            continue
        results.append({
            "sec_uid": sec_uid, "url": f"https://www.douyin.com/user/{sec_uid}",
            "nickname": str(user.get("nickname") or "")[:60], "douyin_id": str(user.get("douyin_id") or "")[:40],
            "followers": parse_count(user.get("followers")), "videos": parse_count(user.get("videos")),
            "signature": str(user.get("signature") or "")[:120], "avatar": str(user.get("avatar") or ""),
        })
    return results


def download_thumb(item: dict, data_dir: str):
    """Tải ảnh bìa về máy (một lần). Trả về đường dẫn file, hoặc None nếu không tải được."""
    path = thumb_file(data_dir, item["key"])
    if path.exists():
        return path
    if not item.get("cover"):
        return None
    data = fetch_image(item["cover"], THUMB_REFERER.get(item["platform"], ""))
    if data is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


ITEM_FIELDS = ("key", "platform", "title", "url", "cover", "likes", "views", "comments", "shares", "collects",
               "published", "crawled", "keyword", "author", "source_creator",
               "series_id", "series_key", "series_key_raw", "caption_key", "creator", "series_name", "series_name_raw",
               "episode", "episode_end", "episodes_total", "episodes_planned",
               "compilation", "finale", "rights", "rights_hit")


def library_file(data_dir: str) -> Path:
    """Thư viện = những video người dùng chọn giữ lại, lưu cạnh kết quả crawl."""
    return Path(data_dir) / "library.json"


def load_saved(data_dir: str) -> dict:
    try:
        data = json.loads(library_file(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def write_saved(data_dir: str, saved: dict) -> None:
    path = library_file(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def results_signature(data_dir: str) -> tuple:
    """Kích thước + giờ sửa của các file kết quả: đổi tức là có video mới."""
    sig = []
    for dirname in DATA_DIRS:
        for path in (Path(data_dir) / dirname / "jsonl").glob("*_contents_*.jsonl"):
            st = path.stat()
            sig.append((str(path), st.st_size, st.st_mtime_ns))
    return tuple(sorted(sig))


# ---------- Theo dõi kênh ----------

# ponytail: mới làm Douyin (base đã có chế độ kênh nhẹ cho Douyin); nền tảng khác thêm khi base có tương tự
FOLLOW_PLATFORMS = {"dy"}
FOLLOW_MAX_VIDEOS = 18  # đúng 1 trang danh sách video của kênh Douyin: đủ để thấy video mới
FOLLOW_HOURS = (1, 3, 6, 12, 24)
DEFAULT_FOLLOW_SETTINGS = {"auto": True, "hours": 6, "last_check": 0}
# Tag phân loại kênh do người dùng gắn (mã -> nhãn). "Lâu chưa update" là tag tự động, không lưu.
CHANNEL_TAGS = {"tiem_nang": "Tiềm năng", "chua_lam": "Chưa làm", "da_lam": "Đã làm",
                "het_series": "Hết series", "ngung_lam": "Ngưng làm"}
STALE_DAYS = 5  # kênh không đăng video mới quá số ngày này thì gắn "Lâu chưa update"
# Nhận làm kênh: ghi tên máy vào kênh, đồng bộ qua Supabase để máy khác (cùng tài khoản) thấy mà không làm trùng.
# ponytail: hai máy bấm nhận cùng lúc thì máy ghi sau thắng; cần khóa thật thì làm bảng riêng trên Supabase.
MACHINE_NAME = (os.environ.get("COMPUTERNAME") or "Máy này")[:40]


def follow_key(platform: str, channel_id: str) -> str:
    """Mã của kênh trong danh sách theo dõi: cùng kênh thì cùng mã, dù dán link dạng nào."""
    return f"{platform}_{safe_key(channel_id)}"


# kind: "follows" = kênh đang theo dõi, "blacklist" = kênh bị chặn (không cho theo dõi). Cùng định dạng, cùng cách đồng bộ.
def follows_file(data_dir: str, kind: str = "follows") -> Path:
    return Path(data_dir) / f"{kind}.json"


def load_follows(data_dir: str, kind: str = "follows") -> dict:
    try:
        data = json.loads(follows_file(data_dir, kind).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("url")} if isinstance(data, dict) else {}


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def deleted_file(data_dir: str, kind: str = "follows") -> Path:
    return Path(data_dir) / f"{kind}_deleted.json"  # kênh đã bỏ -> thời điểm bỏ (ms), để máy khác bỏ theo


def load_deleted(data_dir: str, kind: str = "follows") -> dict:
    try:
        data = json.loads(deleted_file(data_dir, kind).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: int(v) for k, v in data.items() if isinstance(v, (int, float))} if isinstance(data, dict) else {}


def _without_updated(channel):
    return {k: v for k, v in channel.items() if k != "updated"} if channel else None


def write_follows(data_dir: str, channels: dict, touch: bool = True, kind: str = "follows") -> None:
    """touch=True (sửa trên máy này): kênh thay đổi được ghi giờ sửa "updated", kênh bị bỏ được ghi vào
    follows_deleted.json. Đồng bộ dùng các mốc này để biết bản nào mới hơn. touch=False: ghi nguyên trạng."""
    if touch:
        now = int(time.time() * 1000)
        old = load_follows(data_dir, kind)
        for key, channel in channels.items():
            if _without_updated(channel) != _without_updated(old.get(key)):
                channel["updated"] = now
        gone = old.keys() - channels.keys()
        if gone:
            write_json(deleted_file(data_dir, kind), {**load_deleted(data_dir, kind), **{k: now for k in gone}})
    write_json(follows_file(data_dir, kind), channels)


# ---------- Đồng bộ kênh theo dõi qua Supabase ----------
# Project Supabase của chủ app gói sẵn trong exe (supabase.json: url + publishable key, key này được phép công khai).
# Người dùng chỉ đăng nhập bằng email + mật khẩu chủ app cấp. Bảng có RLS: tài khoản nào chỉ thấy dòng của mình.
# Tạo bảng: supabase_setup.sql (chủ app chạy 1 lần).
SYNC_EVERY = 300  # giây: kéo thay đổi từ máy khác khi app đang mở
SYNC_KINDS = ("blacklist", "follows")  # mỗi loại một bảng Supabase cùng tên; blacklist trước để loại kênh bị chặn


def bundled_supabase() -> dict:
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).parent
    try:
        cfg = json.loads((root / "supabase.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    url, key = str(cfg.get("url", "")).rstrip("/"), str(cfg.get("key", ""))
    return {"url": url, "key": key} if url.startswith("https://") and key else {}


SUPABASE = bundled_supabase()


def merge_follows(local: dict, deleted: dict, rows: list):
    """Ghép kênh của máy này với các dòng trên Supabase. Mỗi kênh lấy bản sửa sau cùng (kể cả lệnh bỏ theo dõi);
    riêng "known" (video đã thấy) gộp cả hai bên để không báo lại video máy kia đã báo.
    Trả về (kênh, kênh đã bỏ, các dòng cần đẩy lên)."""
    mine = {k: (int(c.get("updated", 0)), False, _without_updated(c)) for k, c in local.items()}
    for key, ts in deleted.items():
        if key not in mine or ts > mine[key][0]:
            mine[key] = (ts, True, None)
    theirs = {r["key"]: (int(r.get("updated") or 0), bool(r.get("deleted")), r.get("data") or None)
              for r in rows if isinstance(r, dict) and r.get("key")}
    channels, gone, push = {}, {}, []
    for key in mine.keys() | theirs.keys():
        a, b = mine.get(key), theirs.get(key)
        ts, dead, data = max((x for x in (a, b) if x), key=lambda x: x[0])
        if not dead and a and b and not a[1] and not b[1]:
            data = {**data, "known": sorted(set(a[2].get("known", [])) | set(b[2].get("known", [])))}
        if dead or not data or not data.get("url"):
            gone[key] = ts
            data, dead = {}, True
        else:
            channels[key] = {**data, "updated": ts}
        if b != (ts, dead, data or None):
            push.append({"key": key, "data": data, "deleted": dead, "updated": ts})
    return channels, gone, push


def supabase_call(url: str, key: str, path: str, method: str = "GET", body=None, token: str = "", headers=None):
    req = urllib.request.Request(
        url + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"apikey": key, "Content-Type": "application/json", "User-Agent": APP_NAME,
                 **({"Authorization": f"Bearer {token}"} if token else {}), **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def supabase_error(e: urllib.error.HTTPError) -> str:
    try:
        data = json.loads(e.read())
    except (OSError, ValueError):
        data = {}
    msg = data.get("msg") or data.get("error_description") or data.get("message") or data.get("error") or f"HTTP {e.code}"
    if data.get("code") in ("PGRST205", "42P01") or "public.follows" in str(msg) or "public.blacklist" in str(msg):
        return "Máy chủ đồng bộ chưa sẵn sàng (thiếu bảng dữ liệu), báo người quản trị"
    if "Invalid login credentials" in str(msg):
        return "Sai email hoặc mật khẩu"
    if "Invalid API key" in str(msg) or e.code == 401 and "apikey" in str(msg).lower():
        return "Máy chủ đồng bộ từ chối kết nối, báo người quản trị"
    return str(msg)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def resolve_short_url(url: str) -> str:
    """Link rút gọn v.douyin.com -> link thật (đọc header Location, không mở trang)."""
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        opener.open(urllib.request.Request(url, headers={"User-Agent": BROWSER_UA}), timeout=15)
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            return e.headers.get("Location", "")
        raise
    return ""


def parse_channel(text: str, resolve=resolve_short_url) -> tuple:
    """Link trang kênh (hoặc đoạn chia sẻ từ app Douyin) -> (nền tảng, link chuẩn, ID kênh)."""
    text = str(text or "").strip()
    match = re.search(r"douyin\.com/(?:share/)?user/(MS4wLjABAAAA[A-Za-z0-9_-]+)", text) or \
        re.fullmatch(r"(MS4wLjABAAAA[A-Za-z0-9_-]+)", text)
    if match:
        sec_uid = match.group(1)
        return "dy", f"https://www.douyin.com/user/{sec_uid}", sec_uid
    short = re.search(r"https?://v\.douyin\.com/[A-Za-z0-9_-]+/?", text)
    if short:
        try:
            target = resolve(short.group(0))
        except (urllib.error.URLError, OSError) as e:
            raise ValueError(f"Không mở được link rút gọn ({e}). Mở link trên trình duyệt rồi copy link trang kênh.") from None
        if "/user/" in target:
            return parse_channel(target, resolve=lambda u: "")
        if "/video/" in target or "/note/" in target:
            raise ValueError("Đây là link video, không phải link trang kênh. Trong Douyin, bấm vào avatar kênh rồi chia sẻ trang kênh.")
        raise ValueError("Link rút gọn này không dẫn tới trang kênh nào (có thể đã hết hạn). Hãy copy lại link trang kênh.")
    raise ValueError("Chưa nhận ra link kênh. Dán link dạng https://www.douyin.com/user/... hoặc link chia sẻ trang kênh từ app Douyin.")


def collect_channel_videos(data_dir: str, since_ms: int, avatars: dict = None) -> dict:
    """Video lấy được ở lần kiểm tra vừa rồi, gom theo link kênh: {link kênh: {ID video: item}}.
    avatars (nếu truyền vào) được điền {link kênh: link ảnh đại diện}."""
    found = {}
    for path in (Path(data_dir) / "douyin" / "jsonl").glob("creator_contents_*.jsonl"):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or not row.get("source_creator") or parse_time(row.get("last_modify_ts")) < since_ms // 1000:
                    continue
                item = normalize("dy", row)
                if item:
                    found.setdefault(row["source_creator"], {})[str(row.get("aweme_id"))] = item
                if avatars is not None and str(row.get("creator_avatar", "")).startswith("https://"):
                    avatars[row["source_creator"]] = row["creator_avatar"]
    return found


def avatar_file(data_dir: str, key: str) -> Path:
    return Path(data_dir) / "thumbs" / f"avatar_{safe_key(key)}.img"


def save_avatar(data_dir: str, key: str, url: str) -> bool:
    """Tải ảnh đại diện kênh về máy (ghi đè ảnh cũ). Link ảnh Douyin có hạn dùng nên phải lưu file."""
    data = fetch_image(url, THUMB_REFERER["dy"])
    if data is None:
        return False
    path = avatar_file(data_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return True


def apply_check(channels: dict, videos_by_channel: dict, now: int) -> dict:
    """So video vừa lấy với video đã biết của từng kênh. Trả về {key kênh: [video mới]}.

    Lần kiểm tra đầu của một kênh chỉ ghi nhận video hiện có, không báo (tránh báo cả kênh là "mới").
    Kênh không lấy được video (lỗi, bị chặn) thì giữ nguyên để lần sau so tiếp.
    """
    found = {}
    for key, channel in channels.items():
        videos = videos_by_channel.get(channel["url"])
        if videos is None:
            continue
        known = set(channel.get("known", []))
        new_ids = [vid for vid in videos if vid not in known]
        if channel.get("checked_at") and new_ids:
            channel["new"] = (channel.get("new", []) + new_ids)[-200:]
            channel["last_new_at"] = now
            found[key] = [videos[vid] for vid in new_ids]
        channel["known"] = list(known | set(videos))[-3000:]
        channel["checked_at"] = now
        latest = max((v.get("published", 0) for v in videos.values()), default=0)
        if latest:
            channel["last_video_at"] = latest
    return found


# ---------- Thông báo Telegram ----------

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def telegram_call(token: str, method: str, params: dict) -> object:
    if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{30,}", token or ""):
        raise ValueError("Bot token không đúng định dạng (dạng 123456789:AA...)")
    req = urllib.request.Request(TELEGRAM_API.format(token=token, method=method), method="POST",
                                 data=json.dumps(params).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:  # Telegram trả lỗi kèm mô tả trong body
        try:
            data = json.loads(e.read())
        except ValueError:
            data = {"description": f"HTTP {e.code}"}
    if not data.get("ok"):
        raise RuntimeError(f"Telegram báo lỗi: {data.get('description', 'không rõ')}")
    return data["result"]


def telegram_messages(found: dict, channels: dict, limit: int = 3500) -> list:
    """Tin nhắn HTML liệt kê video mới theo kênh; tách nhiều tin nếu dài (Telegram giới hạn 4096 ký tự)."""
    total = sum(len(v) for v in found.values())
    lines = [f"🎬 <b>{total} video mới</b> từ kênh đang theo dõi"]
    for key, videos in found.items():
        lines.append(f"\n<b>{html.escape(channels[key].get('name') or 'Kênh')}</b> ({len(videos)} video)")
        for v in sorted(videos, key=lambda x: x.get("published", 0), reverse=True):
            title = html.escape((v.get("title") or "(không có tiêu đề)")[:120])
            link = f'<a href="{html.escape(v["url"], quote=True)}">{title}</a>' if v.get("url") else title
            lines.append(f"• {link}")
    # tách theo từng dòng (không cắt giữa thẻ HTML), mỗi tin dưới giới hạn
    messages, current = [], ""
    for line in lines:
        if current and len(current) + len(line) + 1 > limit:
            messages.append(current)
            current = ""
        current += line + "\n"
    return messages + [current]


def thumb_data_uri(path: Path) -> str:
    # ponytail: gửi nguyên ảnh gốc sang giao diện; nếu thư viện lên vài nghìn video thì thu nhỏ ảnh khi tải về
    data = path.read_bytes()
    return f"data:{image_mime(data)};base64,{base64.b64encode(data).decode()}"


def get_clipboard() -> str:
    """Đọc chữ trong clipboard Windows (menu chuột phải → Dán của giao diện dùng)."""
    CF_UNICODETEXT = 13
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    for _ in range(20):  # clipboard có thể đang bị app khác giữ trong chốc lát
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise OSError("clipboard đang bị ứng dụng khác dùng")
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""  # clipboard đang chứa ảnh/file, không phải chữ
        pointer = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard(text: str) -> None:
    """Ghi chữ vào clipboard Windows (Unicode, không giới hạn độ dài như cách gọi PowerShell)."""
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = kernel32.GlobalUnlock.argtypes = kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    data = text.encode("utf-16-le") + b"\x00\x00"
    for _ in range(20):  # clipboard có thể đang bị app khác giữ trong chốc lát
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise OSError("clipboard đang bị ứng dụng khác dùng")
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        ctypes.memmove(kernel32.GlobalLock(handle), data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)  # Windows chỉ giữ handle khi SetClipboardData thành công
            raise ctypes.WinError()
    finally:
        user32.CloseClipboard()


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
    launch_fresh(target)
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
        self._procs = {}  # tên việc -> tiến trình đang chạy
        self._jobs = {}
        self._stop_event = threading.Event()
        self._state = "idle"  # idle | updating | crawling
        self._lines = []
        self._dropped = 0
        self._lock = threading.Lock()
        self._library = {}
        self._lib_rev = 0
        self._sig = None
        self._sig_checked = 0.0
        self._follow_lock = threading.Lock()
        self._sync_lock = threading.Lock()
        self._sync_access = None  # (access token, hết hạn lúc) của Supabase, chỉ giữ trong bộ nhớ
        self._sync_missing = set()  # bảng Supabase chưa có (đã báo 1 lần)
        self._avatar_pending = set()  # kênh đang tải ảnh đại diện (máy khác đồng bộ sang, chưa có file)
        self._movies_lock = threading.Lock()
        self._vi_running = False  # đang dịch tên series bằng Gemini
        self._vi_tried = set()  # tên series đã gửi dịch trong lần mở app này (lỗi thì không gọi lại liên tục)
        self._app_update = {}  # bản app mới hơn trên GitHub (nếu có)
        self._app_checked = 0.0
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

    def _run_logged(self, cmd: list, cwd: Path, name: str = "uv", prefix: str = "", extra_env: dict = None,
                    output: list = None) -> int:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), env={**uv_env(), **(extra_env or {})}, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
        )
        self._procs[name] = proc
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if not line.startswith("USER_SEARCH_RESULT "):  # dữ liệu cho app, không in ra nhật ký
                    self._write(prefix + line)
                if output is not None:
                    output.append(line)
            return proc.wait()
        finally:
            self._procs.pop(name, None)

    def _login_run(self, platform: str, headless: bool) -> bool:
        """Chạy base ở chế độ chỉ đăng nhập; True nếu base xác nhận đang đăng nhập (in LOGIN_OK)."""
        values = {"platform": platform, "crawl_type": "search", "targets": "login", "login": "qrcode",
                  "max_notes": 1, "max_comments": 1, "headless": headless}
        output = []
        self._run_logged([uv_path(), "run", "--no-sync", "python", "main.py", *build_args(values, self._data_dir())],
                         current_base(self._settings), "Đăng nhập", f"[Đăng nhập {PLATFORM_NAMES[platform]}] ",
                         extra_env={"MEDIACRAWLER_LOGIN_ONLY": "1"}, output=output)
        return any("LOGIN_OK" in line for line in output)

    def _login_worker(self, platform: str, verify_only: bool) -> None:
        name = PLATFORM_NAMES[platform]
        try:
            self._write("─" * 60)
            if not verify_only:
                # người dùng tự bấm Đăng nhập: cho phép đăng nhập mới dù trước đó đã có dấu
                (browser_data_dir() / f"{platform}.logged_in").unlink(missing_ok=True)
                self._write(f"Mở Chrome để đăng nhập {name}. Đăng nhập trong cửa sổ đó (QR, SMS hay mật khẩu), "
                            "đã đăng nhập sẵn thì app tự nhận ra.")
                if not self._login_run(platform, headless=False):
                    self._write(f"[LỖI] Chưa đăng nhập được {name} (hết giờ chờ hoặc cửa sổ bị đóng). Thử lại khi sẵn sàng.")
                    return
                self._write(f"Đã đăng nhập {name}. Đang mở lại Chrome để kiểm tra phiên được giữ sau khi đóng (cửa sổ tự đóng)...")
            # Không chạy ẩn: Douyin, Xiaohongshu coi Chrome ẩn là chưa đăng nhập dù phiên vẫn còn
            if self._login_run(platform, headless=False):
                self._write(f"Hoàn tất. {name}: phiên đăng nhập được giữ, các lần crawl sau không phải đăng nhập lại.")
            else:
                self._write(f"[LỖI] {name}: phiên đăng nhập KHÔNG còn sau khi đóng Chrome. Bấm Copy log và gửi để phân tích.")
        except Exception as e:  # noqa: BLE001
            self._write(f"[LỖI] Đăng nhập {name} thất bại: {e}")
        finally:
            self._state = "idle"
            self._lib_rev += 1  # giao diện tải lại trạng thái phiên

    def _check_app_update(self) -> None:
        self._app_checked = time.time()
        try:
            latest = latest_app_release()
        except (urllib.error.URLError, OSError, ValueError) as e:
            self._write(f"[CẢNH BÁO] Không kiểm tra được bản app mới: {e}")
            return
        if latest and latest["version_tuple"] > parse_version(APP_VERSION):
            if self._app_update.get("version") != latest["version"]:
                self._write(f"Có bản CrawlTool mới v{latest['version']} (đang dùng v{APP_VERSION}).")
            self._app_update = latest
        else:
            self._app_update = {}

    def _app_update_worker(self) -> None:
        info = self._app_update
        try:
            exe = Path(sys.executable)
            self._write(f"Đang tải CrawlTool v{info['version']}...")
            data = github_download_asset(info["asset_url"])
            apply_app_update(data, exe, info["sha256"])
            self._write("Đã cài bản mới, đang khởi động lại...")
            launch_fresh(exe)
            if self._window:
                self._window.destroy()
        except Exception as e:  # noqa: BLE001
            self._write(f"[LỖI] Cập nhật app thất bại: {e}")
            self._state = "idle"

    def _update_worker(self) -> None:
        """Mở app: kiểm tra bản app mới, và cài base code gói trong exe nếu máy chưa có đúng bản đó."""
        self._check_app_update()
        try:
            for message in migrate_browser_data():
                self._write(message)
            bundle = bundled_base()
            current = current_base(self._settings)
            if not bundle:
                if not current:
                    self._write("[LỖI] Bản app này không kèm base code. Tải lại CrawlTool.exe từ trang phát hành.")
                return
            zip_path, sha = bundle
            if sha == self._settings.get("base_sha") and current:
                remove_old_bases(keep=current)
                return
            self._write(f"Đang cài base code {sha[:7]}...")
            new_dir = base_dir_for(sha)
            shutil.rmtree(new_dir, ignore_errors=True)
            extract_zipball(zip_path.read_bytes(), new_dir)
            use_official_pypi(new_dir)
            self._write("Đang cài thư viện (lần đầu sẽ lâu vì phải tải Python và thư viện)...")
            if self._run_logged([uv_path(), "sync"], new_dir) != 0:
                raise RuntimeError("uv sync thất bại, xem log ở trên")
            self._settings["base_dir"], self._settings["base_sha"] = str(new_dir), sha
            save_settings(self._settings)
            remove_old_bases(keep=new_dir)
            self._write(f"Đã cài base code {sha[:7]}.")
        except Exception as e:  # noqa: BLE001 - lỗi nào cũng phải hiện cho người dùng thấy
            self._write(f"[LỖI] Cập nhật thất bại: {e}")
        finally:
            self._state = "idle"

    def _crawl_one(self, platform: str, args: list) -> None:
        name = PLATFORM_NAMES[platform]
        prefix = f"[{name}] " if len(self._jobs) > 1 else ""
        try:
            base = current_base(self._settings)
            code = self._run_logged([uv_path(), "run", "--no-sync", "python", "main.py", *args], base, name, prefix)
            self._write(f"{prefix}Hoàn tất." if code == 0 else f"[LỖI] {prefix}Crawler dừng với mã {code}.")
        except Exception as e:  # noqa: BLE001
            self._write(f"[LỖI] {prefix}Không chạy được crawler: {e}")

    def _crawl_worker(self) -> None:
        threads = []
        try:
            for i, (platform, args) in enumerate(self._jobs.items()):
                if i and self._stop_event.wait(PARALLEL_STAGGER_SEC):
                    break  # bấm Dừng trong lúc chờ mở nền tảng tiếp theo
                t = threading.Thread(target=self._crawl_one, args=(platform, args), daemon=True)
                t.start()
                threads.append(t)
            for t in threads:
                t.join()
            self._prefetch_thumbs()
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

    # --- theo dõi kênh ---
    def _follow_settings(self) -> dict:
        saved = self._settings.get("follow") if isinstance(self._settings.get("follow"), dict) else {}
        return {**DEFAULT_FOLLOW_SETTINGS, **saved}

    def _telegram_token(self) -> str:
        blob = (self._settings.get("telegram") or {}).get("token")
        try:
            return decrypt_token(blob) if blob else ""
        except (OSError, ValueError):
            return ""

    def _notify_telegram(self, found: dict, channels: dict) -> None:
        tg = self._settings.get("telegram") or {}
        token = self._telegram_token()
        if not (tg.get("enabled", True) and token and tg.get("chat_id")):
            return
        try:
            for text in telegram_messages(found, channels):
                telegram_call(token, "sendMessage", {"chat_id": tg["chat_id"], "text": text, "parse_mode": "HTML",
                                                     "disable_web_page_preview": True})
            self._write("Đã gửi thông báo Telegram.")
        except (ValueError, RuntimeError, urllib.error.URLError, OSError) as e:
            self._write(f"[LỖI] Không gửi được Telegram: {e}")

    def _follow_worker(self, channels: dict, settings: dict) -> None:
        started_ms = int(time.time() * 1000)
        data_dir = self._data_dir()
        try:
            login = self._login()
            values = {
                "platform": "dy", "crawl_type": "creator", "targets": "\n".join(c["url"] for c in channels.values()),
                "login": login["type"], "max_notes": FOLLOW_MAX_VIDEOS, "max_comments": 1, "comments": False,
                "media": False, "headless": False,  # Douyin chặn khi chạy ẩn
            }
            if login["type"] == "cookie":
                values["cookies"] = self._cookies().get("dy", "")
            args = build_args(values, data_dir)
            self._write("─" * 60)
            self._write(f"Kiểm tra {len(channels)} kênh theo dõi (mỗi kênh {FOLLOW_MAX_VIDEOS} video mới nhất): "
                        + " ".join(redact_args(args)))
            code = self._run_logged([uv_path(), "run", "--no-sync", "python", "main.py", *args],
                                    current_base(self._settings), "Theo dõi", "[Theo dõi] ")
            if code != 0:
                self._write(f"[LỖI] [Theo dõi] Crawler dừng với mã {code}; kênh nào đã lấy được vẫn được so sánh.")
            with self._follow_lock:
                all_channels = load_follows(data_dir)
                avatars = {}
                found = apply_check(all_channels, collect_channel_videos(data_dir, started_ms, avatars), int(time.time()))
                changed_avatars = []
                for key, channel in all_channels.items():
                    url = avatars.get(channel["url"])
                    if url and (url != channel.get("avatar") or not avatar_file(data_dir, key).exists()):
                        channel["avatar"] = url
                        changed_avatars.append((key, url))
                write_follows(data_dir, all_channels)
            for key, url in changed_avatars:
                save_avatar(data_dir, key, url)
            total = sum(len(v) for v in found.values())
            self._write(f"Có {total} video mới từ kênh theo dõi." if total else "Kênh theo dõi chưa có video mới.")
            if found:
                self._notify_telegram(found, all_channels)
                self._prefetch_thumbs()
        except Exception as e:  # noqa: BLE001
            self._write(f"[LỖI] Kiểm tra kênh theo dõi thất bại: {e}")
        finally:
            self._settings["follow"] = {**self._follow_settings(), "last_check": int(time.time())}
            save_settings(self._settings)
            self._state = "idle"
            self._lib_rev += 1
            self._sync()

    def _scheduler(self) -> None:
        """Chạy nền khi app mở: đến giờ thì tự kiểm tra kênh theo dõi (app đang bận thì đợi)."""
        self._sync()
        synced = time.time()
        while True:
            time.sleep(60)
            if time.time() - synced >= SYNC_EVERY:
                self._sync()
                synced = time.time()
            if time.time() - self._app_checked > 6 * 3600:  # app mở lâu: 6 giờ hỏi bản app mới 1 lần
                self._check_app_update()
            settings = self._follow_settings()
            channels = load_follows(self._data_dir())
            # checked_at đồng bộ từ máy khác: máy kia vừa kiểm tra thì máy này chưa cần kiểm tra lại
            last = max([settings["last_check"], *(c.get("checked_at", 0) for c in channels.values())])
            due = time.time() - last >= settings["hours"] * 3600
            if settings["auto"] and due and self._state == "idle" and channels:
                self.check_follows()

    def _avatar(self, key: str, channel: dict) -> str:
        """Ảnh đại diện kênh dạng data URI. Chưa có file mà có link (vd. đồng bộ từ máy khác) thì tải nền."""
        data_dir = self._data_dir()
        path = avatar_file(data_dir, key)
        if path.exists():
            return thumb_data_uri(path)
        url = channel.get("avatar", "")
        if url and key not in self._avatar_pending:
            self._avatar_pending.add(key)

            def fetch():
                if save_avatar(data_dir, key, url):
                    self._lib_rev += 1
            threading.Thread(target=fetch, daemon=True).start()
        return ""

    def follows(self) -> dict:
        channels = load_follows(self._data_dir())
        tg = self._settings.get("telegram") or {}
        settings = self._follow_settings()
        return {
            "channels": [{"key": k, "platform": c.get("platform"), "url": c["url"], "name": c.get("name", ""),
                          "added_at": c.get("added_at", 0), "checked_at": c.get("checked_at", 0),
                          "new": c.get("new", []), "last_new_at": c.get("last_new_at", 0),
                          "tags": [t for t in c.get("tags", []) if t in CHANNEL_TAGS],
                          "last_video_at": c.get("last_video_at", 0),
                          "stale": bool(c.get("last_video_at")) and time.time() - c["last_video_at"] > STALE_DAYS * 86400,
                          "holder": c.get("holder", ""), "holder_at": c.get("holder_at", 0),
                          "avatar": self._avatar(k, c)} for k, c in channels.items()],
            "machine": MACHINE_NAME,
            "settings": {**settings, "next_check": settings["last_check"] + settings["hours"] * 3600},
            "telegram": {"has_token": bool(tg.get("token")), "chat_id": tg.get("chat_id", ""),
                         "chat_name": tg.get("chat_name", ""), "enabled": tg.get("enabled", True)},
            "hours_options": list(FOLLOW_HOURS),
            "tags": CHANNEL_TAGS, "stale_days": STALE_DAYS,
            "blacklist": sorted(({"key": k, "platform": c.get("platform"), "url": c["url"], "name": c.get("name", ""),
                                  "added_at": c.get("added_at", 0), "avatar": self._avatar(k, c)}
                                 for k, c in load_follows(self._data_dir(), "blacklist").items()),
                                key=lambda c: -c["added_at"]),
        }

    def follow_channel(self, text, name="", avatar="") -> dict:
        try:
            platform, url, channel_id = parse_channel(text)
        except ValueError as e:
            return {"error": str(e)}
        key = follow_key(platform, channel_id)
        with self._follow_lock:
            if key in load_follows(self._data_dir(), "blacklist"):
                return {"error": "Kênh này đang nằm trong Blacklist. Gỡ khỏi Blacklist (tab Blacklist) nếu muốn theo dõi."}
            channels = load_follows(self._data_dir())
            if key in channels:  # trùng: chỉ rõ là kênh nào (tên anh đã đặt có thể khác hẳn tên kênh)
                old = channels[key]
                # strftime trên Windows không nhận chữ có dấu: để chữ "từ" ngoài định dạng ngày
                since = " từ " + datetime.fromtimestamp(old["added_at"]).strftime("%d/%m/%Y") if old.get("added_at") else ""
                return {"error": f"Kênh này đã có trong danh sách: \"{old.get('name', '')}\"{since}",
                        "duplicate": {"key": key, "name": old.get("name", ""), "added_at": old.get("added_at", 0)}}
            channels[key] = {"platform": platform, "url": url, "name": str(name or "").strip()[:60] or f"Kênh Douyin …{channel_id[-6:]}",
                             "added_at": int(time.time()), "known": [], "new": []}
            if str(avatar or "").startswith("https://"):  # theo dõi từ kết quả tìm kênh: có sẵn ảnh đại diện
                channels[key]["avatar"] = str(avatar)
            write_follows(self._data_dir(), channels)
        self._sync_soon()
        return self.follows()

    def search_channels(self, query) -> dict:
        """Tìm kênh Douyin theo tên (mở Chrome của Douyin như lúc kiểm tra kênh, không crawl, không lưu gì)."""
        query = str(query or "").strip()[:50]
        if not query:
            return {"error": "Nhập tên kênh cần tìm"}
        if self._state != "idle":
            return {"error": "App đang bận, đợi xong rồi thử lại."}
        if not current_base(self._settings):
            return {"error": "Chưa có base code. Vào tab Cài đặt → Kiểm tra cập nhật."}
        self._state = "searching"
        try:
            login = self._login()
            values = {"platform": "dy", "crawl_type": "search", "targets": "search", "login": login["type"],
                      "max_notes": 1, "max_comments": 1, "headless": False}  # Douyin chặn khi chạy ẩn
            if login["type"] == "cookie":
                values["cookies"] = self._cookies().get("dy", "")
            output = []
            self._write("─" * 60)
            self._write(f"Tìm kênh Douyin theo tên: {query}")
            self._run_logged([uv_path(), "run", "--no-sync", "python", "main.py", *build_args(values, self._data_dir())],
                             current_base(self._settings), "Tìm kênh", "[Tìm kênh] ",
                             extra_env={"MEDIACRAWLER_USER_SEARCH": query}, output=output)
            line = next((l for l in output if l.startswith("USER_SEARCH_RESULT ")), None)
            if line is None:
                return {"error": "Không tìm được kênh, xem Nhật ký ở tab Crawl để biết lý do."}
            results = parse_user_results(line)
            followed = load_follows(self._data_dir())
            banned = load_follows(self._data_dir(), "blacklist")
            with ThreadPoolExecutor(max_workers=8) as pool:
                avatars = list(pool.map(lambda r: fetch_image(r["avatar"], THUMB_REFERER["dy"]), results))
            for result, avatar in zip(results, avatars):
                key = follow_key("dy", result["sec_uid"])  # so theo mã kênh như lúc bấm Theo dõi, không theo link
                result["followed"] = key in followed
                result["blacklisted"] = key in banned
                if result["followed"]:
                    result["followed_key"], result["followed_name"] = key, followed[key].get("name", "")
                result["avatar_url"] = result["avatar"]  # link gốc, để lưu ảnh đại diện khi bấm Theo dõi
                result["avatar"] = f"data:{image_mime(avatar)};base64,{base64.b64encode(avatar).decode()}" if avatar else ""
            self._write(f"Tìm thấy {len(results)} kênh.")
            return {"results": results}
        except Exception as e:  # noqa: BLE001
            self._write(f"[LỖI] Tìm kênh thất bại: {e}")
            return {"error": f"Tìm kênh thất bại: {e}"}
        finally:
            self._state = "idle"

    def blacklist_channels(self, keys) -> dict:
        """Đưa các kênh đang theo dõi vào blacklist (đồng thời bỏ theo dõi)."""
        now = int(time.time())
        with self._follow_lock:
            data_dir = self._data_dir()
            channels, banned = load_follows(data_dir), load_follows(data_dir, "blacklist")
            for key in set(keys or []) & channels.keys():
                c = channels.pop(key)
                banned[key] = {k: c[k] for k in ("platform", "url", "name", "avatar") if c.get(k)} | {"added_at": now}
            write_follows(data_dir, banned, kind="blacklist")
            write_follows(data_dir, channels)
        self._sync_soon()
        return self.follows()

    def blacklist_add(self, text, name="") -> dict:
        """Thêm kênh vào blacklist bằng link (kênh chưa theo dõi cũng được)."""
        try:
            platform, url, channel_id = parse_channel(text)
        except ValueError as e:
            return {"error": str(e)}
        key = f"{platform}_{safe_key(channel_id)}"
        with self._follow_lock:
            data_dir = self._data_dir()
            banned = load_follows(data_dir, "blacklist")
            if key in banned:
                return {"error": "Kênh này đã có trong Blacklist"}
            channels = load_follows(data_dir)
            followed = channels.pop(key, None)
            banned[key] = {"platform": platform, "url": url, "added_at": int(time.time()),
                           "name": str(name or "").strip()[:60] or (followed or {}).get("name") or f"Kênh Douyin …{channel_id[-6:]}"}
            if (followed or {}).get("avatar"):
                banned[key]["avatar"] = followed["avatar"]
            write_follows(data_dir, banned, kind="blacklist")
            if followed:
                write_follows(data_dir, channels)
        self._sync_soon()
        return self.follows()

    def unblacklist(self, key) -> dict:
        with self._follow_lock:
            banned = load_follows(self._data_dir(), "blacklist")
            banned.pop(key, None)
            write_follows(self._data_dir(), banned, kind="blacklist")
        self._sync_soon()
        return self.follows()

    def unfollow(self, key) -> dict:
        with self._follow_lock:
            channels = load_follows(self._data_dir())
            channels.pop(key, None)
            write_follows(self._data_dir(), channels)
        self._sync_soon()
        return self.follows()

    def rename_channel(self, key, name) -> dict:
        with self._follow_lock:
            channels = load_follows(self._data_dir())
            if key in channels and str(name or "").strip():
                channels[key]["name"] = str(name).strip()[:60]
                write_follows(self._data_dir(), channels)
        self._sync_soon()
        return self.follows()

    def set_channel_tags(self, key, tags) -> dict:
        tags = [t for t in CHANNEL_TAGS if t in (tags or [])]  # giữ thứ tự cố định, bỏ mã lạ
        with self._follow_lock:
            channels = load_follows(self._data_dir())
            if key in channels:
                channels[key]["tags"] = tags
                write_follows(self._data_dir(), channels)
        self._sync_soon()
        return self.follows()

    def tag_channels(self, keys, tag, on=True) -> dict:
        """Gắn (on=True) hoặc bỏ một tag cho nhiều kênh cùng lúc."""
        if tag not in CHANNEL_TAGS:
            return {"error": "Tag không hợp lệ"}
        keys = set(keys or [])
        with self._follow_lock:
            channels = load_follows(self._data_dir())
            for key in keys & channels.keys():
                tags = set(channels[key].get("tags", [])) - {tag} | ({tag} if on else set())
                channels[key]["tags"] = [t for t in CHANNEL_TAGS if t in tags]
            write_follows(self._data_dir(), channels)
        self._sync_soon()
        return self.follows()

    def claim_channel(self, key, on=True, force=False) -> dict:
        """Nhận làm kênh trên máy này (on=False: bỏ nhận). Đồng bộ trước để thấy máy khác vừa nhận chưa;
        máy khác đang giữ thì chỉ đổi khi force=True (người dùng đã xác nhận)."""
        self._sync()
        with self._follow_lock:
            channels = load_follows(self._data_dir())
            channel = channels.get(key)
            if not channel:
                return {"error": "Không tìm thấy kênh"}
            holder = channel.get("holder", "")
            if holder and holder != MACHINE_NAME and not force:
                return {"error": f"Máy {holder} đang làm kênh này", "holder": holder}
            if on:
                channel["holder"], channel["holder_at"] = MACHINE_NAME, int(time.time())
            else:
                channel.pop("holder", None)
                channel.pop("holder_at", None)
            write_follows(self._data_dir(), channels)
        self._sync_soon()
        return self.follows()

    def mark_seen(self, key=None) -> dict:
        with self._follow_lock:
            channels = load_follows(self._data_dir())
            for k, channel in channels.items():
                if key in (None, k):
                    channel["new"] = []
            write_follows(self._data_dir(), channels)
        self._sync_soon()
        return self.follows()

    def set_follow_settings(self, auto=None, hours=None) -> dict:
        settings = self._follow_settings()
        if auto is not None:
            settings["auto"] = bool(auto)
        if hours is not None:
            if int(hours) not in FOLLOW_HOURS:
                return {"error": "Chu kỳ kiểm tra không hợp lệ"}
            settings["hours"] = int(hours)
        self._settings["follow"] = settings
        save_settings(self._settings)
        return self.follows()

    def check_follows(self) -> dict:
        if self._state != "idle":
            return {"error": "App đang bận, đợi xong rồi thử lại."}
        if not current_base(self._settings):
            return {"error": "Chưa có base code. Vào tab Cài đặt → Kiểm tra cập nhật."}
        self._sync()  # lấy kênh mới thêm / video đã báo từ máy khác trước khi kiểm tra
        channels = {k: c for k, c in load_follows(self._data_dir()).items() if c.get("platform") in FOLLOW_PLATFORMS}
        if not channels:
            return {"error": "Chưa theo dõi kênh nào"}
        self._state = "following"
        threading.Thread(target=self._follow_worker, args=(channels, self._follow_settings()), daemon=True).start()
        return {"ok": True}

    # --- Telegram ---
    def set_telegram(self, token=None, enabled=None, clear=False) -> dict:
        tg = dict(self._settings.get("telegram") or {})
        if clear:
            tg = {}
        elif token and str(token).strip():
            token = str(token).strip()
            try:
                bot = telegram_call(token, "getMe", {})
            except (ValueError, RuntimeError, urllib.error.URLError, OSError) as e:
                return {"error": f"Không kiểm tra được bot: {e}"}
            tg.update({"token": encrypt_token(token), "bot_name": bot.get("username", ""), "chat_id": "", "chat_name": ""})
        if enabled is not None:
            tg["enabled"] = bool(enabled)
        self._settings["telegram"] = tg
        save_settings(self._settings)
        return self.follows()

    def telegram_detect_chat(self) -> dict:
        """Tìm chat của người dùng: người dùng nhắn bất kỳ tin gì cho bot trước, app đọc tin mới nhất."""
        token = self._telegram_token()
        if not token:
            return {"error": "Chưa nhập bot token"}
        try:
            updates = telegram_call(token, "getUpdates", {"limit": 50})
        except (ValueError, RuntimeError, urllib.error.URLError, OSError) as e:
            return {"error": str(e)}
        chats = [u["message"]["chat"] for u in updates if isinstance(u, dict) and isinstance(u.get("message"), dict)]
        if not chats:
            bot = (self._settings.get("telegram") or {}).get("bot_name", "")
            return {"error": f"Chưa thấy tin nhắn nào. Mở Telegram, nhắn /start cho bot @{bot} rồi bấm lại."}
        chat = chats[-1]
        name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) or chat.get("username", "")
        self._settings["telegram"] = {**(self._settings.get("telegram") or {}), "chat_id": str(chat["id"]), "chat_name": name}
        save_settings(self._settings)
        return self.follows()

    def telegram_test(self) -> dict:
        tg = self._settings.get("telegram") or {}
        token = self._telegram_token()
        if not (token and tg.get("chat_id")):
            return {"error": "Cần nhập bot token và lấy chat ID trước"}
        try:
            telegram_call(token, "sendMessage", {"chat_id": tg["chat_id"], "text": "✅ CrawlTool đã kết nối. Khi kênh theo dõi có video mới, bot sẽ nhắn ở đây."})
        except (ValueError, RuntimeError, urllib.error.URLError, OSError) as e:
            return {"error": str(e)}
        return {"ok": True}

    def _on_closed(self) -> None:
        self.stop()

    # --- gọi từ giao diện ---
    def init(self) -> dict:
        if self._settings.pop("token", None):  # token GitHub của bản cũ (repo riêng tư): không còn dùng, xóa khỏi máy
            save_settings(self._settings)
        saved = dict(self._settings.get("form", {}))
        old_login = saved.pop("login", None)
        if "login" not in self._settings and old_login in UI_LOGIN_TYPES:
            self._settings["login"] = {"type": old_login}
        if "platform" in saved and "platforms" not in saved:  # form của bản trước chỉ có 1 nền tảng
            saved["platforms"] = [saved.pop("platform")]
        platforms = saved.get("platforms")
        if not (isinstance(platforms, list) and platforms and set(platforms) <= PLATFORM_CODES):
            saved.pop("platforms", None)
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
            "form": form, "data_dir": self._data_dir(),
            "warnings": warnings, "repo": APP_REPO, "login": self.get_login(), "gemini": self.get_gemini(), "tmdb": self.get_tmdb(),
            "sync": self.get_sync(),
            "app_version": APP_VERSION, "can_self_update": bool(getattr(sys, "frozen", False)),
            "ai_topic": clean_topic(self._settings.get("ai_topic")),
        }

    # --- đăng nhập (trong Cài đặt) ---
    def _login(self) -> dict:
        login = self._settings.get("login")
        login = login if isinstance(login, dict) else {}
        login_type = login.get("type") if login.get("type") in UI_LOGIN_TYPES else "qrcode"
        cookies = login.get("cookies") if isinstance(login.get("cookies"), dict) else {}
        return {"type": login_type, "cookies": {p: c for p, c in cookies.items() if p in PLATFORM_CODES}}

    def _cookies(self) -> dict:
        result = {}
        for platform, blob in self._login()["cookies"].items():
            try:
                result[platform] = decrypt_token(blob)
            except (OSError, ValueError):
                self._write(f"[LỖI] Không giải mã được cookie {PLATFORM_NAMES[platform]}, hãy dán lại trong Cài đặt.")
        return result

    def get_login(self) -> dict:
        login = self._login()
        sessions = sorted(p for p in PLATFORM_CODES if (browser_data_dir() / f"{p}.logged_in").exists())
        return {"type": login["type"], "saved": sorted(login["cookies"]), "sessions": sessions}

    def login_platform(self, platform, verify_only=False) -> dict:
        """Nút Đăng nhập / Kiểm tra phiên trong Cài đặt: đăng nhập 1 lần ngoài lúc crawl, rồi tự kiểm tra phiên còn giữ."""
        if platform not in PLATFORM_CODES:
            return {"error": "Nền tảng không hợp lệ"}
        if self._state != "idle":
            return {"error": "App đang bận, đợi xong rồi thử lại."}
        if not current_base(self._settings):
            return {"error": "Chưa có base code. Vào tab Cài đặt → Kiểm tra cập nhật."}
        self._state = "login"
        threading.Thread(target=self._login_worker, args=(platform, bool(verify_only)), daemon=True).start()
        return {"ok": True}

    def reset_session(self, platform) -> dict:
        """Cho phép lần crawl tới đăng nhập lại nền tảng này (base mặc định không tự đăng nhập lặp lại)."""
        if platform not in PLATFORM_CODES:
            return {"error": "Nền tảng không hợp lệ"}
        (browser_data_dir() / f"{platform}.logged_in").unlink(missing_ok=True)
        self._write(f"Đã cho phép đăng nhập lại {PLATFORM_NAMES[platform]} ở lần crawl tới.")
        return self.get_login()

    def set_login_type(self, login_type) -> dict:
        if login_type not in UI_LOGIN_TYPES:
            return {"error": "Cách đăng nhập không hợp lệ"}
        self._settings["login"] = {**self._login(), "type": login_type}
        save_settings(self._settings)
        return self.get_login()

    def set_cookie(self, platform, cookie) -> dict:
        if platform not in PLATFORM_CODES:
            return {"error": "Nền tảng không hợp lệ"}
        login = self._login()
        cookie = str(cookie or "").strip()
        if cookie:
            login["cookies"][platform] = encrypt_token(cookie)
        else:
            login["cookies"].pop(platform, None)
        self._settings["login"] = login
        save_settings(self._settings)
        return self.get_login()

    # --- Gemini (trong Cài đặt) ---
    def get_gemini(self) -> dict:
        gemini = self._settings.get("gemini") if isinstance(self._settings.get("gemini"), dict) else {}
        return {"has_key": bool(gemini.get("key")), "model": gemini.get("model") or GEMINI_DEFAULT_MODEL,
                "enabled": gemini.get("enabled", True)}

    def set_gemini(self, key=None, model=None, clear=False, enabled=None) -> dict:
        gemini = dict(self._settings.get("gemini") or {})
        if enabled is not None:
            gemini["enabled"] = bool(enabled)
        if clear:
            gemini.pop("key", None)
        elif key and str(key).strip():
            gemini["key"] = encrypt_token(str(key).strip())
        if model is not None:
            model = str(model).strip() or GEMINI_DEFAULT_MODEL
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", model):
                return {"error": "Tên model không hợp lệ"}
            gemini["model"] = model
        self._settings["gemini"] = gemini
        save_settings(self._settings)
        return self.get_gemini()

    # --- Lịch phim (TMDB) ---
    # --- đồng bộ kênh theo dõi (Supabase) ---
    def _sync_cfg(self) -> dict:
        return dict(self._settings.get("sync") or {})

    def get_sync(self) -> dict:
        s = self._sync_cfg()
        return {"available": bool(SUPABASE), "email": s.get("email", ""), "logged_in": bool(s.get("refresh")),
                "last_sync": s.get("last_sync", 0), "problem": s.get("error", "")}

    def sync_login(self, email, password) -> dict:
        email, password = str(email or "").strip(), str(password or "")
        if not SUPABASE:
            return {"error": "Bản app này chưa có đồng bộ"}
        if not (email and password):
            return {"error": "Nhập email và mật khẩu"}
        try:
            data = supabase_call(SUPABASE["url"], SUPABASE["key"], "/auth/v1/token?grant_type=password", "POST",
                                 {"email": email, "password": password})
        except urllib.error.HTTPError as e:
            return {"error": f"Đăng nhập thất bại: {supabase_error(e)}"}
        except (urllib.error.URLError, OSError, ValueError) as e:
            return {"error": f"Không kết nối được máy chủ đồng bộ: {e}"}
        user_id = str((data.get("user") or {}).get("id") or "")
        old = self._sync_cfg()
        # đổi sang tài khoản khác trên cùng máy: cất danh sách kênh của tài khoản cũ đi (đã có trên máy chủ),
        # để không gộp nhầm kênh của người trước vào tài khoản mới
        same = user_id == old["user_id"] if old.get("user_id") and user_id else             str(old.get("email", "")).lower() in ("", email.lower())
        if not same:
            with self._follow_lock:
                data_dir = self._data_dir()
                tag = re.sub(r"\W", "_", old.get("user_id") or old.get("email") or "cu")[:40]
                for path in [p for kind in SYNC_KINDS for p in (follows_file(data_dir, kind), deleted_file(data_dir, kind))]:
                    if path.exists():
                        path.replace(path.with_name(f"{path.stem}.{tag}.bak.json"))
            self._write(f"Đổi tài khoản: đã cất danh sách kênh của tài khoản trước ({old.get('email', '')}).")
        with self._sync_lock:
            self._settings["sync"] = {"email": email, "user_id": user_id, "refresh": encrypt_token(data["refresh_token"]),
                                      "last_sync": 0, "error": ""}
            self._sync_access = (data["access_token"], time.time() + int(data.get("expires_in", 3600)) - 120)
            save_settings(self._settings)
        self._write(f"Đã đăng nhập ({email}), đang đồng bộ kênh theo dõi...")
        self._sync()
        return self.get_sync()

    def sync_change_password(self, old, new) -> dict:
        """Đổi mật khẩu tài khoản đồng bộ: đăng nhập lại bằng mật khẩu cũ để xác nhận, rồi đặt mật khẩu mới."""
        old, new = str(old or ""), str(new or "")
        email = self._sync_cfg().get("email", "")
        if not (SUPABASE and email and self._sync_cfg().get("refresh")):
            return {"error": "Đăng nhập trước khi đổi mật khẩu"}
        if len(new) < 8:
            return {"error": "Mật khẩu mới cần ít nhất 8 ký tự"}
        if new == old:
            return {"error": "Mật khẩu mới phải khác mật khẩu cũ"}
        url, key = SUPABASE["url"], SUPABASE["key"]
        try:
            try:
                data = supabase_call(url, key, "/auth/v1/token?grant_type=password", "POST", {"email": email, "password": old})
            except urllib.error.HTTPError as e:
                msg = supabase_error(e)
                return {"error": "Mật khẩu hiện tại không đúng" if msg == "Sai email hoặc mật khẩu" else msg}
            supabase_call(url, key, "/auth/v1/user", "PUT", {"password": new}, data["access_token"])
        except urllib.error.HTTPError as e:
            return {"error": f"Không đổi được mật khẩu: {supabase_error(e)}"}
        except (urllib.error.URLError, OSError, ValueError) as e:
            return {"error": f"Không kết nối được máy chủ đồng bộ: {e}"}
        with self._sync_lock:
            s = self._sync_cfg()
            s["refresh"] = encrypt_token(data["refresh_token"])  # phiên mới từ lần xác nhận mật khẩu cũ
            self._settings["sync"] = s
            self._sync_access = (data["access_token"], time.time() + int(data.get("expires_in", 3600)) - 120)
            save_settings(self._settings)
        return {"ok": True}

    def sync_logout(self) -> dict:
        with self._sync_lock:
            s = self._sync_cfg()
            s.pop("refresh", None)
            s["error"] = ""
            self._settings["sync"] = s
            self._sync_access = None
            save_settings(self._settings)
        return self.get_sync()

    def sync_now(self) -> dict:
        self._sync()
        return self.get_sync()

    def _sync_soon(self) -> None:
        if SUPABASE and self._sync_cfg().get("refresh"):
            threading.Thread(target=self._sync, daemon=True).start()

    def _sync_token(self, s: dict) -> str:
        if self._sync_access and time.time() < self._sync_access[1]:
            return self._sync_access[0]
        data = supabase_call(SUPABASE["url"], SUPABASE["key"], "/auth/v1/token?grant_type=refresh_token", "POST",
                             {"refresh_token": decrypt_token(s["refresh"])})
        s["refresh"] = encrypt_token(data["refresh_token"])  # Supabase đổi refresh token mỗi lần dùng: phải lưu lại
        self._settings["sync"] = s
        save_settings(self._settings)
        self._sync_access = (data["access_token"], time.time() + int(data.get("expires_in", 3600)) - 120)
        return self._sync_access[0]

    def _sync(self) -> None:
        """Kéo kênh từ Supabase, ghép với máy này, đẩy phần mới hơn lên. Lỗi chỉ ghi lại, không làm hỏng việc khác."""
        if not (SUPABASE and self._sync_cfg().get("refresh")):
            return
        with self._sync_lock:
            s = self._sync_cfg()
            if not s.get("refresh"):
                return
            old_error = s.get("error", "")
            refreshing = not (self._sync_access and time.time() < self._sync_access[1])
            try:
                url, key = SUPABASE["url"], SUPABASE["key"]
                token = self._sync_token(s)
                refreshing = False
                for kind in SYNC_KINDS:
                    try:
                        rows = supabase_call(url, key, f"/rest/v1/{kind}?select=key,data,deleted,updated", token=token)
                    except urllib.error.HTTPError as e:
                        if kind == "follows" or e.code != 404:
                            raise
                        # máy chủ chưa có bảng blacklist (chủ app chưa chạy SQL mới): blacklist chỉ lưu trên máy này
                        if kind not in self._sync_missing:
                            self._sync_missing.add(kind)
                            self._write(f"[CẢNH BÁO] Máy chủ đồng bộ chưa có bảng {kind}: phần này chỉ lưu trên máy này.")
                        continue
                    with self._follow_lock:
                        data_dir = self._data_dir()
                        before = load_follows(data_dir, kind)
                        channels, gone, push = merge_follows(before, load_deleted(data_dir, kind), rows or [])
                        if kind == "follows":  # kênh đã vào blacklist (vd. ở máy khác) thì bỏ theo dõi
                            now = int(time.time() * 1000)
                            for k in load_follows(data_dir, "blacklist").keys() & channels.keys():
                                channels.pop(k)
                                gone[k] = now
                                push = [r for r in push if r["key"] != k] + [
                                    {"key": k, "data": {}, "deleted": True, "updated": now}]
                        write_follows(data_dir, channels, touch=False, kind=kind)
                        write_json(deleted_file(data_dir, kind), gone)
                    if push:
                        supabase_call(url, key, f"/rest/v1/{kind}?on_conflict=user_id,key", "POST", push, token,
                                      {"Prefer": "resolution=merge-duplicates,return=minimal"})
                    if channels != before:
                        self._lib_rev += 1
                s.update(last_sync=int(time.time()), error="")
            except urllib.error.HTTPError as e:
                msg = supabase_error(e)
                if refreshing and e.code in (400, 401):
                    s.pop("refresh", None)  # phiên đăng nhập Supabase không còn dùng được
                    msg = "Phiên đăng nhập đã hết, đăng nhập lại trong Cài đặt → Đồng bộ"
                s["error"] = msg
                self._write(f"[LỖI] Đồng bộ: {msg}")
            except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                s["error"] = f"Không kết nối được máy chủ đồng bộ: {e}"
            self._settings["sync"] = s
            save_settings(self._settings)
            if s.get("error", "") != old_error or not s.get("refresh"):
                self._lib_rev += 1  # giao diện cập nhật trạng thái đồng bộ

    def _tmdb_key(self) -> str:
        blob = (self._settings.get("tmdb") or {}).get("key")
        try:
            return decrypt_token(blob) if blob else ""
        except (OSError, ValueError):
            return ""

    def get_tmdb(self) -> dict:
        return {"has_key": bool(self._tmdb_key())}

    def set_tmdb(self, key=None, clear=False) -> dict:
        tmdb = dict(self._settings.get("tmdb") or {})
        if clear:
            tmdb.pop("key", None)
        elif key and str(key).strip():
            tmdb["key"] = encrypt_token(str(key).strip())
        self._settings["tmdb"] = tmdb
        save_settings(self._settings)
        MOVIES_FILE.unlink(missing_ok=True)  # đổi key thì lấy lại lịch từ đầu
        return self.get_tmdb()

    def movies(self, refresh=False) -> dict:
        """Lịch phim từ cache; cache cũ hơn MOVIES_MAX_AGE (hoặc bấm tải lại) thì lấy mới từ TMDB."""
        key = self._tmdb_key()
        result = {"has_key": bool(key), "items": [], "fetched_at": 0,
                  "streamers": {name: info["vn"] for name, info in STREAMERS.items()}}
        if not key:
            return result
        with self._movies_lock:
            try:
                cache = json.loads(MOVIES_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cache = {}
            if cache.get("v") != MOVIES_CACHE_VERSION:
                cache = {}  # cache của bản app cũ thiếu trường mới
            if refresh or time.time() - cache.get("fetched_at", 0) > MOVIES_MAX_AGE:
                try:
                    cache = {"v": MOVIES_CACHE_VERSION, "fetched_at": int(time.time()),
                             "items": fetch_movie_calendar(key, date.today())}
                    MOVIES_FILE.parent.mkdir(parents=True, exist_ok=True)
                    MOVIES_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
                except urllib.error.HTTPError as e:
                    result["error"] = tmdb_error(e)
                except (urllib.error.URLError, OSError, ValueError) as e:
                    result["error"] = f"Không tải được lịch phim từ TMDB: {e}"
        return {**result, "items": cache.get("items", []), "fetched_at": cache.get("fetched_at", 0)}

    def suggest_keywords(self, topic, count=12, platforms=None) -> dict:
        topic = clean_topic(topic)
        if not (topic["desc"] or topic["genres"]):
            return {"error": "Nhập mô tả chủ đề hoặc chọn ít nhất một thể loại"}
        self._settings["ai_topic"] = topic  # nhớ chủ đề cho lần sau
        save_settings(self._settings)
        gemini = self._settings.get("gemini") or {}
        if not self.get_gemini()["enabled"]:
            return {"error": "Gợi ý từ khóa bằng AI đang tắt (bật lại trong Cài đặt)"}
        if not gemini.get("key"):
            return {"error": "need_key"}
        try:
            api_key = decrypt_token(gemini["key"])
            count = min(max(int(count), 3), 30)
            platforms = [p for p in (platforms or []) if p in PLATFORM_CODES]
            return {"keywords": gemini_keywords(api_key, self.get_gemini()["model"], topic, count, platforms)}
        except urllib.error.HTTPError as e:
            return {"error": gemini_error(e)}
        except (urllib.error.URLError, OSError) as e:
            return {"error": f"Không kết nối được Gemini: {e}"}
        except ValueError as e:
            return {"error": str(e)}

    def check_reup(self, name) -> dict:
        """Tìm trên YouTube xem series đã có kênh Việt đăng lại chưa: theo tên Trung, và tên Việt nếu có Gemini."""
        name = " ".join(str(name or "").split())[:80]
        if not name:
            return {"error": "Series không có tên để tìm"}
        queries, note = [name], ""
        gemini = self._settings.get("gemini") or {}
        if self.get_gemini()["enabled"] and gemini.get("key"):
            try:
                queries += gemini_vi_titles(decrypt_token(gemini["key"]), self.get_gemini()["model"], name)
            except (urllib.error.URLError, OSError, ValueError):
                note = "Gemini chưa dịch được tên phim nên chỉ tìm theo tên Trung."
        else:
            note = "Chưa có Gemini nên chỉ tìm theo tên Trung. Kênh Việt thường đặt tên Việt, nên có thể sót."

        def search(query):
            try:
                return parse_youtube_results(youtube_page(query)), ""
            except (urllib.error.URLError, OSError, ValueError) as e:
                return [], f"{query}: {e}"
        with ThreadPoolExecutor(max_workers=4) as pool:
            pages = list(pool.map(search, queries))
        errors = [err for _, err in pages if err]
        if len(errors) == len(queries):
            return {"error": "Không tìm được trên YouTube. " + errors[0]}
        seen, results = set(), []
        for query, (found, _) in zip(queries, pages):
            for r in found[:15]:
                if r["id"] not in seen:
                    seen.add(r["id"])
                    results.append({**r, "query": query})
        results.sort(key=lambda r: not r["vi"])  # video tiêu đề tiếng Việt lên trước, giữ thứ tự YouTube
        return {"queries": queries, "results": results[:30], "vi": sum(r["vi"] for r in results),
                "note": note, "errors": errors}

    def poll(self, since=0) -> dict:
        # Đang crawl: cứ vài giây xem file kết quả có thêm video chưa để giao diện hiện dần
        if self._state == "crawling" and time.monotonic() - self._sig_checked > 3:
            self._sig_checked = time.monotonic()
            sig = results_signature(self._data_dir())
            if sig != self._sig:
                self._sig = sig
                self._lib_rev += 1
        with self._lock:
            start = max(int(since) - self._dropped, 0)
            lines = self._lines[start:]
            next_index = self._dropped + len(self._lines)
        return {
            "lines": lines, "next": next_index, "state": self._state,
            "running": [n for n in self._procs if n in PLATFORM_NAMES.values()],
            "base": self._settings.get("base_sha", "")[:7], "lib_rev": self._lib_rev,
            "app_update": {k: self._app_update[k] for k in ("version", "notes")} if self._app_update else None,
        }

    def check_update(self) -> dict:
        if self._state != "idle":
            return {"error": "App đang bận, đợi xong rồi thử lại."}
        self._state = "updating"
        threading.Thread(target=self._update_worker, daemon=True).start()
        return {"ok": True}

    def start(self, values) -> dict:
        if self._state != "idle":
            return {"error": "App đang bận, đợi xong rồi thử lại."}
        if not current_base(self._settings):
            return {"error": "Chưa có base code. Vào tab Cài đặt → Kiểm tra cập nhật."}
        for message in migrate_browser_data():  # phòng khi lần cập nhật trước chưa chuyển được hồ sơ
            self._write(message)
        try:
            login = self._login()
            values = {**(values or {}), "login": login["type"]}
            jobs = build_jobs(values, self._data_dir(), self._cookies() if login["type"] == "cookie" else None)
        except ValueError as e:
            return {"error": str(e)}
        self._settings["form"] = {k: v for k, v in values.items() if k in DEFAULT_FORM}
        self._settings["last_run"] = int(time.time())  # mốc để lọc "lần crawl gần nhất"
        save_settings(self._settings)
        Path(self._data_dir()).mkdir(parents=True, exist_ok=True)
        self._jobs, self._stop_event, self._state = jobs, threading.Event(), "crawling"
        self._write("─" * 60)
        if len(jobs) > 1:
            names = ", ".join(PLATFORM_NAMES[p] for p in jobs)
            self._write(f"Crawl song song {len(jobs)} nền tảng: {names}. Mỗi nền tảng mở một cửa sổ Chrome riêng, "
                        f"cách nhau {PARALLEL_STAGGER_SEC} giây.")
        for platform, args in jobs.items():
            self._write(f"Bắt đầu crawl {PLATFORM_NAMES[platform]}: " + " ".join(redact_args(args)))
        threading.Thread(target=self._crawl_worker, daemon=True).start()
        return {"ok": True}

    def stop(self) -> dict:
        self._stop_event.set()
        procs = list(self._procs.values())
        for proc in procs:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=NO_WINDOW)
        if procs:
            self._write("Đã dừng.")
        return {"ok": True}

    def library(self) -> dict:
        data_dir = self._data_dir()
        items = load_library(data_dir)
        saved = load_saved(data_dir)
        crawled = {i["key"]: i for i in items}
        # Video đã lưu: lấy số liệu mới nhất nếu vẫn còn trong kết quả crawl, không thì dùng bản đã lưu
        saved_items = [{**v, **crawled.get(k, {}), "key": k, "saved_at": v.get("saved_at", 0)} for k, v in saved.items()]
        for item in items:
            item["saved"] = item["key"] in saved
        for item in saved_items:
            item["saved"] = True
        link_series(items + saved_items, load_series_alias(data_dir))  # nối các tên của cùng một phim + gộp/tách tay
        self._library = {**{i["key"]: i for i in saved_items}, **crawled}
        series = series_summary(list(self._library.values()), int(time.time()))
        series_vi = resolve_series_vi(series, *read_series_vi(data_dir))
        missing = {s["name"]: s["sample"] for s in series.values()
                   if s["name"] and s["name"] not in series_vi and s["name"] not in self._vi_tried}
        if missing:
            self._translate_soon(dict(sorted(missing.items())))
        return {"items": items, "saved": saved_items, "last_run": self._settings.get("last_run", 0),
                "series": series, "series_vi": series_vi}

    def set_series_vi(self, name, vi) -> dict:
        """Người dùng sửa tên Việt của một series. Để trống = bỏ bản sửa tay, quay về bản Gemini dịch."""
        name, vi = str(name or "").strip(), " ".join(str(vi or "").split())[:80]
        if not name:
            return {"error": "Series không có tên"}
        data_dir = self._data_dir()
        manual = read_series_vi(data_dir)[1]
        if vi:
            manual[name] = vi
        else:
            manual.pop(name, None)
            self._vi_tried.discard(name)  # chưa có bản Gemini thì cho dịch lại
        save_series_vi(data_dir, manual=manual)
        self._lib_rev += 1
        return {"ok": True}

    def merge_series(self, key, target) -> dict:
        """Gộp tay series `key` vào series `target`. Lưu lại: video mới của series đó về sau cũng tự vào `target`."""
        key, target = str(key or ""), str(target or "")
        if not key or not target or key == target:
            return {"error": "Chọn series khác để gộp vào"}
        data_dir = self._data_dir()
        aliases = load_series_alias(data_dir)
        if aliases.get(target) not in (None, target):  # target đã gộp vào series khác: gộp thẳng vào đích cuối
            target = aliases[target]
        aliases[key] = target
        write_json(series_alias_file(data_dir), aliases)
        self._lib_rev += 1
        return {"ok": True}

    def split_series(self, key) -> dict:
        """Tách tay một tên gốc khỏi series nó đang bị gộp vào; giữ riêng luôn (không gộp tự động nữa)."""
        key = str(key or "")
        if not key:
            return {"error": "Không rõ series cần tách"}
        data_dir = self._data_dir()
        aliases = load_series_alias(data_dir)
        aliases[key] = key
        write_json(series_alias_file(data_dir), aliases)
        self._lib_rev += 1
        return {"ok": True}

    def _translate_soon(self, names: dict) -> None:
        """Dịch tên series chưa có bản dịch (chạy nền, cần Gemini). names = {tên: câu mô tả làm ngữ cảnh}.
        Mỗi tên chỉ thử một lần mỗi lần mở app (lỗi thì không gọi lại liên tục)."""
        gemini = self._settings.get("gemini") or {}
        if not (self.get_gemini()["enabled"] and gemini.get("key")) or self._vi_running:
            return
        self._vi_running = True
        self._vi_tried |= set(names)
        items = list(names.items())
        batches = [dict(items[i:i + SERIES_VI_BATCH]) for i in range(0, len(items), SERIES_VI_BATCH)]
        lock = threading.Lock()

        def one(key, model, batch):
            try:
                found = gemini_translate_names(key, model, batch)
            except urllib.error.HTTPError as e:
                return gemini_error(e)
            except (urllib.error.URLError, OSError, ValueError) as e:
                return str(e)
            if found:
                with lock:  # các lô xong cùng lúc: ghi lần lượt để không mất bản dịch của nhau
                    data_dir = self._data_dir()
                    save_series_vi(data_dir, auto={**read_series_vi(data_dir)[0], **found})
                self._lib_rev += 1  # giao diện tải lại để hiện tên đã dịch
            return ""

        def work():
            try:
                key, model = decrypt_token(gemini["key"]), self.get_gemini()["model"]
                with ThreadPoolExecutor(max_workers=SERIES_VI_PARALLEL) as pool:
                    errors = [e for e in pool.map(lambda b: one(key, model, b), batches) if e]
                if errors:
                    self._write(f"[CẢNH BÁO] Không dịch được {len(errors)}/{len(batches)} lô tên series: {errors[0]}")
            except (OSError, ValueError) as e:
                self._write(f"[CẢNH BÁO] Không dịch được tên series: {e}")
            finally:
                self._vi_running = False
        threading.Thread(target=work, daemon=True).start()

    def save_video(self, key) -> dict:
        item = self._library.get(key)
        if not item:
            return {"error": "Không tìm thấy video, hãy làm mới danh sách"}
        data_dir = self._data_dir()
        saved = load_saved(data_dir)
        saved[key] = {**{f: item.get(f) for f in ITEM_FIELDS}, "saved_at": int(time.time())}
        write_saved(data_dir, saved)
        threading.Thread(target=download_thumb, args=(item, data_dir), daemon=True).start()
        return {"ok": True, "saved_at": saved[key]["saved_at"]}

    def remove_video(self, key) -> dict:
        data_dir = self._data_dir()
        saved = load_saved(data_dir)
        if saved.pop(key, None) is not None:
            write_saved(data_dir, saved)
        return {"ok": True}

    def thumb(self, key) -> str:
        item = self._library.get(key)
        path = download_thumb(item, self._data_dir()) if item else None
        return thumb_data_uri(path) if path else ""

    def open_url(self, url) -> None:
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            os.startfile(url)

    def update_app(self) -> dict:
        """Nút "Cập nhật & khởi động lại": tải bản app mới, thay exe, mở lại."""
        if not self._app_update:
            return {"error": "Đang dùng bản mới nhất"}
        if not getattr(sys, "frozen", False):
            return {"error": "Chỉ tự cập nhật được khi chạy từ file CrawlTool.exe"}
        if self._state != "idle":
            return {"error": "App đang bận, đợi chạy xong rồi cập nhật."}
        self._state = "app_updating"
        threading.Thread(target=self._app_update_worker, daemon=True).start()
        return {"ok": True}

    def copy(self, text) -> None:
        set_clipboard(str(text))

    def read_clipboard(self) -> dict:
        try:
            return {"text": get_clipboard()}
        except OSError as e:
            return {"error": f"Không đọc được clipboard: {e}"}

    def copy_log(self) -> dict:
        """Copy toàn bộ nhật ký của phiên này (kể cả phần đã xóa khỏi màn hình) để gửi đi phân tích."""
        with self._lock:
            lines = list(self._lines)
        try:
            set_clipboard("\r\n".join(lines))  # CRLF: dán vào Notepad/Zalo/Messenger giữ đúng từng dòng
        except OSError as e:
            return {"error": f"Không copy được: {e}"}
        return {"ok": True, "lines": len(lines)}

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
    html = (root / "ui.html").read_text(encoding="utf-8")
    logo = root / "logo_64.png"  # build.ps1 tạo từ logo.png
    if logo.is_file():
        img = f'<img src="data:image/png;base64,{base64.b64encode(logo.read_bytes()).decode()}" alt="">'
        html = re.sub(r'<span class="logo">.*?</span>', f'<span class="logo img">{img}</span>', html, count=1)
    return html


def main() -> None:
    if self_install_if_needed():
        return
    if getattr(sys, "frozen", False):
        remove_old_exe(Path(sys.executable))
    import webview

    api = Api()
    window = webview.create_window(
        # text_select: cho bôi đen/copy chữ (mặc định pywebview chặn). Menu chuột phải mặc định của
        # WebView2 bị pywebview tắt khi không debug, nên ui.html tự làm menu Cắt/Copy/Dán.
        APP_NAME, html=ui_html(), js_api=api, width=1320, height=860, min_size=(1000, 640), text_select=True,
    )
    api._window = window
    window.events.closed += api._on_closed
    threading.Thread(target=api._scheduler, daemon=True).start()
    webview.start()


if __name__ == "__main__":
    main()
