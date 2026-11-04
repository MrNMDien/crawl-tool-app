"""Crawl Tool: app Tkinter điều khiển MediaCrawler.

Base code (MediaCrawler) không đóng gói trong app. App tự tải bản mới nhất từ repo
GitHub riêng tư về %LOCALAPPDATA%\\CrawlTool\\base, cài thư viện bằng uv rồi chạy
main.py qua dòng lệnh. Đẩy code mới lên repo là app tự nhận ở lần mở sau.
"""
import base64
import ctypes
import io
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import urllib.error
import urllib.request
import zipfile
from ctypes import wintypes
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

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

PLATFORMS = {
    "Douyin": "dy", "Xiaohongshu": "xhs", "Kuaishou": "ks", "Bilibili": "bili",
    "Weibo": "wb", "Baidu Tieba": "tieba", "Zhihu": "zhihu",
}
CRAWL_TYPES = {
    "Tìm theo từ khóa": "search", "Bài/video chỉ định": "detail", "Trang creator": "creator",
}
LOGIN_TYPES = {"Quét mã QR": "qrcode", "Số điện thoại": "phone", "Cookie": "cookie"}
SAVE_FORMATS = ["excel", "csv", "json", "jsonl", "sqlite"]
TARGET_FLAG = {"search": "--keywords", "detail": "--specified_id", "creator": "--creator_id"}


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
    crawl_type = CRAWL_TYPES[values["crawl_type"]]
    targets = [t.strip() for t in values["targets"].replace(",", "\n").splitlines() if t.strip()]
    if not targets:
        raise ValueError("Chưa nhập từ khóa hoặc ID/URL")
    login = LOGIN_TYPES[values["login"]]
    args = [
        "--platform", PLATFORMS[values["platform"]],
        "--type", crawl_type,
        TARGET_FLAG[crawl_type], ",".join(targets),
        "--lt", login,
        "--crawler_max_notes_count", str(int(values["max_notes"])),
        "--get_comment", "true" if values["comments"] else "false",
        "--max_comments_count_singlenotes", str(int(values["max_comments"])),
        "--get_media", "true" if values["media"] else "false",
        "--save_data_option", values["save_format"],
        "--save_data_path", data_dir,
        "--headless", "true" if values["headless"] else "false",
    ]
    if login == "cookie":
        if not values["cookies"].strip():
            raise ValueError("Đăng nhập bằng cookie nhưng chưa dán cookie")
        args += ["--cookies", values["cookies"].strip()]
    return args


def find_chrome() -> bool:
    candidates = [
        Path(os.environ.get(var, "")) / "Google/Chrome/Application/chrome.exe"
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
    ]
    return any(p.is_file() for p in candidates)


# ---------- Tự cài đặt / gỡ cài đặt (khi chạy từ file .exe) ----------

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
    if not messagebox.askyesno(APP_NAME, f"Cài {APP_NAME} vào máy?\n\nThư mục: {INSTALL_DIR}\nTạo shortcut ở Desktop và Start Menu."):
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


# ---------- Giao diện ----------

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = load_settings()
        self.proc = None
        self.busy = False
        self.log_queue = queue.Queue()

        root.title(APP_NAME)
        root.geometry("900x680")
        root.minsize(760, 560)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        menu = tk.Menu(root)
        tools = tk.Menu(menu, tearoff=False)
        tools.add_command(label="Kiểm tra cập nhật base code", command=self.check_update)
        tools.add_command(label="Đổi GitHub token", command=self.ask_token)
        tools.add_command(label="Đổi thư mục lưu kết quả", command=self.choose_data_dir)
        tools.add_separator()
        tools.add_command(label="Gỡ cài đặt", command=self.on_uninstall)
        menu.add_cascade(label="Công cụ", menu=tools)
        root.config(menu=menu)

        form = ttk.Frame(root, padding=10)
        form.pack(fill="x")
        saved = self.settings.get("form", {})
        self.vars = {
            "platform": tk.StringVar(value=saved.get("platform", "Douyin")),
            "crawl_type": tk.StringVar(value=saved.get("crawl_type", "Tìm theo từ khóa")),
            "login": tk.StringVar(value=saved.get("login", "Quét mã QR")),
            "max_notes": tk.IntVar(value=saved.get("max_notes", 20)),
            "comments": tk.BooleanVar(value=saved.get("comments", False)),
            "max_comments": tk.IntVar(value=saved.get("max_comments", 10)),
            "media": tk.BooleanVar(value=saved.get("media", False)),
            "save_format": tk.StringVar(value=saved.get("save_format", "excel")),
            "headless": tk.BooleanVar(value=saved.get("headless", False)),
        }

        def row(r, label, widget):
            ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", pady=3)
            widget.grid(row=r, column=1, sticky="we", pady=3)

        form.columnconfigure(1, weight=1)
        row(0, "Nền tảng", ttk.Combobox(form, textvariable=self.vars["platform"], values=list(PLATFORMS), state="readonly"))
        row(1, "Kiểu crawl", ttk.Combobox(form, textvariable=self.vars["crawl_type"], values=list(CRAWL_TYPES), state="readonly"))
        self.targets = tk.Text(form, height=4, wrap="word")
        self.targets.insert("1.0", saved.get("targets", "AI短剧\nAI漫剧"))
        row(2, "Từ khóa / ID / URL\n(mỗi dòng một cái)", self.targets)
        row(3, "Đăng nhập", ttk.Combobox(form, textvariable=self.vars["login"], values=list(LOGIN_TYPES), state="readonly"))
        self.cookies = ttk.Entry(form, show="•")
        row(4, "Cookie (nếu chọn Cookie)", self.cookies)

        opts = ttk.Frame(form)
        ttk.Label(opts, text="Số bài tối đa").pack(side="left")
        ttk.Spinbox(opts, from_=1, to=1000, width=6, textvariable=self.vars["max_notes"]).pack(side="left", padx=(4, 14))
        ttk.Checkbutton(opts, text="Lấy bình luận, tối đa", variable=self.vars["comments"]).pack(side="left")
        ttk.Spinbox(opts, from_=1, to=1000, width=6, textvariable=self.vars["max_comments"]).pack(side="left", padx=(4, 14))
        ttk.Checkbutton(opts, text="Tải video/ảnh", variable=self.vars["media"]).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(opts, text="Chạy ẩn trình duyệt", variable=self.vars["headless"]).pack(side="left")
        row(5, "Tùy chọn", opts)
        row(6, "Định dạng lưu", ttk.Combobox(form, textvariable=self.vars["save_format"], values=SAVE_FORMATS, state="readonly", width=10))

        buttons = ttk.Frame(root, padding=(10, 0))
        buttons.pack(fill="x")
        self.start_btn = ttk.Button(buttons, text="▶ Bắt đầu", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(buttons, text="■ Dừng", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(buttons, text="📂 Mở thư mục kết quả", command=self.open_data_dir).pack(side="left")
        self.status = ttk.Label(buttons, text="")
        self.status.pack(side="right")

        self.log = scrolledtext.ScrolledText(root, height=20, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=10, pady=10)

        self.root.after(100, self.drain_log)
        self.write("Lưu ý: crawl Douyin/Xiaohongshu cần mở Chrome và bật chrome://inspect/#remote-debugging "
                   "(tick 'Allow remote debugging for this browser instance').")
        if not find_chrome():
            self.write("[CẢNH BÁO] Không thấy Google Chrome. Hãy cài Chrome bản mới nhất.")
        if not shutil.which("node"):
            self.write("[CẢNH BÁO] Không thấy Node.js. Douyin và Zhihu cần Node.js để tính chữ ký: https://nodejs.org")
        self.refresh_status()
        self.check_update()

    # --- tiện ích ---
    @property
    def data_dir(self) -> str:
        return self.settings.get("data_dir", str(DEFAULT_DATA_DIR))

    def write(self, line: str) -> None:
        self.log_queue.put(line)

    def drain_log(self) -> None:
        lines = []
        while not self.log_queue.empty():
            lines.append(self.log_queue.get_nowait())
        if lines:
            self.log.configure(state="normal")
            self.log.insert("end", "\n".join(lines) + "\n")
            if int(self.log.index("end").split(".")[0]) > 5000:
                self.log.delete("1.0", "1000.0")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.root.after(100, self.drain_log)

    def refresh_status(self) -> None:
        sha = self.settings.get("base_sha", "")
        self.status.config(text=f"Base: {sha[:7]}" if sha else "Base: chưa cài")

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.root.after(0, lambda: (
            self.start_btn.config(state="disabled" if busy else "normal"),
            self.stop_btn.config(state="normal" if self.proc else "disabled"),
        ))

    def form_values(self) -> dict:
        values = {k: v.get() for k, v in self.vars.items()}
        values["targets"] = self.targets.get("1.0", "end").strip()
        values["cookies"] = self.cookies.get()
        return values

    def run_logged(self, cmd: list, cwd: Path) -> int:
        self.proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=uv_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
        )
        self.set_busy(True)
        for raw in self.proc.stdout:
            self.write(raw.decode("utf-8", errors="replace").rstrip())
        code = self.proc.wait()
        self.proc = None
        return code

    # --- token & thư mục ---
    def token(self) -> str:
        blob = self.settings.get("token")
        if blob:
            try:
                return decrypt_token(blob)
            except OSError:
                self.write("[LỖI] Không giải mã được token đã lưu, hãy nhập lại.")
        return self.ask_token()

    def ask_token(self) -> str:
        token = simpledialog.askstring(
            "GitHub token",
            f"Nhập GitHub token để tải base code từ repo riêng tư {BASE_REPO}.\n\n"
            "Tạo ở GitHub → Settings → Developer settings → Fine-grained tokens:\n"
            f"chỉ chọn repo {BASE_REPO.split('/')[1]}, quyền Contents: Read-only.",
            show="•", parent=self.root,
        )
        if token:
            self.settings["token"] = encrypt_token(token.strip())
            save_settings(self.settings)
            return token.strip()
        return ""

    def choose_data_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.data_dir, parent=self.root)
        if chosen:
            self.settings["data_dir"] = chosen
            save_settings(self.settings)
            self.write(f"Kết quả sẽ lưu vào: {chosen}")

    def open_data_dir(self) -> None:
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        os.startfile(self.data_dir)

    # --- cập nhật base ---
    def check_update(self) -> None:
        if self.busy:
            return
        token = self.token()
        if not token:
            self.write("[CẢNH BÁO] Chưa có GitHub token nên chưa tải được base code (menu Công cụ → Đổi GitHub token).")
            return
        self.set_busy(True)
        threading.Thread(target=self._update_worker, args=(token,), daemon=True).start()

    def _update_worker(self, token: str) -> None:
        try:
            self.write("Đang kiểm tra bản mới của base code...")
            sha = latest_sha(token)
            if sha == self.settings.get("base_sha") and BASE_DIR.exists():
                self.write(f"Base code đã là bản mới nhất ({sha[:7]}).")
                return
            self.write(f"Đang tải base code {sha[:7]}...")
            zip_bytes = github_get(f"https://api.github.com/repos/{BASE_REPO}/zipball/{sha}", token)
            new_dir = APP_HOME / "base_new"
            shutil.rmtree(new_dir, ignore_errors=True)
            extract_zipball(zip_bytes, new_dir)
            self.write("Đang cài thư viện (lần đầu sẽ lâu vì phải tải Python và thư viện)...")
            if self.run_logged([uv_path(), "sync"], new_dir) != 0:
                raise RuntimeError("uv sync thất bại, xem log ở trên")
            swap_base(new_dir)
            self.settings["base_sha"] = sha
            save_settings(self.settings)
            self.write(f"Đã cập nhật base code lên {sha[:7]}.")
        except urllib.error.HTTPError as e:
            hint = " (token sai, hết hạn hoặc không có quyền vào repo)" if e.code in (401, 403, 404) else ""
            self.write(f"[LỖI] GitHub trả về {e.code}{hint}")
        except Exception as e:  # noqa: BLE001 - lỗi nào cũng phải hiện cho người dùng thấy
            self.write(f"[LỖI] Cập nhật thất bại: {e}")
        finally:
            self.set_busy(False)
            self.root.after(0, self.refresh_status)

    # --- chạy crawler ---
    def start(self) -> None:
        if not BASE_DIR.exists():
            messagebox.showwarning(APP_NAME, "Chưa có base code. Vào menu Công cụ → Kiểm tra cập nhật.")
            return
        values = self.form_values()
        try:
            args = build_args(values, self.data_dir)
        except ValueError as e:
            messagebox.showwarning(APP_NAME, str(e))
            return
        self.settings["form"] = {k: v for k, v in values.items() if k != "cookies"}
        save_settings(self.settings)
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        self.write("─" * 60)
        self.write("Bắt đầu crawl: " + " ".join(a for a in args if not a.startswith("--cookies")))
        threading.Thread(target=self._crawl_worker, args=(args,), daemon=True).start()

    def _crawl_worker(self, args: list) -> None:
        try:
            code = self.run_logged([uv_path(), "run", "--no-sync", "python", "main.py", *args], BASE_DIR)
            self.write("Hoàn tất." if code == 0 else f"[LỖI] Crawler dừng với mã {code}.")
        except Exception as e:  # noqa: BLE001
            self.write(f"[LỖI] Không chạy được crawler: {e}")
        finally:
            self.set_busy(False)

    def stop(self) -> None:
        if self.proc:
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                           capture_output=True, creationflags=NO_WINDOW)
            self.write("Đã dừng.")

    def on_uninstall(self) -> None:
        if messagebox.askyesno(APP_NAME, f"Gỡ {APP_NAME}?\n\nXóa app, base code và token đã lưu.\n"
                                         f"Thư mục kết quả ({self.data_dir}) được giữ lại."):
            self.stop()
            uninstall()
            self.root.destroy()

    def on_close(self) -> None:
        if self.proc and not messagebox.askyesno(APP_NAME, "Crawler đang chạy. Dừng và thoát?"):
            return
        self.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    if self_install_if_needed():
        root.destroy()
        return
    root.deiconify()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
