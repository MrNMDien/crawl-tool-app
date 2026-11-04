"""Kiểm tra phần logic không cần giao diện. Chạy: python test_crawl_tool.py"""
import io
import json
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import crawl_tool as ct

BASE_FORM = {
    "platform": "dy", "crawl_type": "search", "targets": "AI短剧\nAI漫剧, AI微短剧\n\n",
    "login": "qrcode", "cookies": "", "max_notes": "30", "comments": False, "max_comments": 10,
    "media": False, "headless": False,
}


def test_build_args():
    args = ct.build_args(BASE_FORM, "D:/out")
    assert args[args.index("--platform") + 1] == "dy"
    assert args[args.index("--keywords") + 1] == "AI短剧,AI漫剧,AI微短剧"
    assert args[args.index("--crawler_max_notes_count") + 1] == "30"
    assert args[args.index("--save_data_option") + 1] == "jsonl"
    assert args[args.index("--save_data_path") + 1] == "D:/out"
    assert "--cookies" not in args

    detail = ct.build_args({**BASE_FORM, "crawl_type": "detail", "targets": "7525538910311632128"}, "x")
    assert "--specified_id" in detail and "--keywords" not in detail

    cookie = ct.build_args({**BASE_FORM, "login": "cookie", "cookies": " a=1; b=2 "}, "x")
    assert cookie[cookie.index("--cookies") + 1] == "a=1; b=2"
    assert "a=1; b=2" not in ct.redact_args(cookie) and "***" in ct.redact_args(cookie)

    for bad in (
        {**BASE_FORM, "targets": " \n "},
        {**BASE_FORM, "login": "cookie", "cookies": ""},
        {**BASE_FORM, "platform": "Douyin"},  # nhãn cũ của bản Tkinter, không phải mã
        {**BASE_FORM, "max_notes": "abc"},
    ):
        try:
            ct.build_args(bad, "x")
        except ValueError:
            continue
        raise AssertionError(f"phải báo lỗi: {bad}")


def test_build_jobs():
    multi = {**BASE_FORM, "platforms": ["dy", "xhs", "dy"]}
    jobs = ct.build_jobs(multi, "x")
    assert list(jobs) == ["dy", "xhs"]  # bỏ trùng, giữ thứ tự
    assert jobs["xhs"][jobs["xhs"].index("--platform") + 1] == "xhs"
    for bad in (
        {**BASE_FORM, "platforms": []},
        {**BASE_FORM, "platforms": ["dy", "nope"]},
        {**BASE_FORM, "platforms": ["dy", "xhs"], "crawl_type": "detail"},  # link/ID riêng từng nền tảng
        {**BASE_FORM, "platforms": ["dy", "xhs"], "login": "cookie", "cookies": "a=1"},
    ):
        try:
            ct.build_jobs(bad, "x")
        except ValueError:
            continue
        raise AssertionError(f"phải báo lỗi: {bad}")
    assert list(ct.build_jobs({**BASE_FORM, "platforms": ["ks"], "crawl_type": "detail"}, "x")) == ["ks"]


def test_parallel_crawl():
    """Chạy song song bằng một crawler giả: tên file 'run' được python chạy thay cho 'uv run'."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "run").write_text("\n".join([
            "import sys, time",
            "p = sys.argv[sys.argv.index('--platform') + 1]",
            "print('start', p, flush=True)",
            "time.sleep(30 if p == 'bili' else 1.5)",
            "print('end', p, flush=True)",
        ]), encoding="utf-8")
        old = ct.BASE_DIR, ct.uv_path, ct.PARALLEL_STAGGER_SEC, ct.save_settings
        ct.BASE_DIR, ct.uv_path, ct.PARALLEL_STAGGER_SEC = base, lambda: sys.executable, 0
        ct.save_settings = lambda settings: None  # không ghi đè settings.json thật
        try:
            api = ct.Api()
            api._settings = {"data_dir": str(base / "out")}
            began = time.time()
            assert api.start({**BASE_FORM, "platforms": ["dy", "xhs"]}) == {"ok": True}
            assert api.start({**BASE_FORM, "platforms": ["dy"]})["error"]  # đang bận
            while api._state != "idle" and time.time() - began < 20:
                time.sleep(0.1)
            log = "\n".join(api._lines)
            assert api._state == "idle" and time.time() - began < 3.5, "2 nền tảng phải chạy song song"
            assert "[Douyin] start dy" in log and "[Xiaohongshu] end xhs" in log
            assert "[Douyin] Hoàn tất." in log and "[Xiaohongshu] Hoàn tất." in log

            # Dừng: tắt cả tiến trình đang chạy dở
            assert api.start({**BASE_FORM, "platforms": ["bili", "dy"]}) == {"ok": True}
            while "Bilibili" not in api.poll()["running"]:
                time.sleep(0.1)
            api.stop()
            stopped = time.time()
            while api._state != "idle" and time.time() - stopped < 10:
                time.sleep(0.1)
            assert api._state == "idle" and "end bili" not in "\n".join(api._lines)
        finally:
            ct.BASE_DIR, ct.uv_path, ct.PARALLEL_STAGGER_SEC, ct.save_settings = old


def test_old_form_migrates():
    api = ct.Api()
    api._settings = {"form": {"platform": "dy", "crawl_type": "search"}}
    assert api.init()["form"]["platforms"] == ["dy"]
    api._settings = {"form": {"platforms": ["Douyin"]}}  # nhãn của bản Tkinter
    assert api.init()["form"]["platforms"] == ["xhs"]


def test_parse():
    assert ct.parse_count("1.2万") == 12000
    assert ct.parse_count("10万+") == 100000
    assert ct.parse_count("3,456") == 3456
    assert ct.parse_count(None) == 0 and ct.parse_count("abc") == 0
    assert ct.parse_time(1730000000000) == 1730000000  # mili-giây
    assert ct.parse_time("1730000000") == 1730000000
    assert ct.parse_time(None) == 0


def test_normalize_and_library():
    with tempfile.TemporaryDirectory() as tmp:
        dy_dir = Path(tmp) / "douyin" / "jsonl"
        dy_dir.mkdir(parents=True)
        rows = [
            {"aweme_id": "111", "title": "Phim AI tập 1", "aweme_url": "https://www.douyin.com/video/111",
             "cover_url": "https://p3.douyinpic.com/a.jpg", "liked_count": "5000", "comment_count": "20",
             "create_time": 1730000000, "last_modify_ts": 1, "source_keyword": "AI短剧"},
            # cùng video crawl lại lần sau: lượt like mới phải thắng
            {"aweme_id": "111", "title": "Phim AI tập 1", "aweme_url": "https://www.douyin.com/video/111",
             "cover_url": "https://p3.douyinpic.com/a.jpg", "liked_count": "9000", "last_modify_ts": 2},
            {"aweme_id": "222", "aweme_url": "javascript:alert(1)", "cover_url": "http://x/b.jpg", "last_modify_ts": 1},
        ]
        (dy_dir / "search_contents_2026-11-04.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\nhỏng\n", encoding="utf-8")
        xhs_dir = Path(tmp) / "xhs" / "jsonl"
        xhs_dir.mkdir(parents=True)
        (xhs_dir / "search_contents_2026-11-04.jsonl").write_text(json.dumps({
            "note_id": "abc", "title": "", "desc": "Mô tả", "note_url": "https://www.xiaohongshu.com/explore/abc",
            "image_list": "https://sns-webpic.xhscdn.com/1.jpg,https://sns-webpic.xhscdn.com/2.jpg",
            "liked_count": "1.2万", "time": 1730000000000, "last_modify_ts": 1,
        }, ensure_ascii=False), encoding="utf-8")

        items = {i["key"]: i for i in ct.load_library(tmp)}
        assert set(items) == {"dy_111", "dy_222", "xhs_abc"}
        assert items["dy_111"]["likes"] == 9000
        assert items["dy_222"]["url"] == ""  # link lạ bị loại, không cho giao diện mở
        assert items["dy_222"]["cover"] == "https://x/b.jpg"  # http được nâng lên https
        assert items["xhs_abc"]["cover"] == "https://sns-webpic.xhscdn.com/1.jpg"
        assert items["xhs_abc"]["likes"] == 12000 and items["xhs_abc"]["title"] == "Mô tả"
        assert items["xhs_abc"]["published"] == 1730000000


def test_thumb_cache():
    with tempfile.TemporaryDirectory() as tmp:
        path = ct.thumb_file(tmp, "dy_111")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 10)
        # đã có file thì không tải lại (không gọi mạng)
        assert ct.download_thumb({"key": "dy_111", "platform": "dy", "cover": "https://invalid.example/a.jpg"}, tmp) == path
        assert ct.thumb_data_uri(path).startswith("data:image/jpeg;base64,")
        assert ct.download_thumb({"key": "dy_x", "platform": "dy", "cover": ""}, tmp) is None
        assert ct.thumb_file(tmp, "../../evil").name == "evil.img"
    assert ct.image_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert ct.image_mime(b"<html>") == ""


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
    test_build_jobs()
    test_parallel_crawl()
    test_old_form_migrates()
    test_parse()
    test_normalize_and_library()
    test_thumb_cache()
    test_token_roundtrip()
    test_extract_and_swap()
    print("OK")
