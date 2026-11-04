"""Kiểm tra phần logic không cần giao diện. Chạy: python test_crawl_tool.py"""
import io
import tempfile
import zipfile
from pathlib import Path

import crawl_tool as ct

BASE_FORM = {
    "platform": "Douyin", "crawl_type": "Tìm theo từ khóa", "targets": "AI短剧\nAI漫剧, AI微短剧\n\n",
    "login": "Quét mã QR", "cookies": "", "max_notes": 30, "comments": False, "max_comments": 10,
    "media": False, "save_format": "excel", "headless": False,
}


def test_build_args():
    args = ct.build_args(BASE_FORM, "D:/out")
    assert args[args.index("--platform") + 1] == "dy"
    assert args[args.index("--keywords") + 1] == "AI短剧,AI漫剧,AI微短剧"
    assert args[args.index("--get_comment") + 1] == "false"
    assert args[args.index("--save_data_path") + 1] == "D:/out"
    assert "--cookies" not in args

    detail = ct.build_args({**BASE_FORM, "crawl_type": "Bài/video chỉ định", "targets": "7525538910311632128"}, "x")
    assert "--specified_id" in detail and "--keywords" not in detail

    cookie = ct.build_args({**BASE_FORM, "login": "Cookie", "cookies": " a=1; b=2 "}, "x")
    assert cookie[cookie.index("--cookies") + 1] == "a=1; b=2"

    for bad in ({**BASE_FORM, "targets": " \n "}, {**BASE_FORM, "login": "Cookie", "cookies": ""}):
        try:
            ct.build_args(bad, "x")
        except ValueError:
            continue
        raise AssertionError("phải báo lỗi khi thiếu dữ liệu")


def test_token_roundtrip():
    blob = ct.encrypt_token("github_pat_test")
    assert "github_pat_test" not in blob
    assert ct.decrypt_token(blob) == "github_pat_test"


def test_extract_and_swap():
    with tempfile.TemporaryDirectory() as tmp:
        ct.APP_HOME = Path(tmp)
        ct.BASE_DIR = Path(tmp) / "base"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("MrNMDien-MediaCrawler-abc1234/main.py", "print('v2')")
        # base cũ có phiên đăng nhập cần giữ lại
        (ct.BASE_DIR / "browser_data").mkdir(parents=True)
        (ct.BASE_DIR / "browser_data" / "cookie").write_text("keep")
        (ct.BASE_DIR / "main.py").write_text("print('v1')")

        new_dir = Path(tmp) / "base_new"
        ct.extract_zipball(buf.getvalue(), new_dir)
        ct.swap_base(new_dir)

        assert (ct.BASE_DIR / "main.py").read_text() == "print('v2')"
        assert (ct.BASE_DIR / "browser_data" / "cookie").read_text() == "keep"
        assert not new_dir.exists() and not (Path(tmp) / "base_old").exists()


if __name__ == "__main__":
    test_build_args()
    test_token_roundtrip()
    test_extract_and_swap()
    print("OK")
