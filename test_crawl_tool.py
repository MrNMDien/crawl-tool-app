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
            "                            'source_creator': creator, 'last_modify_ts': int(time.time() * 1000)}) + '\\n')",
            "print('creator mode', creator)",
        ]), encoding="utf-8")
        sent = []
        old = ct.BASE_DIR, ct.uv_path, ct.save_settings, ct.telegram_call, ct.download_thumb
        ct.BASE_DIR, ct.uv_path = base, lambda: sys.executable
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

            def run_check():
                assert api.check_follows() == {"ok": True}
                assert api.check_follows()["error"]  # đang bận
                began = time.time()
                while api._state != "idle" and time.time() - began < 20:
                    time.sleep(0.1)

            run_check()  # lần đầu: ghi nhận video 1, 2
            assert api.follows()["channels"][0]["new"] == [] and sent == []
            assert "--type creator" in " ".join(api._lines) and "--crawler_max_notes_count 18" in " ".join(api._lines)
            counter.write_text("1")
            time.sleep(1.1)  # lần sau ghi dòng mới có last_modify_ts mới hơn
            run_check()  # video 3 mới
            follow = api.follows()
            assert follow["channels"][0]["new"] == ["3"]
            assert sent and sent[0][0] == "sendMessage" and sent[0][1]["chat_id"] == "42" and "Tập 3" in sent[0][1]["text"]
            assert api.mark_seen()["channels"][0]["new"] == []
            assert api.set_follow_settings(None, 5, None)["error"] and api.set_follow_settings(False, 12, True)["settings"]["hours"] == 12
            assert api.unfollow(follow["channels"][0]["key"])["channels"] == []
        finally:
            ct.BASE_DIR, ct.uv_path, ct.save_settings, ct.telegram_call, ct.download_thumb = old


def test_login_button_flow():
    """Nút Đăng nhập: chạy base ở chế độ chỉ đăng nhập, rồi chạy lại (ẩn) để xem phiên có được giữ không."""
    with tempfile.TemporaryDirectory() as tmp:
        base, bd = Path(tmp) / "base", Path(tmp) / "bd"
        base.mkdir()
        bd.mkdir()
        mode = Path(tmp) / "mode.txt"  # crawler giả: "ok" | "lost" (mất phiên khi mở lại) | "fail"
        (base / "run").write_text("\n".join([
            "import os, sys, pathlib",
            f"mode = pathlib.Path(r'{mode}').read_text()",
            "headless = sys.argv[sys.argv.index('--headless') + 1] == 'true'",
            "assert os.environ['MEDIACRAWLER_LOGIN_ONLY'] == '1'",
            "print('headless' if headless else 'window')",
            "if mode == 'ok' or (mode == 'lost' and not headless):",
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
                assert api.login_platform("dy", verify_only) == {"ok": True}
                assert api.login_platform("dy", verify_only)["error"]  # đang bận
                began = time.time()
                while api._state != "idle" and time.time() - began < 20:
                    time.sleep(0.1)
                return "\n".join(api._lines)

            mode.write_text("ok")
            log = run(False)
            assert "window" in log and "headless" in log and "phiên đăng nhập được giữ" in log
            assert not (bd / "dy.logged_in").exists()  # bấm Đăng nhập thì bỏ dấu cũ (base ghi lại khi LOGIN_OK)

            mode.write_text("lost")
            log = run(False)
            assert "KHÔNG còn sau khi đóng Chrome" in log

            mode.write_text("fail")
            log = run(False)
            assert "Chưa đăng nhập được" in log and "headless" not in log  # không đăng nhập được thì không kiểm tra lại

            mode.write_text("ok")
            log = run(True)  # Kiểm tra phiên: chỉ chạy ẩn
            assert "window" not in log and "phiên đăng nhập được giữ" in log
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
        {"tag_name": "app-v1.3.0", "draft": True, "assets": [{"name": "CrawlTool.exe", "url": "u-draft"}]},  # nháp: bỏ
        {"tag_name": "app-v1.2.0", "body": f"Sửa dán\n\nsha256: {sha}", "assets": [{"name": "CrawlTool.exe", "url": "u-120", "size": 9}]},
        {"tag_name": "app-v1.10.0", "body": "không có file exe", "assets": []},  # thiếu file: bỏ
        {"tag_name": "v9.9.9", "assets": [{"name": "CrawlTool.exe", "url": "u-khac"}]},  # không phải tag app: bỏ
        {"tag_name": "app-v1.1.5", "assets": [{"name": "CrawlTool.exe", "url": "u-115"}]},
    ]
    old_get = ct.github_get
    ct.github_get = lambda url, token: json.dumps(releases).encode()
    try:
        latest = ct.latest_app_release("t")
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


if __name__ == "__main__":
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
    test_base_update_keeps_login_profiles()
    print("OK")
