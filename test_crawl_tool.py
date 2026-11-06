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
        {**BASE_FORM, "platforms": ["dy", "xhs"], "login": "cookie"},  # thiếu cookie của xhs
    ):
        try:
            ct.build_jobs(bad, "x", cookies={"dy": "a=1"})
        except ValueError:
            continue
        raise AssertionError(f"phải báo lỗi: {bad}")
    assert list(ct.build_jobs({**BASE_FORM, "platforms": ["ks"], "crawl_type": "detail"}, "x")) == ["ks"]

    # đăng nhập bằng cookie: mỗi nền tảng dùng cookie của riêng nó
    jobs = ct.build_jobs({**BASE_FORM, "platforms": ["dy", "xhs"], "login": "cookie"}, "x", cookies={"dy": "a=1", "xhs": "b=2"})
    assert jobs["dy"][jobs["dy"].index("--cookies") + 1] == "a=1"
    assert jobs["xhs"][jobs["xhs"].index("--cookies") + 1] == "b=2"


def test_login_settings():
    old_save = ct.save_settings
    ct.save_settings = lambda settings: None
    try:
        api = ct.Api()
        api._settings = {"form": {"login": "cookie", "platforms": ["dy"]}}
        api.init()  # cách đăng nhập cũ nằm trong form -> chuyển sang Cài đặt
        assert {k: api.get_login()[k] for k in ("type", "saved")} == {"type": "cookie", "saved": []}
        assert "login" not in api.init()["form"]
        assert api.set_login_type("phone")["error"] and api.set_cookie("nope", "x")["error"]
        assert api.set_cookie("dy", " sid=1; uid=2 ")["saved"] == ["dy"]
        assert "sid=1" not in json.dumps(api._settings)  # cookie lưu mã hóa
        assert api._cookies() == {"dy": "sid=1; uid=2"}
        assert api.set_cookie("dy", "")["saved"] == []
        assert api.set_login_type("qrcode")["type"] == "qrcode"

        assert api.get_gemini() == {"has_key": False, "model": ct.GEMINI_DEFAULT_MODEL, "enabled": True}
        # tắt AI: không gọi Gemini, kể cả khi đã có key
        assert api.set_gemini("AIza-test", None, False, False)["enabled"] is False
        assert "tắt" in api.suggest_keywords("phim AI")["error"]
        assert api.set_gemini(None, None, False, True)["enabled"] is True and api.get_gemini()["has_key"]
        api.set_gemini(None, None, True)
        assert api.set_gemini("AIza-test", "gemini-2.5-flash")["has_key"] is True
        assert "AIza-test" not in json.dumps(api._settings)
        assert api.set_gemini(None, "../../evil")["error"]
        assert api.set_gemini("", "")["model"] == ct.GEMINI_DEFAULT_MODEL and api.get_gemini()["has_key"]  # key trống: giữ key cũ
        assert api.set_gemini(None, None, True)["has_key"] is False
        assert api.suggest_keywords("phim AI")["error"] == "need_key"
    finally:
        ct.save_settings = old_save


def test_gemini_keywords():
    kw = lambda k, m="": {"keyword": k, "meaning": m}
    assert ct.clean_keywords(["AI短剧", " AI短剧 ", "AI, 漫剧", "a\nb", 5, "x" * 41, ""], 10) == [kw("AI短剧"), kw("AI 漫剧"), kw("a b")]
    assert ct.clean_keywords([{"keyword": "古风漫剧", "meaning_vi": " truyện tranh\ncổ phong "}, {"meaning_vi": "thiếu từ"}], 5) == [
        kw("古风漫剧", "truyện tranh cổ phong")]
    assert ct.clean_keywords("not a list", 5) == []
    assert [k["keyword"] for k in ct.clean_keywords([str(i) for i in range(50)], 3)] == ["0", "1", "2"]

    topic = ct.clean_topic({"desc": " phim AI ", "genres": ["Cổ trang (古装)", "", 7], "styles": "nope", "audience": "Nữ (女频)",
                            "exclude": "x" * 500, "evil": "ignored"})
    assert topic == {"desc": "phim AI", "include": "", "exclude": "x" * 200, "audience": "Nữ (女频)",
                     "genres": ["Cổ trang (古装)", "7"], "styles": []}
    assert ct.clean_topic("chỉ một câu")["desc"] == "chỉ một câu"  # bản cũ gửi chuỗi

    sent = {}

    class FakeResp:
        def __init__(self, payload):
            self.payload = payload
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps(self.payload).encode()

    def fake_urlopen(req, timeout):
        sent["url"], sent["key"] = req.full_url, req.get_header("X-goog-api-key")
        sent["body"] = json.loads(req.data)
        text = json.dumps([{"keyword": "AI短剧推荐", "meaning_vi": "phim ngắn AI đề cử"},
                           {"keyword": "AI漫剧", "meaning_vi": "truyện tranh AI"}, {"keyword": "AI漫剧", "meaning_vi": "trùng"}])
        return FakeResp({"candidates": [{"content": {"parts": [{"text": text}]}}]})

    old = ct.urllib.request.urlopen
    ct.urllib.request.urlopen = fake_urlopen
    try:
        topic = {"desc": "phim bộ AI cổ trang", "genres": ["Trọng sinh (重生穿越)"], "styles": ["Anime 2D (2D动漫)"],
                 "audience": "Nữ (女频)", "include": "全集", "exclude": "phim người đóng"}
        kws = ct.gemini_keywords("KEY", "gemini-flash-latest", topic, 5, ["dy", "xhs"])
        assert kws == [kw("AI短剧推荐", "phim ngắn AI đề cử"), kw("AI漫剧", "truyện tranh AI")]
        assert sent["url"].endswith("/models/gemini-flash-latest:generateContent") and sent["key"] == "KEY"
        prompt = sent["body"]["contents"][0]["parts"][0]["text"]
        for part in ("phim bộ AI cổ trang", "Douyin, Xiaohongshu", "Trọng sinh (重生穿越)", "Anime 2D (2D动漫)",
                     "Nữ (女频)", "全集", "Loại trừ", "phim người đóng", "meaning_vi"):
            assert part in prompt, part
        assert sent["body"]["generationConfig"]["responseSchema"]["items"]["type"] == "OBJECT"

        # Api: chủ đề trống bị chặn, chủ đề hợp lệ được nhớ cho lần sau
        old_save = ct.save_settings
        ct.save_settings = lambda settings: None
        try:
            api = ct.Api()
            api._settings = {"gemini": {"key": ct.encrypt_token("KEY")}}
            assert "Nhập mô tả" in api.suggest_keywords({"desc": " ", "genres": []})["error"]
            res = api.suggest_keywords(topic, 2, ["dy", "evil"])
            assert [k["keyword"] for k in res["keywords"]] == ["AI短剧推荐", "AI漫剧"]
            assert api.init()["ai_topic"]["desc"] == "phim bộ AI cổ trang"
        finally:
            ct.save_settings = old_save
        try:
            ct.gemini_keywords("KEY", "../x", "t", 5, [])
        except ValueError:
            pass
        else:
            raise AssertionError("model lạ phải bị chặn")
    finally:
        ct.urllib.request.urlopen = old


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


def test_saved_library_and_live_refresh():
    with tempfile.TemporaryDirectory() as tmp:
        jsonl = Path(tmp) / "douyin" / "jsonl" / "search_contents_2026-11-04.jsonl"
        jsonl.parent.mkdir(parents=True)
        row = {"aweme_id": "1", "title": "A", "aweme_url": "https://www.douyin.com/video/1", "liked_count": "10", "last_modify_ts": 1}
        jsonl.write_text(json.dumps(row) + "\n", encoding="utf-8")

        old_save = ct.save_settings
        ct.save_settings = lambda settings: None
        try:
            api = ct.Api()
            api._settings = {"data_dir": tmp, "last_run": 123}
            data = api.library()
            assert data["last_run"] == 123 and data["saved"] == [] and data["items"][0]["saved"] is False

            assert api.save_video("dy_1")["ok"] and api.save_video("nope").get("error")
            # crawl lại: số like mới phải hiện cả trong thư viện
            jsonl.write_text(json.dumps({**row, "liked_count": "99", "last_modify_ts": 2}) + "\n", encoding="utf-8")
            data = api.library()
            assert data["items"][0]["saved"] is True
            assert [(i["key"], i["likes"], i["saved"]) for i in data["saved"]] == [("dy_1", 99, True)]

            # file kết quả bị xoá: video vẫn còn trong thư viện (dùng bản đã lưu)
            jsonl.unlink()
            data = api.library()
            assert data["items"] == [] and data["saved"][0]["key"] == "dy_1"

            assert api.remove_video("dy_1") == {"ok": True}
            assert api.library()["saved"] == [] and ct.load_saved(tmp) == {}

            # đang crawl: file kết quả đổi -> lib_rev tăng để giao diện tự tải lại
            api._state = "crawling"
            rev = api.poll()["lib_rev"]
            jsonl.write_text(json.dumps(row) + "\n", encoding="utf-8")
            api._sig_checked = 0
            assert api.poll()["lib_rev"] == rev + 1
            api._sig_checked = 0
            assert api.poll()["lib_rev"] == rev + 1  # không đổi gì thì không tải lại
        finally:
            ct.save_settings = old_save


def test_follow_logic():
    sec = "MS4wLjABAAAATJPY7LAlaa5X-c8uNdWkvz0jUGgpw4eeXIwu_8BhvqE"
    url = f"https://www.douyin.com/user/{sec}"
    assert ct.parse_channel(url + "?from_tab_name=main") == ("dy", url, sec)
    assert ct.parse_channel(sec) == ("dy", url, sec)
    share = "长按复制此条消息，打开抖音搜索，查看TA的更多作品。 https://v.douyin.com/iABC123/"
    assert ct.parse_channel(share, resolve=lambda u: f"https://www.iesdouyin.com/share/user/{sec}?u_code=1") == ("dy", url, sec)
    for bad, resolver in (("https://v.douyin.com/x/", lambda u: "https://www.iesdouyin.com/share/video/1/"),
                          ("https://www.youtube.com/@abc", None), ("", None)):
        try:
            ct.parse_channel(bad, resolve=resolver or (lambda u: ""))
        except ValueError:
            continue
        raise AssertionError(f"phải báo lỗi: {bad}")

    v = lambda i: {"key": f"dy_{i}", "url": f"https://www.douyin.com/video/{i}", "title": f"Tập {i} <b>", "published": i}
    channels = {"dy_a": {"url": "A", "name": "Kênh <A>"}, "dy_b": {"url": "B", "name": "Kênh B"}}
    # lần đầu: chỉ ghi nhận, không báo
    assert ct.apply_check(channels, {"A": {"1": v(1), "2": v(2)}, "B": {}}, now=100) == {}
    assert sorted(channels["dy_a"]["known"]) == ["1", "2"] and channels["dy_b"]["checked_at"] == 100
    # lần sau: video 3 mới ở kênh A; kênh B lần này lỗi (không có trong kết quả) thì giữ nguyên
    found = ct.apply_check(channels, {"A": {"2": v(2), "3": v(3)}}, now=200)
    assert [i["key"] for i in found["dy_a"]] == ["dy_3"] and "dy_b" not in found
    assert channels["dy_a"]["new"] == ["3"] and channels["dy_b"]["checked_at"] == 100
    # kênh đã kiểm tra mà trước đó chưa có video nào: video đầu tiên vẫn được báo
    found = ct.apply_check(channels, {"B": {"9": v(9)}}, now=300)
    assert [i["key"] for i in found["dy_b"]] == ["dy_9"]

    msgs = ct.telegram_messages({"dy_a": [v(3)]}, channels)
    assert len(msgs) == 1 and "Kênh &lt;A&gt;" in msgs[0] and "Tập 3 &lt;b&gt;" in msgs[0] and 'href="https://www.douyin.com/video/3"' in msgs[0]
    many = ct.telegram_messages({"dy_a": [v(i) for i in range(200)]}, channels, limit=1000)
    assert len(many) > 1 and all(len(m) < 4096 for m in many)
    for bad_token in ("", "abc", "123:short"):
        try:
            ct.telegram_call(bad_token, "getMe", {})
        except ValueError:
            continue
        raise AssertionError("token sai định dạng phải bị chặn trước khi gọi mạng")


def test_follow_check_end_to_end():
    """Crawler giả ghi video vào creator_contents_*.jsonl như base, Telegram giả ghi lại tin nhắn."""
    sec = "MS4wLjABAAAAfakechannel"
    with tempfile.TemporaryDirectory() as tmp:
        base, data = Path(tmp) / "base", Path(tmp) / "data"
        base.mkdir()
        counter = Path(tmp) / "round.txt"
        counter.write_text("0")
        (base / "run").write_text("\n".join([
            "import json, sys, time, pathlib",
            "argv = sys.argv",
            "out = pathlib.Path(argv[argv.index('--save_data_path') + 1]) / 'douyin' / 'jsonl'",
            "out.mkdir(parents=True, exist_ok=True)",
            "creator = argv[argv.index('--creator_id') + 1]",
            f"rnd = int(pathlib.Path(r'{counter}').read_text())",
            "ids = ['1', '2'] + (['3'] if rnd else [])",
            "with open(out / 'creator_contents_2026-11-04.jsonl', 'a', encoding='utf-8') as f:",
            "    for i in ids:",
            "        f.write(json.dumps({'aweme_id': i, 'title': 'Tập ' + i, 'aweme_url': 'https://www.douyin.com/video/' + i,",
            "                            'source_creator': creator, 'last_modify_ts': int(time.time() * 1000),",
            "                            'creator_avatar': 'https://p3.douyinpic.com/ava' + str(rnd) + '.jpeg'}) + '\\n')",
            "print('creator mode', creator)",
        ]), encoding="utf-8")
        sent = []
        old = ct.BASE_DIR, ct.uv_path, ct.save_settings, ct.telegram_call, ct.download_thumb, ct.fetch_image
        ct.BASE_DIR, ct.uv_path = base, lambda: sys.executable
        fetched = []
        ct.fetch_image = lambda url, referer: fetched.append(url) or b"\xff\xd8\xff" + url.encode()  # ảnh JPEG giả
        ct.save_settings = lambda settings: None
        ct.telegram_call = lambda token, method, params: sent.append((method, params)) or {}
        ct.download_thumb = lambda item, data_dir: None
        try:
            api = ct.Api()
            api._settings = {"data_dir": str(data), "telegram": {"token": ct.encrypt_token("1:x"), "chat_id": "42"}}
            assert api.check_follows()["error"] == "Chưa theo dõi kênh nào"
            res = api.follow_channel(f"https://www.douyin.com/user/{sec}", "Kênh thử")
            assert res["channels"][0]["name"] == "Kênh thử"
            assert api.follow_channel(sec)["error"]  # trùng
            key = res["channels"][0]["key"]
            assert api.rename_channel(key, "  Tên tự đặt  ")["channels"][0]["name"] == "Tên tự đặt"
            assert api.rename_channel(key, "   ")["channels"][0]["name"] == "Tên tự đặt"  # tên trống thì giữ tên cũ
            api.rename_channel(key, "Kênh thử")

            def run_check():
                assert api.check_follows() == {"ok": True}
                assert api.check_follows()["error"]  # đang bận
                began = time.time()
                while api._state != "idle" and time.time() - began < 20:
                    time.sleep(0.1)

            run_check()  # lần đầu: ghi nhận video 1, 2
            assert api.follows()["channels"][0]["new"] == [] and sent == []
            # ảnh đại diện kênh: lấy từ lần kiểm tra, lưu file trên máy, giao diện nhận data URI
            assert fetched == ["https://p3.douyinpic.com/ava0.jpeg"]
            assert api.follows()["channels"][0]["avatar"].startswith("data:image/jpeg;base64,")
            assert "--type creator" in " ".join(api._lines) and "--crawler_max_notes_count 18" in " ".join(api._lines)
            counter.write_text("1")
            time.sleep(1.1)  # lần sau ghi dòng mới có last_modify_ts mới hơn
            run_check()  # video 3 mới
            follow = api.follows()
            assert fetched[-1] == "https://p3.douyinpic.com/ava1.jpeg"  # kênh đổi ảnh: tải lại
            assert follow["channels"][0]["new"] == ["3"]
            assert sent and sent[0][0] == "sendMessage" and sent[0][1]["chat_id"] == "42" and "Tập 3" in sent[0][1]["text"]
            assert api.mark_seen()["channels"][0]["new"] == []
            assert api.set_follow_settings(None, 5)["error"] and api.set_follow_settings(False, 12)["settings"]["hours"] == 12
            assert api.unfollow(follow["channels"][0]["key"])["channels"] == []
        finally:
            ct.BASE_DIR, ct.uv_path, ct.save_settings, ct.telegram_call, ct.download_thumb, ct.fetch_image = old


def test_login_button_flow():
    """Nút Đăng nhập: chạy base ở chế độ chỉ đăng nhập, rồi chạy lại (ẩn) để xem phiên có được giữ không."""
    with tempfile.TemporaryDirectory() as tmp:
        base, bd = Path(tmp) / "base", Path(tmp) / "bd"
        base.mkdir()
        bd.mkdir()
        mode = Path(tmp) / "mode.txt"  # crawler giả: "ok" | "lost" (mất phiên khi mở lại) | "fail"
        runs = Path(tmp) / "runs.txt"  # đếm số lần chạy: lần 2 trở đi là lần mở lại để kiểm tra
        (base / "run").write_text("\n".join([
            "import os, sys, pathlib",
            f"mode = pathlib.Path(r'{mode}').read_text()",
            "headless = sys.argv[sys.argv.index('--headless') + 1] == 'true'",
            "assert os.environ['MEDIACRAWLER_LOGIN_ONLY'] == '1'",
            f"runs = pathlib.Path(r'{runs}')",
            "n = int(runs.read_text()) + 1 if runs.exists() else 1",
            "runs.write_text(str(n))",
            "print('headless' if headless else 'window', 'run', n)",
            "if mode == 'ok' or (mode == 'lost' and n == 1):",
            "    print('[Đăng nhập] Douyin: LOGIN_OK')",
        ]), encoding="utf-8")
        old = ct.BASE_DIR, ct.BROWSER_DATA_DIR, ct.uv_path
        ct.BASE_DIR, ct.BROWSER_DATA_DIR, ct.uv_path = base, bd, lambda: sys.executable
        try:
            api = ct.Api()
            api._settings = {"data_dir": str(Path(tmp) / "data")}
            (bd / "dy.logged_in").write_text("1")

            def run(verify_only):
                api._lines.clear()
                runs.unlink(missing_ok=True)
                assert api.login_platform("dy", verify_only) == {"ok": True}
                assert api.login_platform("dy", verify_only)["error"]  # đang bận
                began = time.time()
                while api._state != "idle" and time.time() - began < 20:
                    time.sleep(0.1)
                return "\n".join(api._lines)

            mode.write_text("ok")
            log = run(False)
            # kiểm tra lại cũng mở cửa sổ: Douyin/Xiaohongshu coi Chrome ẩn là chưa đăng nhập
            assert "window run 2" in log and "headless" not in log and "phiên đăng nhập được giữ" in log
            assert not (bd / "dy.logged_in").exists()  # bấm Đăng nhập thì bỏ dấu cũ (base ghi lại khi LOGIN_OK)

            mode.write_text("lost")
            log = run(False)
            assert "KHÔNG còn sau khi đóng Chrome" in log

            mode.write_text("fail")
            log = run(False)
            assert "Chưa đăng nhập được" in log and "run 2" not in log  # không đăng nhập được thì không kiểm tra lại

            mode.write_text("ok")
            log = run(True)  # Kiểm tra phiên: chạy 1 lần
            assert "run 2" not in log and "headless" not in log and "phiên đăng nhập được giữ" in log
            assert api.login_platform("nope")["error"]
        finally:
            ct.BASE_DIR, ct.BROWSER_DATA_DIR, ct.uv_path = old


def test_search_channels():
    with tempfile.TemporaryDirectory() as tmp:
        base, data = Path(tmp) / "base", Path(tmp) / "data"
        base.mkdir()
        results = [
            {"sec_uid": "MS4wLjABAAAAkenhA", "nickname": "Kênh A <b>", "douyin_id": "kenha", "followers": "1.2万",
             "videos": 30, "signature": "phim AI", "avatar": "https://p3.douyinpic.com/a.jpg"},
            {"sec_uid": "../../evil", "nickname": "giả mạo"},  # ID lạ bị loại
            {"sec_uid": "MS4wLjABAAAAkenhB", "nickname": "Kênh B", "avatar": "http://khong-https/b.jpg"},
        ]
        (base / "run").write_text("\n".join([
            "import json, os",
            "assert os.environ['MEDIACRAWLER_USER_SEARCH'] == 'phim AI'",
            "print('dang tim')",
            f"print('USER_SEARCH_RESULT ' + json.dumps({json.dumps(results, ensure_ascii=False)!r} and json.loads({json.dumps(results, ensure_ascii=False)!r}), ensure_ascii=False))",
        ]), encoding="utf-8")
        old = ct.BASE_DIR, ct.uv_path, ct.fetch_image, ct.save_settings
        ct.BASE_DIR, ct.uv_path, ct.save_settings = base, lambda: sys.executable, (lambda settings: None)
        ct.fetch_image = lambda url, referer: b"\xff\xd8\xff\xe0jpg" if url.startswith("https://") else None
        try:
            api = ct.Api()
            api._settings = {"data_dir": str(data)}
            assert api.search_channels("  ")["error"]
            api.follow_channel("https://www.douyin.com/user/MS4wLjABAAAAkenhB", "Kênh B")
            res = api.search_channels("phim AI")
            assert [r["nickname"] for r in res["results"]] == ["Kênh A <b>", "Kênh B"]
            a, b = res["results"]
            assert a["url"] == "https://www.douyin.com/user/MS4wLjABAAAAkenhA" and a["followers"] == 12000
            assert a["avatar"].startswith("data:image/jpeg;base64,") and b["avatar"] == ""
            assert a["followed"] is False and b["followed"] is True
            assert "USER_SEARCH_RESULT" not in "\n".join(api._lines) and "Tìm thấy 2 kênh" in "\n".join(api._lines)
            assert api._state == "idle"
        finally:
            ct.BASE_DIR, ct.uv_path, ct.fetch_image, ct.save_settings = old


def test_app_self_update():
    assert ct.parse_version("app-v1.2.10") == (1, 2, 10) and ct.parse_version("v2") == ()
    exe_bytes = b"MZ" + b"\0" * 1_200_000
    sha = __import__("hashlib").sha256(exe_bytes).hexdigest()
    releases = [
        {"tag_name": "app-v1.3.0", "draft": True, "assets": [{"name": "CrawlTool.exe", "browser_download_url": "u-draft"}]},  # nháp: bỏ
        {"tag_name": "app-v1.2.0", "body": f"Sửa dán\n\nsha256: {sha}", "assets": [{"name": "CrawlTool.exe", "browser_download_url": "u-120", "size": 9}]},
        {"tag_name": "app-v1.10.0", "body": "không có file exe", "assets": []},  # thiếu file: bỏ
        {"tag_name": "v9.9.9", "assets": [{"name": "CrawlTool.exe", "browser_download_url": "u-khac"}]},  # không phải tag app: bỏ
        {"tag_name": "app-v1.1.5", "assets": [{"name": "CrawlTool.exe", "browser_download_url": "u-115"}]},
    ]
    old_get = ct.github_get
    ct.github_get = lambda url, timeout=120: json.dumps(releases).encode()
    try:
        latest = ct.latest_app_release()
    finally:
        ct.github_get = old_get
    assert latest["version"] == "1.2.0" and latest["asset_url"] == "u-120" and latest["sha256"] == sha
    assert latest["notes"] == "Sửa dán"

    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / "CrawlTool.exe"
        exe.write_bytes(b"MZ old")
        for bad, bad_sha in ((b"not an exe" * 200_000, sha), (exe_bytes, "0" * 64), (exe_bytes, "")):
            try:
                ct.apply_app_update(bad, exe, bad_sha)
            except ValueError:
                continue
            raise AssertionError("file sai phải bị từ chối")
        assert exe.read_bytes() == b"MZ old"  # từ chối thì không đụng bản đang dùng
        ct.apply_app_update(exe_bytes, exe, sha)
        assert exe.read_bytes() == exe_bytes and (Path(tmp) / "CrawlTool.old.exe").read_bytes() == b"MZ old"
        ct.remove_old_exe(exe)
        assert not (Path(tmp) / "CrawlTool.old.exe").exists()
        # mở lại bản mới phải reset môi trường PyInstaller, không thì bản mới mất thư mục tạm (mất uv.exe)
        from unittest import mock
        with mock.patch.object(ct.subprocess, "Popen") as popen:
            ct.launch_fresh(exe)
        assert popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"

    api = ct.Api()
    assert api.update_app()["error"] == "Đang dùng bản mới nhất"
    api._app_update = latest
    assert "CrawlTool.exe" in api.update_app()["error"]  # chạy từ mã nguồn thì không tự cập nhật
    assert api.poll()["app_update"] == {"version": "1.2.0", "notes": "Sửa dán"}


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
             "cover_url": "https://p3.douyinpic.com/a.jpg", "liked_count": "9000", "last_modify_ts": 2,
             "series_id": "7694570596704716826", "series_name": "《总裁心声太反差》", "episode": 7, "episodes_total": 9},
            {"aweme_id": "222", "series_id": "1/../x", "aweme_url": "javascript:alert(1)", "cover_url": "http://x/b.jpg", "last_modify_ts": 1},
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
        assert {k: items["dy_111"][k] for k in ("series_id", "series_name", "episode", "episodes_total")} == {
            "series_id": "7694570596704716826", "series_name": "《总裁心声太反差》", "episode": 7, "episodes_total": 9}
        assert items["dy_222"]["series_id"] == "1" and items["xhs_abc"]["series_id"] == ""  # ID series chỉ giữ chữ số
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


def test_bundled_base_installs_once():
    old = ct.APP_HOME, ct.BASE_DIR, ct.BROWSER_DATA_DIR, ct.save_settings, ct.bundled_base, ct.latest_app_release
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        zip_path = home / "base.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("MediaCrawler/main.py", "print('base')")
        ct.APP_HOME, ct.BASE_DIR, ct.BROWSER_DATA_DIR = home, home / "base", home / "browser_data"
        ct.save_settings = lambda settings: None
        ct.bundled_base = lambda: (zip_path, "c0ffee1234567890")
        ct.latest_app_release = lambda: {}
        try:
            api = ct.Api()
            api._settings = {"token": "cu"}
            api.init()
            assert "token" not in api._settings  # token GitHub bản cũ bị xóa khỏi máy
            syncs = []
            api._run_logged = lambda cmd, cwd, *a, **k: syncs.append(cwd) or 0
            api._update_worker()
            base = home / "base-c0ffee123456"
            assert (base / "main.py").read_text() == "print('base')" and syncs == [base]
            assert api._settings["base_sha"] == "c0ffee1234567890" and ct.current_base(api._settings) == base
            api._update_worker()  # mở lại app: đã đúng bản, không cài lại
            assert syncs == [base] and api._state == "idle"
        finally:
            ct.APP_HOME, ct.BASE_DIR, ct.BROWSER_DATA_DIR, ct.save_settings, ct.bundled_base, ct.latest_app_release = old


def test_token_roundtrip():
    blob = ct.encrypt_token("github_pat_test")
    assert "github_pat_test" not in blob
    assert ct.decrypt_token(blob) == "github_pat_test"


def test_base_update_keeps_login_profiles():
    old = ct.APP_HOME, ct.BASE_DIR, ct.BROWSER_DATA_DIR, ct.save_settings
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        ct.APP_HOME, ct.BASE_DIR, ct.BROWSER_DATA_DIR = home, home / "base", home / "browser_data"
        ct.save_settings = lambda settings: None
        try:
            # Tình huống thật: bản app cũ cập nhật lỗi giữa chừng, hồ sơ nằm rải ở base và base_new
            (home / "base" / "browser_data" / "cdp_xhs_user_data_dir").mkdir(parents=True)
            (home / "base" / "browser_data" / "cdp_xhs_user_data_dir" / "Cookies").write_text("xhs-moi")
            (home / "base" / "browser_data" / "cdp_dy_user_data_dir").mkdir()
            (home / "base" / "browser_data" / "cdp_dy_user_data_dir" / "Cookies").write_text("dy-moi")
            (home / "base_new" / "browser_data" / "cdp_dy_user_data_dir").mkdir(parents=True)
            (home / "base_new" / "browser_data" / "cdp_dy_user_data_dir" / "Cookies").write_text("dy-cu")
            (home / "base_new" / "browser_data" / "cdp_bili_user_data_dir").mkdir()
            (home / "base" / "main.py").write_text("print('v1')")
            assert ct.browser_data_dir() == home / "base" / "browser_data"  # chưa chuyển: dùng chỗ cũ
            assert ct.current_base({}) == home / "base"

            messages = ct.migrate_browser_data()
            data = home / "browser_data"
            assert ct.browser_data_dir() == data
            assert (data / "cdp_xhs_user_data_dir" / "Cookies").read_text() == "xhs-moi"
            assert (data / "cdp_dy_user_data_dir" / "Cookies").read_text() == "dy-moi"  # bản trong base (mới hơn) thắng
            assert (data / "cdp_bili_user_data_dir").is_dir() and len(messages) == 3

            # Cài bản mới vào thư mục riêng, không đổi tên thư mục cũ
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("MrNMDien-MediaCrawler-abc1234/main.py", "print('v2')")
            new_dir = ct.base_dir_for("abc1234def5678")
            ct.extract_zipball(buf.getvalue(), new_dir)
            assert new_dir.name == "base-abc1234def56" and (new_dir / "main.py").read_text() == "print('v2')"
            ct.remove_old_bases(keep=new_dir)
            assert sorted(p.name for p in home.iterdir()) == ["base-abc1234def56", "browser_data"]
            assert ct.current_base({"base_dir": str(new_dir)}) == new_dir
            assert ct.uv_env()["MEDIACRAWLER_BROWSER_DATA_DIR"] == str(data)

            # Đánh dấu đã đăng nhập do base ghi; nút "Đăng nhập lại" xóa nó
            (data / "xhs.logged_in").write_text("1")
            api = ct.Api()
            api._settings = {}
            assert api.get_login()["sessions"] == ["xhs"]
            assert api.reset_session("xhs")["sessions"] == [] and not (data / "xhs.logged_in").exists()
            assert api.reset_session("nope")["error"]
        finally:
            ct.APP_HOME, ct.BASE_DIR, ct.BROWSER_DATA_DIR, ct.save_settings = old


def test_movie_calendar():
    from datetime import date
    # Giờ mùa hè Mỹ 2026: 08/03 -> 01/11 (đổi lúc 2 giờ sáng, nên 00:00 ngày đổi vẫn theo giờ cũ)
    assert [ct.pacific_offset(date(2026, 3, d)) for d in (8, 9)] == [-8, -7]
    assert [ct.pacific_offset(date(2026, 11, d)) for d in (1, 2)] == [-7, -8]
    assert ct.vn_release("2026-11-06T00:00:00.000Z", "Netflix") == ("2026-11-06", "15:00")
    assert ct.vn_release("2026-11-01", "Netflix") == ("2026-11-01", "14:00")
    assert ct.vn_release("2026-11-18", "Prime Video") == ("2026-11-18", "07:00")
    assert ct.streamer_of("Netflix Standard with Ads") == "Netflix"
    assert ct.streamer_of("Paramount+ Amazon Channel") == "Paramount+"
    assert ct.streamer_of("Max") == "HBO Max" and ct.streamer_of("Maxdome") is None and ct.streamer_of(None) is None

    def movie(mid, us_dates, note="", flatrate=(), videos=(), lang="en", kr_dates=(), kr_flatrate=()):
        return {
            "id": mid, "title": f"Phim {mid}", "release_date": "2026-01-01", "popularity": mid, "original_language": lang,
            "release_dates": {"results": [
                {"iso_3166_1": "GB", "release_dates": [{"type": 4, "release_date": "2026-11-05"}]},
                {"iso_3166_1": "US", "release_dates": [{"type": t, "release_date": d, "note": note} for t, d in us_dates]},
                {"iso_3166_1": "KR", "release_dates": [{"type": t, "release_date": d, "note": n} for t, d, n in kr_dates]}]},
            "watch/providers": {"results": {"US": {"flatrate": [{"provider_name": p} for p in flatrate]},
                                            "KR": {"flatrate": [{"provider_name": p} for p in kr_flatrate]}}},
            "videos": {"results": list(videos)},
        }

    def show(sid, first_air, networks, episodes=16, kr_flatrate=()):
        return {"id": sid, "name": f"Drama {sid}", "first_air_date": first_air, "popularity": sid, "original_language": "ko",
                "networks": [{"name": n} for n in networks], "number_of_episodes": episodes,
                "watch/providers": {"results": {"KR": {"flatrate": [{"provider_name": p} for p in kr_flatrate]}}},
                "videos": {"results": [{"site": "YouTube", "type": "Teaser", "key": f"teaser{sid}x"}]}}

    movies = {
        1: movie(1, [(4, "2026-11-09T00:00:00.000Z")], note="Netflix",
                 videos=[{"site": "YouTube", "type": "Teaser", "key": "teaser00001"},
                         {"site": "YouTube", "type": "Trailer", "key": "unofficial1", "official": False},
                         {"site": "YouTube", "type": "Trailer", "key": "official001", "official": True}]),
        2: movie(2, [(3, "2026-09-01"), (4, "2026-11-20")], flatrate=["HBO Max", "Some Local Service"]),
        3: movie(3, [(4, "2026-11-12")]),  # chỉ thuê/mua
        4: movie(4, [(4, "2027-03-01")], note="Netflix"),  # ngoài khoảng
        5: movie(5, [(4, "2026-11-25")], flatrate=["Hulu"]),  # lỗi khi lấy chi tiết
        6: movie(6, [], lang="ko", kr_dates=[(3, "2026-11-12", "")]),  # phim Hàn chỉ chiếu rạp ở Hàn
        7: movie(7, [(4, "2026-11-06")], note="Netflix", lang="ko", kr_dates=[(4, "2026-11-06", "Netflix")]),  # ra toàn cầu
        8: movie(8, [], lang="ko", kr_dates=[(4, "2026-11-15", "")], kr_flatrate=["TVING"]),  # chỉ lên TVING
    }
    shows = {
        101: show(101, "2026-11-14", ["tvN"], kr_flatrate=["TVING", "Netflix"]),
        102: show(102, "2026-11-20", ["Netflix"], episodes=8),
        103: show(103, "2027-02-01", ["JTBC"]),  # ngoài khoảng
    }
    calls = []

    def fake_get(key, path, params):
        calls.append((path, params))
        assert key == "k"
        if path == "/discover/movie":
            assert (params["release_date.gte"], params["release_date.lte"]) == ("2026-10-29", "2027-01-04")
            if params["region"] == "US":
                assert params["with_release_type"] == 4
                return {"results": [{"id": i} for i in (4, 3, 2, 1, 1, 5, 7)], "total_pages": 1}
            assert (params["region"], params["with_release_type"], params["with_original_language"]) == ("KR", "3|4", "ko")
            return {"results": [{"id": i} for i in (6, 7, 8)], "total_pages": 1}
        if path == "/discover/tv":
            assert params["with_original_language"] == "ko" and params["first_air_date.gte"] == "2026-10-29"
            return {"results": [{"id": i} for i in shows], "total_pages": 1}
        kind, mid = path.strip("/").split("/")
        if mid == "5":
            raise ct.urllib.error.URLError("timeout")
        return (shows if kind == "tv" else movies)[int(mid)]

    items = ct.fetch_movie_calendar("k", date(2026, 11, 5), get=fake_get)
    assert [c[0] for c in calls].count("/discover/movie") == 2  # total_pages = 1: không gọi trang 2
    assert [c[0] for c in calls].count("/movie/7") == 1  # id trùng (cả lịch Mỹ lẫn Hàn) chỉ lấy 1 lần
    by_id = {i["id"]: i for i in items}
    # cùng ngày: có giờ trước, chưa rõ giờ sau, rồi phổ biến hơn trước; phim 7 chỉ còn bản lịch Mỹ
    assert [i["id"] for i in items] == [7, 1, 6, 3, 101, 8, 102, 2]
    netflix, rent, hbo = by_id[1], by_id[3], by_id[2]
    assert (netflix["date"], netflix["time"], netflix["platforms"], netflix["kind"], netflix["vn"], netflix["type"]) == \
        ("2026-11-09", "15:00", ["Netflix"], "original", True, "movie")
    assert netflix["trailer"] == "https://www.youtube.com/watch?v=official001"
    assert (hbo["platforms"], hbo["kind"]) == (["HBO Max"], "premiere")
    assert (rent["platforms"], rent["kind"], rent["vn"], rent["time"]) == ([], "rent", False, "")
    assert rent["trailer"].startswith("https://www.youtube.com/results?search_query=Phim%203")
    assert (by_id[7]["region"], by_id[7]["lang"], by_id[7]["time"]) == ("US", "ko", "15:00")
    assert (by_id[6]["kind"], by_id[6]["region"], by_id[6]["time"], by_id[6]["platforms"]) == ("theater", "KR", "", [])
    assert (by_id[8]["platforms"], by_id[8]["kind"], by_id[8]["time"], by_id[8]["vn"]) == (["TVING"], "original", "", False)
    tvn, nfx = by_id[101], by_id[102]
    assert (tvn["type"], tvn["kind"], tvn["platforms"], tvn["channel"], tvn["episodes"], tvn["time"]) == \
        ("tv", "series", ["TVING", "Netflix"], "tvN", 16, "")
    assert tvn["url"] == "https://www.themoviedb.org/tv/101" and tvn["trailer"].endswith("teaser101x")
    assert (nfx["platforms"], nfx["channel"], nfx["time"], nfx["vn"], nfx["title"]) == (["Netflix"], "", "15:00", True, "Drama 102")

    # Api: chưa có key thì không gọi mạng; có key thì cache, quá 6 giờ mới lấy lại
    with tempfile.TemporaryDirectory() as tmp:
        old_file, old_fetch, old_settings = ct.MOVIES_FILE, ct.fetch_movie_calendar, ct.SETTINGS_FILE
        ct.MOVIES_FILE, ct.SETTINGS_FILE = Path(tmp) / "movies.json", Path(tmp) / "settings.json"
        fetched = []
        ct.fetch_movie_calendar = lambda key, today: fetched.append(key) or [{"id": 9}]
        try:
            api = ct.Api()
            assert api.movies() == {"has_key": False, "items": [], "fetched_at": 0,
                                    "streamers": {n: s["vn"] for n, s in ct.STREAMERS.items()}}
            assert api.set_tmdb("  key123  ") == {"has_key": True} and api._tmdb_key() == "key123"
            assert api.movies()["items"] == [{"id": 9}] and api.movies()["items"] == [{"id": 9}]
            assert fetched == ["key123"]  # lần 2 lấy từ cache
            api.movies(refresh=True)
            assert len(fetched) == 2
            ct.fetch_movie_calendar = lambda key, today: (_ for _ in ()).throw(ct.urllib.error.URLError("offline"))
            res = api.movies(refresh=True)
            assert res["items"] == [{"id": 9}] and "Không tải được" in res["error"]  # lỗi mạng vẫn giữ lịch cũ
            assert api.set_tmdb(clear=True) == {"has_key": False} and not ct.MOVIES_FILE.exists()
        finally:
            ct.MOVIES_FILE, ct.fetch_movie_calendar, ct.SETTINGS_FILE = old_file, old_fetch, old_settings


def test_bundled_supabase():
    with tempfile.TemporaryDirectory() as tmp:
        old = ct.__file__
        ct.__file__ = str(Path(tmp) / "crawl_tool.py")
        try:
            assert ct.bundled_supabase() == {}
            (Path(tmp) / "supabase.json").write_text('{"url": "http://x", "key": "k"}', encoding="utf-8")
            assert ct.bundled_supabase() == {}  # chỉ nhận https
            (Path(tmp) / "supabase.json").write_text('{"url": "https://x.supabase.co/", "key": "sb_publishable_1"}', encoding="utf-8")
            assert ct.bundled_supabase() == {"url": "https://x.supabase.co", "key": "sb_publishable_1"}
        finally:
            ct.__file__ = old


def test_supabase_sync():
    """2 máy đăng nhập cùng tài khoản (Supabase giả, gói sẵn trong app): theo dõi, đổi tên, bỏ theo dõi ở máy này thì máy kia thấy."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse, parse_qs

    accounts = {"a@b.c": {"password": "pw", "id": "u1"}, "x@y.z": {"password": "pw2", "id": "u2"}}
    account = accounts["a@b.c"]
    rows = {"u1": {}, "u2": {}}  # như RLS: mỗi tài khoản một tập dòng riêng
    db = rows["u1"]
    tokens = {"n": 0, "valid": {}}  # refresh token -> tài khoản; mỗi lần đăng nhập 1 token riêng, dùng 1 lần là đổi
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, code, body=None):
            data = b"" if body is None else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def uid(self):
            auth = self.headers.get("Authorization", "")
            return auth[len("Bearer at-"):] if auth.startswith("Bearer at-") else None

        def do_PUT(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            uid = self.uid()
            if self.path != "/auth/v1/user" or not uid:
                return self.reply(401, {"message": "no auth"})
            next(a for a in accounts.values() if a["id"] == uid)["password"] = body["password"]
            self.reply(200, {"id": uid})

        def do_GET(self):
            calls.append(("GET", self.path))
            if self.headers.get("apikey") != "pk" or not self.uid():
                return self.reply(401, {"message": "no auth"})
            self.reply(200, list(rows[self.uid()].values()))

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            url = urlparse(self.path)
            calls.append(("POST", url.path))
            if self.headers.get("apikey") != "pk":
                return self.reply(401, {"message": "Invalid API key"})
            if url.path == "/auth/v1/token":
                grant = parse_qs(url.query)["grant_type"][0]
                if grant == "password":
                    acc = accounts.get(body["email"])
                    if not acc or acc["password"] != body["password"]:
                        return self.reply(400, {"error_description": "Invalid login credentials"})
                    uid = acc["id"]
                else:
                    uid = tokens["valid"].pop(body["refresh_token"], None)
                    if not uid:
                        return self.reply(400, {"error_description": "Invalid Refresh Token"})
                tokens["n"] += 1
                tokens["valid"][f"rt{tokens['n']}"] = uid
                return self.reply(200, {"access_token": f"at-{uid}", "refresh_token": f"rt{tokens['n']}",
                                        "expires_in": 3600, "user": {"id": uid}})
            if not self.uid():
                return self.reply(401, {"message": "no auth"})
            for row in body:  # upsert theo (user, key): ghi vào đúng tài khoản trong token
                rows[self.uid()][row["key"]] = row
            self.reply(201)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    old = ct.save_settings, ct.Api._sync_soon, ct.SUPABASE
    ct.save_settings = lambda settings: None
    ct.Api._sync_soon = lambda self: self._sync()  # chạy ngay để test không phải chờ luồng nền
    try:
        with tempfile.TemporaryDirectory() as tmp:
            machines = []
            for name in ("A", "B"):
                api = ct.Api()
                api._settings = {"data_dir": str(Path(tmp) / name)}
                machines.append(api)
            a, b = machines
            ct.SUPABASE = {}
            assert not a.get_sync()["available"] and a.sync_login("a@b.c", "pw")["error"]  # bản không gói đồng bộ
            ct.SUPABASE = {"url": url, "key": "pk"}
            assert a.get_sync()["available"] and a.sync_login("a@b.c", "")["error"]
            assert "Sai email" in a.sync_login("a@b.c", "sai")["error"]
            a.follow_channel("https://www.douyin.com/user/MS4wLjABAAAAkenhA", "Kênh A")  # có trước khi đăng nhập
            res = a.sync_login("a@b.c", "pw")
            assert res["logged_in"] and not res["problem"], res
            saved = a._settings["sync"]
            assert set(saved) == {"email", "user_id", "refresh", "last_sync", "error"}  # không lưu mật khẩu
            assert not saved["refresh"].startswith("rt") and ct.decrypt_token(saved["refresh"]).startswith("rt")  # token mã hóa
            assert list(db) == ["dy_MS4wLjABAAAAkenhA"]

            b.sync_login("a@b.c", "pw")
            assert [c["name"] for c in b.follows()["channels"]] == ["Kênh A"]

            b.follow_channel("https://www.douyin.com/user/MS4wLjABAAAAkenhB", "Kênh B")
            b.rename_channel("dy_MS4wLjABAAAAkenhA", "Tên mới")
            a.sync_now()
            assert sorted(c["name"] for c in a.follows()["channels"]) == ["Kênh B", "Tên mới"]

            time.sleep(0.002)
            a.unfollow("dy_MS4wLjABAAAAkenhB")
            b.sync_now()
            assert [c["name"] for c in b.follows()["channels"]] == ["Tên mới"]  # bỏ theo dõi ở A thì B cũng bỏ
            assert db["dy_MS4wLjABAAAAkenhB"]["deleted"]

            # video đã thấy (known) gộp cả 2 máy, để máy kia không báo lại
            key = "dy_MS4wLjABAAAAkenhA"
            for m, vid in ((a, "v1"), (b, "v2")):
                time.sleep(0.002)
                with m._follow_lock:
                    ch = ct.load_follows(m._data_dir())
                    ch[key]["known"] = [vid]
                    ct.write_follows(m._data_dir(), ch)
            a.sync_now(); b.sync_now(); a.sync_now()
            for m in (a, b):
                assert ct.load_follows(m._data_dir())[key]["known"] == ["v1", "v2"]
            pushes = calls.count(("POST", "/rest/v1/follows"))
            a.sync_now(); b.sync_now()
            assert calls.count(("POST", "/rest/v1/follows")) == pushes  # không đổi gì thì không đẩy

            # access token hết hạn: tự lấy token mới bằng refresh token (đổi mỗi lần) và lưu lại
            a._sync_access = None
            assert not a.sync_now()["problem"]
            a._sync_access = None
            assert not a.sync_now()["problem"]  # dùng được refresh token mới vừa lưu
            # phiên của B bị thu hồi (vd. đổi mật khẩu): báo đăng nhập lại
            tokens["valid"].pop(ct.decrypt_token(b._settings["sync"]["refresh"]))
            b._sync_access = None
            res = b.sync_now()
            assert not res["logged_in"] and "đăng nhập lại" in res["problem"]
            # đổi mật khẩu: phải đúng mật khẩu cũ; xong thì đăng nhập bằng mật khẩu mới
            assert "không đúng" in a.sync_change_password("sai", "matkhaumoi1")["error"]
            assert a.sync_change_password("pw", "ngan")["error"]
            assert a.sync_change_password("pw", "matkhaumoi1") == {"ok": True} and account["password"] == "matkhaumoi1"
            assert not a.sync_now()["problem"]  # phiên vẫn dùng tiếp
            assert "Sai email" in b.sync_login("a@b.c", "pw")["error"]
            assert b.sync_login("a@b.c", "matkhaumoi1")["logged_in"]
            assert not a.sync_logout()["logged_in"]
            assert a.sync_change_password("matkhaumoi1", "khac12345")["error"]  # đã đăng xuất

            # cùng máy A, người khác đăng nhập: không thấy và không nhận nhầm kênh của tài khoản trước
            mine = sorted(ct.load_follows(a._data_dir()))
            assert mine and not rows["u2"]
            res = a.sync_login("x@y.z", "pw2")
            assert res["logged_in"] and not res["problem"]
            assert a.follows()["channels"] == [] and rows["u2"] == {}  # không đẩy kênh của u1 sang u2
            a.follow_channel("https://www.douyin.com/user/MS4wLjABAAAAkenhX", "Kênh của X")
            assert list(rows["u2"]) == ["dy_MS4wLjABAAAAkenhX"] and "dy_MS4wLjABAAAAkenhX" not in rows["u1"]
            assert list(Path(a._data_dir()).glob("follows.u1.bak.json"))  # danh sách cũ được cất, không xóa
            # người cũ đăng nhập lại: lấy lại đúng kênh của mình từ máy chủ, không thấy kênh của X
            a.sync_logout()
            a.sync_login("a@b.c", "matkhaumoi1")
            assert sorted(ct.load_follows(a._data_dir())) == mine
    finally:
        ct.save_settings, ct.Api._sync_soon, ct.SUPABASE = old
        server.shutdown()


def test_channel_tags_and_stale():
    """Tag phân loại do người dùng gắn; "Lâu chưa update" tự tính từ video mới nhất của kênh (> 5 ngày)."""
    now = int(time.time())
    channels = {"dy_a": {"url": "A", "known": [], "new": []}, "dy_b": {"url": "B", "known": [], "new": []}}
    ct.apply_check(channels, {"A": {"1": {"published": now - 7 * 86400}, "2": {"published": now - 6 * 86400}},
                              "B": {"3": {"published": now - 3600}}}, now)
    assert channels["dy_a"]["last_video_at"] == now - 6 * 86400 and channels["dy_b"]["last_video_at"] == now - 3600
    ct.apply_check(channels, {"A": {"9": {"published": 0}}}, now)  # không đọc được giờ đăng: giữ mốc cũ
    assert channels["dy_a"]["last_video_at"] == now - 6 * 86400

    old_save = ct.save_settings
    ct.save_settings = lambda settings: None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            api = ct.Api()
            api._settings = {"data_dir": tmp}
            ct.write_follows(tmp, {**channels, "dy_c": {"url": "C", "name": "C", "platform": "dy"}})
            by_key = {c["key"]: c for c in api.follows()["channels"]}
            assert by_key["dy_a"]["stale"] and not by_key["dy_b"]["stale"]
            assert not by_key["dy_c"]["stale"]  # chưa kiểm tra lần nào thì không kết luận
            res = api.set_channel_tags("dy_a", ["da_lam", "la", "tiem_nang", "da_lam"])
            assert {c["key"]: c["tags"] for c in res["channels"]}["dy_a"] == ["tiem_nang", "da_lam"]
            assert res["tags"]["het_series"] == "Hết series" and res["stale_days"] == 5
            assert ct.load_follows(tmp)["dy_a"]["updated"]  # đổi tag cũng được đồng bộ
            # chọn nhiều kênh rồi gắn / bỏ tag một lần
            res = api.tag_channels(["dy_a", "dy_b", "khong_co"], "het_series")
            tags = {c["key"]: c["tags"] for c in res["channels"]}
            assert tags["dy_a"] == ["tiem_nang", "da_lam", "het_series"] and tags["dy_b"] == ["het_series"] and tags["dy_c"] == []
            tags = {c["key"]: c["tags"] for c in api.tag_channels(["dy_a", "dy_b"], "tiem_nang", False)["channels"]}
            assert tags["dy_a"] == ["da_lam", "het_series"] and tags["dy_b"] == ["het_series"]
            assert api.tag_channels(["dy_a"], "bua")["error"]
    finally:
        ct.save_settings = old_save


def test_series_appraisal():
    """Cờ bản quyền, số tập đọc từ mô tả, tình trạng series (đang ra / kết thúc / drop / chưa rõ)."""
    assert ct.rights_flag("《师尊》第3集 #红果短剧") == ("big", "红果")
    assert ct.rights_flag("原创剧，没有小说，没有上架红果") == ("small", "原创剧")  # "không lên Hồng Quả" không phải bản quyền lớn
    assert ct.rights_flag("请勿爆书名 第2集") == ("unclear", "请勿爆书名")
    assert ct.rights_flag("鄂作登字-2025-I-1234 原创") == ("small", "作登字")
    assert ct.rights_flag("tập 5") == ("", "")

    info = ct.episode_info
    assert info("《傲娇师尊》第 15集 看全集点合集") == {"episode": 15, "episode_end": 15, "compilation": False, "finale": False, "total": 0}
    assert info("EP12大结局") == {"episode": 12, "episode_end": 12, "compilation": False, "finale": True, "total": 0}
    assert info("1-8合 纯享版") == {"episode": 1, "episode_end": 8, "compilation": True, "finale": False, "total": 0}
    assert info("第176-177集")["episode_end"] == 177
    assert info("纯享版 加长") == {"episode": 0, "episode_end": 0, "compilation": True, "finale": False, "total": 0}
    assert not info("第20集 未完结")["finale"] and not info("还没完结 第3集")["finale"]
    assert info("#短剧合集 #第99集 剧情")["episode"] == 0  # hashtag không tính
    assert info("REP12 sleep") == {"episode": 0, "episode_end": 0, "compilation": False, "finale": False, "total": 0}
    # tổng số tập trong caption
    assert info("《师尊》第8集 共30集")["total"] == 30
    assert (info("《师尊》8/30集")["episode"], info("《师尊》8/30集")["total"]) == (8, 30)
    assert info("第8集/共30集")["total"] == 30 and info("全24集 第3集")["total"] == 24
    assert info("看全集 第3集")["total"] == 0 and info("2025/11 第3集")["total"] == 0

    row = {"aweme_id": "1", "title": "《X》第3集 #番茄小说", "liked_count": "1.2万", "collected_count": "300",
           "series_id": "77", "series_name": "X", "episode": 9, "episodes_total": 20}
    item = ct.normalize("dy", row)
    assert item["episode"] == 3 and item["collects"] == 300 and item["rights"] == "big"  # mô tả thắng số Douyin (9)
    assert ct.normalize("dy", {**row, "title": "không số tập"})["episode"] == 9  # không có trong mô tả: dùng số Douyin
    assert item["series_key"] == "77" and item["series_name"] == "X"
    # kênh không dùng 合集: nhận series theo 《tên》 trong mô tả
    loose = ct.normalize("dy", {"aweme_id": "2", "title": "《傲娇 师尊》第5集 #短剧", "source_creator": "https://u/1"})
    assert (loose["series_id"], loose["series_key"], loose["series_name"], loose["episode"]) == ("", "t:傲娇 师尊", "傲娇 师尊", 5)
    assert ct.series_key("", "", "video thường") == ("", "")

    now, day = 1_800_000_000, 86400
    def ep(key, sid, n, published, total=10, title=""):
        return {"key": key, "series_id": sid, "episode": n, "episode_end": n, "compilation": False,
                "finale": "大结局" in title, "title": title, "published": published, "episodes_total": total,
                "likes": 100 * n, "comments": n, "collects": 2 * n, "shares": 0}
    items = [ep("a1", "A", 1, now - 5 * day), ep("a2", "A", 2, now - day, title="请勿爆书名"),
             ep("b1", "B", 1, now - 90 * day), ep("b10", "B", 10, now - 60 * day, title="大结局 #红果"),
             ep("c10", "C", 10, now - 40 * day),           # đã thấy tập 10/10, quá 21 ngày, không 大结局
             ep("d1", "D", 1, now - 40 * day, total=30),   # mới thấy tập 1/30: chưa kết luận
             ep("d1", "D", 1, now - 40 * day, total=30)]   # cùng video lặp (thư viện + kết quả)
    s = ct.series_summary(items, now)
    assert s["A"]["status"] == "airing" and s["A"]["rights"] == "unclear"
    assert s["A"]["first"] == {"episode": 1, "likes": 100} and s["A"]["last"] == {"episode": 2, "likes": 200}
    assert s["A"]["likes"] == 300 and s["A"]["collects"] == 6 and s["A"]["comments"] == 3
    assert s["B"]["status"] == "ended" and s["B"]["rights"] == "big"
    assert s["C"]["status"] == "dropped" and s["D"]["status"] == "unknown" and s["D"]["videos"] == 1
    assert s["B"]["max_episode"] == 10 and s["A"]["collection"]

    # series của kênh theo dõi (không 合集): biết kênh nào, đang up tập mấy; bản quyền tính cho cả series
    k = [{**ep(f"k{n}", "", n, now - (9 - n) * day, total=0, title=t), "series_key": "t:Y", "series_name": "Y",
          "source_creator": "https://u/1", "author": "kênh"} for n, t in ((7, ""), (8, "#红果短剧"), (9, ""))]
    y = ct.series_summary(k, now)["t:Y"]
    assert (y["name"], y["channel"], y["max_episode"], y["collection"]) == ("Y", "https://u/1", 9, False)
    assert y["rights"] == "big" and y["status"] == "airing"
    old = [{**v, "published": v["published"] - 40 * day} for v in k]
    assert ct.series_summary(old, now)["t:Y"]["status"] == "dropped"  # kênh theo dõi không up tiếp hơn 21 ngày
    assert ct.series_summary([{**v, "source_creator": ""} for v in old], now)["t:Y"]["status"] == "unknown"  # chỉ gặp qua tìm kiếm
    full = ct.series_summary([{**v, "episodes_planned": 9} for v in old], now)["t:Y"]  # caption 共9集, đã tới tập 9
    assert full["status"] == "ended" and full["episodes_total"] == 9 and full["planned"] == 9
    assert ct.series_summary([{**v, "episodes_planned": 30} for v in k], now)["t:Y"]["episodes_total"] == 30
    assert ct.normalize("dy", {"aweme_id": "3", "title": "《Z》第2集 共12集"})["episodes_planned"] == 12


def test_youtube_reup():
    """Đọc trang tìm kiếm YouTube; check_reup tìm thêm theo tên Việt (Gemini) và đưa video tiếng Việt lên đầu."""
    def page(videos):
        data = {"contents": {"list": [{"videoRenderer": {
            "videoId": vid, "title": {"runs": [{"text": title}]}, "ownerText": {"runs": [{"text": "Kênh " + vid[:2]}]},
            "viewCountText": {"simpleText": "1.000 lượt xem"}, "publishedTimeText": {"simpleText": "2 tuần trước"}}}
            for vid, title in videos] + [{"videoRenderer": {"videoId": "bad"}}]}}
        return f"<script>var ytInitialData = {json.dumps(data)};</script>"
    res = ct.parse_youtube_results(page([("aaaaaaaaaaa", "傲娇师尊 第1集"), ("bbbbbbbbbbb", "Sư Tôn Ngạo Kiều tập 1")]))
    assert [r["id"] for r in res] == ["aaaaaaaaaaa", "bbbbbbbbbbb"] and [r["vi"] for r in res] == [False, True]
    assert res[1]["channel"] == "Kênh bb" and res[1]["url"] == "https://www.youtube.com/watch?v=bbbbbbbbbbb"
    try:
        ct.parse_youtube_results("<html>consent</html>")
        assert False
    except ValueError:
        pass

    pages = {"傲娇师尊": page([("aaaaaaaaaaa", "傲娇师尊 第1集")]),
             "Sư Tôn Ngạo Kiều": page([("bbbbbbbbbbb", "Sư Tôn Ngạo Kiều vietsub"), ("aaaaaaaaaaa", "傲娇师尊 第1集")])}
    old = ct.youtube_page, ct.gemini_vi_titles, ct.decrypt_token
    ct.youtube_page = lambda q: pages[q]
    ct.decrypt_token = lambda blob: "key"
    try:
        api = ct.Api()
        api._settings = {}
        r = api.check_reup(" 傲娇师尊 ")
        assert r["queries"] == ["傲娇师尊"] and "Gemini" in r["note"] and r["vi"] == 0
        ct.gemini_vi_titles = lambda key, model, name: ["Sư Tôn Ngạo Kiều"]
        api._settings = {"gemini": {"key": "x"}}
        r = api.check_reup("傲娇师尊")
        assert r["queries"] == ["傲娇师尊", "Sư Tôn Ngạo Kiều"] and r["vi"] == 1 and not r["note"]
        assert [v["id"] for v in r["results"]] == ["bbbbbbbbbbb", "aaaaaaaaaaa"]  # tiếng Việt lên trước, không lặp
        ct.youtube_page = lambda q: (_ for _ in ()).throw(OSError("mất mạng"))
        assert "mất mạng" in api.check_reup("傲娇师尊")["error"]
        assert api.check_reup("  ")["error"]
    finally:
        ct.youtube_page, ct.gemini_vi_titles, ct.decrypt_token = old


def test_series_translation():
    """Dịch sơ tên series bằng Gemini: chạy nền, lưu series_vi.json, tên đã dịch / đã thử thì không gọi lại."""
    old = ct.gemini_text, ct.decrypt_token, ct.save_settings
    sent = []

    def fake_gemini(key, model, prompt, schema, temp):
        names = json.loads(prompt[prompt.index("["):])
        sent.append(names)
        return json.dumps([{"zh": n, "vi": "VI " + n} for n in names if n != "bỏ"] + [{"zh": "lạ", "vi": "x"}, "rác"])
    ct.gemini_text, ct.decrypt_token, ct.save_settings = fake_gemini, (lambda b: "key"), (lambda s: None)
    try:
        assert ct.gemini_translate_names("k", "m", ["师尊", "bỏ"]) == {"师尊": "VI 师尊"}  # bỏ tên lạ / dòng hỏng
        sent.clear()
        with tempfile.TemporaryDirectory() as tmp:
            jsonl = Path(tmp) / "douyin" / "jsonl"
            jsonl.mkdir(parents=True)
            rows = [{"aweme_id": str(i), "title": f"《{n}》第1集", "last_modify_ts": 1} for i, n in enumerate(["师尊", "仙途"])]
            (jsonl / "search_contents_1.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
            api = ct.Api()
            api._settings = {"data_dir": tmp}  # chưa có Gemini: không dịch
            assert api.library()["series_vi"] == {} and not sent
            api._settings = {"data_dir": tmp, "gemini": {"key": "x"}}
            api.library()
            for _ in range(50):
                if not api._vi_running:
                    break
                time.sleep(0.05)
            assert sent == [["仙途", "师尊"]]
            assert api.library()["series_vi"] == {"仙途": "VI 仙途", "师尊": "VI 师尊"}
            assert len(sent) == 1  # đã có bản dịch: không gọi Gemini lại
    finally:
        ct.gemini_text, ct.decrypt_token, ct.save_settings = old


def test_claim_channel():
    """Nhận làm kênh trên máy này; máy khác đang giữ thì phải xác nhận (force) mới đổi."""
    old_save, old_machine = ct.save_settings, ct.MACHINE_NAME
    ct.save_settings = lambda settings: None
    ct.MACHINE_NAME = "MAY-A"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            api = ct.Api()
            api._settings = {"data_dir": tmp}
            ct.write_follows(tmp, {"dy_a": {"url": "A", "name": "A", "platform": "dy"}})
            res = api.claim_channel("dy_a")
            assert res["machine"] == "MAY-A" and res["channels"][0]["holder"] == "MAY-A"
            assert ct.load_follows(tmp)["dy_a"]["updated"]  # nhận làm cũng được đồng bộ sang máy khác
            ct.MACHINE_NAME = "MAY-B"
            res = api.claim_channel("dy_a")
            assert res["holder"] == "MAY-A" and "MAY-A" in res["error"]
            assert ct.load_follows(tmp)["dy_a"]["holder"] == "MAY-A"  # chưa xác nhận thì không đổi
            assert api.claim_channel("dy_a", False)["holder"] == "MAY-A"
            assert api.claim_channel("dy_a", True, True)["channels"][0]["holder"] == "MAY-B"
            assert not api.claim_channel("dy_a", False)["channels"][0]["holder"]
            assert api.claim_channel("khong_co")["error"]
    finally:
        ct.save_settings, ct.MACHINE_NAME = old_save, old_machine


if __name__ == "__main__":
    test_series_appraisal()
    test_youtube_reup()
    test_claim_channel()
    test_series_translation()
    test_movie_calendar()
    test_bundled_supabase()
    test_supabase_sync()
    test_channel_tags_and_stale()
    test_build_args()
    test_build_jobs()
    test_login_settings()
    test_gemini_keywords()
    test_parallel_crawl()
    test_saved_library_and_live_refresh()
    test_follow_logic()
    test_follow_check_end_to_end()
    test_login_button_flow()
    test_search_channels()
    test_app_self_update()
    test_old_form_migrates()
    test_parse()
    test_normalize_and_library()
    test_thumb_cache()
    test_token_roundtrip()
    test_bundled_base_installs_once()
    test_base_update_keeps_login_profiles()
    print("OK")
