import io
import subprocess
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from autopost import main as mainmod
from autopost import media
from autopost import state as st
from autopost.ai import parse_judgement
from autopost.config import Config, read_prompt

TZ = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 10, 26, 17, 30, tzinfo=TZ)


def cfg(until="2026-11-23"):
    c = Config.load()
    c.raw["approval"]["until"] = until
    return c


def jpeg(color="red", size=(600, 800)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


# ---- 設定・状態 ----

def test_approval_period():
    c = cfg("2026-11-23")
    assert c.approval_required(date(2026, 11, 23))
    assert not c.approval_required(date(2026, 11, 24))
    assert cfg("always").approval_required(date(2030, 1, 1))
    assert not cfg("never").approval_required(date(2026, 1, 1))


def test_caption_and_prompt():
    assert "#3" in cfg().caption(date(2026, 10, 26), 3)
    p = read_prompt("edit.md")
    assert "<!--" not in p and "顔" in p


def test_choose_prefers_fresh_then_stock(tmp_path):
    s = st.State({}, tmp_path / "s.json")
    s.media.update(
        {
            "old_great": {"status": st.CANDIDATE, "score": 10, "taken": (NOW - timedelta(days=5)).isoformat()},
            "new_ok": {"status": st.CANDIDATE, "score": 7, "taken": (NOW - timedelta(hours=3)).isoformat()},
            "new_low": {"status": st.CANDIDATE, "score": 4, "taken": NOW.isoformat()},
            "posted": {"status": st.POSTED, "score": 10, "taken": NOW.isoformat()},
        }
    )
    assert s.choose(NOW, 36, 6) == "new_ok"
    s.media["new_ok"]["status"] = st.POSTED
    assert s.choose(NOW, 36, 6) == "old_great"  # 今日の素材がなければ過去のストック
    s.media["old_great"]["status"] = st.REJECTED
    assert s.choose(NOW, 36, 6) is None


def test_parse_judgement_clamps():
    j = parse_judgement('{"usable": true, "score": 14, "best_frame": 9, "reason": "良い"}', 6)
    assert j == {"usable": True, "score": 10, "best_frame": 6, "reason": "良い"}


# ---- 画像・動画 ----

def test_video_frames_and_sheet(tmp_path):
    video = tmp_path / "v.mp4"
    subprocess.run(
        [media.FFMPEG, "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-t", "3", "-pix_fmt", "yuv420p", str(video)],
        check=True,
        capture_output=True,
    )
    times, frames = media.video_frames(video.read_bytes(), 6)
    assert len(frames) == 6 and times[0] > 0 and times[-1] < 3
    sheet = media.contact_sheet(frames)
    assert sheet.size == (3 * 384, 2 * 384)


def test_is_video_sniffs_untyped_upload():
    mov = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 20
    heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 20
    assert media.is_video("application/octet-stream", mov)
    assert not media.is_video("application/octet-stream", heic)
    assert media.is_video("", b"", "IMG_0001.MOV")
    assert not media.is_video("image/jpeg", mov)


def test_crop_to_4x5():
    out = media.crop_to(media.open_image(jpeg(size=(1024, 1536))), 1080, 1350)
    assert out.size == (1080, 1350)


# ---- 一連の流れ（外部サービスは偽物） ----

class FakeInbox:
    def __init__(self, files):
        self.files = files
        self.deleted = []

    def delete(self, fid):
        self.deleted.append(fid)

    def list_media(self, since):
        return [f for f in self.files if f["taken"] >= since.isoformat()]

    def download(self, fid):
        return jpeg()


class FakeAI:
    def __init__(self, scores):
        self.scores = scores  # 素材の色 → 点数（判定は呼ばれた順）
        self.calls = 0
        self.fail_edit = False

    def judge(self, png, frames):
        score = self.scores[self.calls]
        self.calls += 1
        return {"usable": score > 0, "score": score, "best_frame": 1, "reason": "test"}

    def edit(self, png):
        if self.fail_edit:
            raise RuntimeError("moderation")
        return media.to_png(Image.new("RGB", (1088, 1360), "blue"))


class FakeWorker:
    def __init__(self):
        self.images, self.kv, self.approvals = {}, {}, {}

    def put_image(self, key, data):
        self.images[key] = data
        return f"https://w.example/media/{key}"

    def set_pending(self, pid):
        self.approvals[pid] = "pending"

    def approval(self, pid):
        return self.approvals.get(pid, "none")

    def close_approval(self, pid):
        self.approvals.pop(pid, None)

    def get_value(self, k):
        return self.kv.get(k)

    def put_value(self, k, v):
        self.kv[k] = v


class FakeLine:
    def __init__(self):
        self.sent = []

    def text(self, t):
        self.sent.append(("text", t))

    def ask_approval(self, pid, url, caption, post_time):
        self.sent.append(("ask", pid))


class FakeIG:
    published = []

    def __init__(self, token=None):
        pass

    def publish(self, url, caption):
        FakeIG.published.append((url, caption))
        return "ig1"

    def permalink(self, mid):
        return "https://instagram.com/p/x"


def make_app(tmp_path, c, files, scores):
    app = object.__new__(mainmod.App)
    app.cfg, app.state = c, st.State({}, tmp_path / "s.json")
    app.inbox, app.ai, app.worker, app.line = FakeInbox(files), FakeAI(scores), FakeWorker(), FakeLine()
    app.instagram = lambda: FakeIG()
    return app


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(mainmod, "wait_until", lambda t: None)
    FakeIG.published = []


def files():
    return [
        {"id": "a", "name": "gym.jpg", "mimeType": "image/jpeg", "taken": (NOW - timedelta(hours=2)).isoformat()},
        {"id": "b", "name": "cat.jpg", "mimeType": "image/jpeg", "taken": (NOW - timedelta(hours=1)).isoformat()},
    ]


def test_approval_flow_approved(tmp_path):
    app = make_app(tmp_path, cfg(), files(), [8, 0])
    post = app.prepare(NOW, ask=True)
    assert post["status"] == st.PENDING and post["media_id"] == "a"
    assert app.state.media["b"]["status"] == st.UNUSABLE
    assert ("ask", "2026-10-26") in app.line.sent

    app.worker.approvals["2026-10-26"] = "approved"
    app.publish(NOW.replace(hour=19, minute=45))
    assert FakeIG.published and app.state.post_for("2026-10-26")["status"] == st.DONE
    assert app.state.media["a"]["status"] == st.POSTED
    assert "2026-10-26" not in app.worker.approvals  # 締め切られている
    assert app.inbox.deleted == ["b", "a"]  # 関係ない写真と投稿済みの写真は預かり場所から消す


def test_approval_flow_no_reply_keeps_media(tmp_path):
    app = make_app(tmp_path, cfg(), files(), [8, 0])
    app.prepare(NOW, ask=True)
    app.publish(NOW.replace(hour=19, minute=45))
    assert not FakeIG.published
    assert app.state.post_for("2026-10-26")["status"] == st.EXPIRED
    assert app.state.media["a"]["status"] == st.CANDIDATE


def test_approval_flow_rejected(tmp_path):
    app = make_app(tmp_path, cfg(), files(), [8, 0])
    app.prepare(NOW, ask=True)
    app.worker.approvals["2026-10-26"] = "rejected"
    app.publish(NOW.replace(hour=19, minute=45))
    assert not FakeIG.published
    assert app.state.media["a"]["status"] == st.REJECTED


def test_auto_mode_prepares_and_posts(tmp_path):
    app = make_app(tmp_path, cfg("never"), files(), [8, 9])
    app.publish(NOW.replace(hour=19, minute=45))
    assert len(FakeIG.published) == 1
    assert app.state.post_for("2026-10-26")["media_id"] == "b"  # 点数の高い方
    assert not any(kind == "ask" for kind, _ in app.line.sent)


def test_no_material_notifies(tmp_path):
    app = make_app(tmp_path, cfg(), files(), [0, 0])
    assert app.prepare(NOW, ask=True) is None
    assert "見つからなかった" in app.line.sent[-1][1]


def test_edit_failure_gives_up_after_marking(tmp_path):
    app = make_app(tmp_path, cfg(), files(), [8, 7])
    app.ai.fail_edit = True
    assert app.prepare(NOW, ask=True) is None
    assert all(m["status"] == st.UNUSABLE for m in app.state.media.values())
    assert "編集に失敗" in app.line.sent[-1][1]


def test_heic_photo_opens():
    import pillow_heif

    heif = pillow_heif.from_pillow(Image.new("RGB", (64, 48), "green"))
    buf = io.BytesIO()
    heif.save(buf, quality=80)
    assert media.open_image(buf.getvalue()).size == (64, 48)
