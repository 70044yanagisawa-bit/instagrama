"""コマンド：
  python -m autopost prepare   素材を選んで編集し、（確認期間中は）LINE に確認を送る（17:30 ごろ）
  python -m autopost publish   20:00 に Instagram へ投稿する
  python -m autopost try 写真のパス   編集の型（prompts/edit.md）を手元で試す。out/ に保存
"""

import argparse
import hashlib
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from . import media
from . import state as st
from .config import ROOT, Config, env

MAX_NEW_PER_RUN = 200
MAX_EDIT_TRIES = 3
MAX_WAIT_MINUTES = 40
TOKEN_REFRESH_DAYS = 7


def log(*args):
    print(*args, flush=True)


class App:
    def __init__(self, cfg: Config, state: st.State):
        from .ai import AI
        from .drive import Drive
        from .services import Line, Worker

        self.cfg, self.state = cfg, state
        self.drive, self.ai, self.worker, self.line = Drive(), AI(cfg), Worker(), Line()

    # ---- 素材を AI に見てもらう ----
    def scan(self, now: datetime) -> None:
        sel = self.cfg.section("selection")
        since = now - timedelta(days=sel.get("lookback_days", 30))
        new = [f for f in self.drive.list_media(since) if f["id"] not in self.state.media]
        log(f"新しい素材：{len(new)} 件")
        for f in new[:MAX_NEW_PER_RUN]:
            try:
                self.state.media[f["id"]] = self.judge(f, sel)
                m = self.state.media[f["id"]]
                log(f"  {f['name']}: {m['status']} {m.get('score')} {m.get('reason', '')}")
            except Exception as e:  # 1件の失敗で全体を止めない（次回また試す）
                log(f"  {f['name']}: 読み込み失敗 {e}")
            self.state.save()

    def judge(self, f: dict, sel: dict) -> dict:
        data = self.drive.download(f["id"])
        if media.is_video(f["mimeType"]):
            times, frames = media.video_frames(data, sel.get("video_frames", 6))
        else:
            times, frames = [None], [media.open_image(data)]
        result = self.ai.judge(media.to_png(media.contact_sheet(frames)), len(frames))
        frame_time = times[result["best_frame"] - 1]
        ok = result["usable"] and result["score"] >= sel.get("min_score", 6)
        return {
            "name": f["name"],
            "mime": f["mimeType"],
            "taken": f["taken"],
            "status": st.CANDIDATE if ok else st.UNUSABLE,
            "score": result["score"],
            "frame_time": frame_time,
            "reason": result["reason"],
        }

    # ---- 選んで編集する ----
    def prepare(self, now: datetime, ask: bool) -> dict | None:
        day_id = now.date().isoformat()
        existing = self.state.post_for(day_id)
        if existing and existing["status"] in (st.PENDING, st.READY, st.DONE):
            log(f"{day_id} の投稿はもう準備済み（{existing['status']}）")
            return existing

        self.scan(now)
        sel = self.cfg.section("selection")
        for attempt in range(MAX_EDIT_TRIES):
            mid = self.state.choose(now, sel.get("fresh_hours", 36), sel.get("min_score", 6))
            if not mid:
                reason = "画像の編集に失敗し、ほかに使える素材もなかった" if attempt else "投稿に使える筋トレの写真・動画が見つからなかった"
                self.line.text(f"今日は{reason}ため、投稿をお休みします。")
                return None
            m = self.state.media[mid]
            try:
                url = self.make_image(mid, m, day_id)
                break
            except Exception as e:  # AI が編集を断った等。その素材は使わず次の候補へ
                log(f"{m['name']} の編集に失敗：{e}")
                m["status"], m["reason"] = st.UNUSABLE, f"編集失敗: {e}"[:200]
                self.state.save()
        else:
            self.line.text("画像の編集が続けて失敗したため、今日の投稿はお休みします。GitHub Actions のログを確認してください。")
            return None

        post = {
            "id": day_id,
            "media_id": mid,
            "image_url": url,
            "caption": self.cfg.caption(now.date(), self.state.posted_count() + 1),
            "status": st.PENDING if ask else st.READY,
            "created": now.isoformat(),
        }
        self.state.posts.append(post)
        self.state.save()
        if ask:
            self.worker.set_pending(day_id)
            post_time = self.cfg.post_time(now.date()).strftime("%H:%M")
            self.line.ask_approval(day_id, url, post["caption"], post_time)
        log(f"準備完了：{m['name']} → {url}")
        return post

    def make_image(self, mid: str, m: dict, day_id: str) -> str:
        from .ai import source_image

        src = source_image(self.drive, {"id": mid, "mimeType": m["mime"]}, m.get("frame_time"))
        jpeg = edit_to_jpeg(self.ai, self.cfg, src)
        key = f"{day_id}-{hashlib.sha256(mid.encode()).hexdigest()[:10]}.jpg"
        return self.worker.put_image(key, jpeg)

    # ---- 投稿する ----
    def publish(self, now: datetime) -> None:
        day_id = now.date().isoformat()
        ask = self.cfg.approval_required(now.date())
        post = self.state.post_for(day_id)

        if not ask and (post is None or post["status"] in (st.FAILED,)):
            post = self.prepare(now, ask=False)
        if post is None:
            log("今日の投稿はありません")
            return

        if post["status"] == st.PENDING:
            wait_until(self.cfg.post_time(now.date()))  # 20:00 ギリギリまで返事を待つ
            answer = self.worker.approval(day_id)
            self.worker.close_approval(day_id)
            if answer == "rejected":
                post["status"] = st.DECLINED
                self.state.media[post["media_id"]]["status"] = st.REJECTED
                self.state.save()
                log("LINE で NG だったので投稿しません")
                return
            if answer != "approved":
                post["status"] = st.EXPIRED
                self.state.save()
                self.line.text("今日の確認に返事がなかったので、投稿しませんでした。この素材は次回以降の候補に残しておきます。")
                return
        elif post["status"] != st.READY:
            log(f"今日の投稿は {post['status']} のため何もしません")
            return

        wait_until(self.cfg.post_time(now.date()))
        try:
            ig = self.instagram()
            media_id = ig.publish(post["image_url"], post["caption"])
            link = ig.permalink(media_id)
        except Exception:
            post["status"] = st.FAILED
            self.state.save()
            self.line.text("Instagram への投稿に失敗しました。GitHub Actions のログを確認してください。")
            raise
        post.update(status=st.DONE, ig_media_id=media_id, permalink=link, posted=datetime.now(now.tzinfo).isoformat())
        self.state.media[post["media_id"]]["status"] = st.POSTED
        self.state.save()
        self.line.text(f"Instagram に投稿しました ✅\n{link}")

    def instagram(self):
        """トークンは Worker に保管して 1 週間ごとに延長する（60 日で切れるため）。"""
        from .services import Instagram

        secret = env("IG_ACCESS_TOKEN")
        seed = hashlib.sha256(secret.encode()).hexdigest()[:16]
        token = self.worker.get_value("ig_token")
        if not token or self.worker.get_value("ig_token_seed") != seed:
            # 初回、または Secrets のトークンを入れ直したとき
            token = secret
            self.worker.put_value("ig_token", token)
            self.worker.put_value("ig_token_seed", seed)
            self.worker.put_value("ig_token_refreshed", datetime.now().isoformat())
        last = self.worker.get_value("ig_token_refreshed")
        if not last or datetime.now() - datetime.fromisoformat(last) > timedelta(days=TOKEN_REFRESH_DAYS):
            try:
                token = Instagram.refresh(token)
                self.worker.put_value("ig_token", token)
                self.worker.put_value("ig_token_refreshed", datetime.now().isoformat())
                log("Instagram のトークンを延長しました")
            except Exception as e:
                log(f"トークンの延長に失敗（今のトークンで続行）：{e}")
        return Instagram(token)


def edit_to_jpeg(ai, cfg: Config, src) -> bytes:
    out = cfg.section("output")
    edited = media.open_image(ai.edit(media.to_png(media.fit_for_edit(src))))
    return media.to_jpeg(media.crop_to(edited, out.get("width", 1080), out.get("height", 1350)))


def wait_until(target: datetime) -> None:
    """GitHub Actions は少し早めに起動するので、投稿時刻まで待つ。"""
    seconds = (target - datetime.now(target.tzinfo)).total_seconds()
    if seconds <= 0:
        return
    if seconds > MAX_WAIT_MINUTES * 60:
        raise RuntimeError(f"投稿時刻 {target:%H:%M} まで {seconds / 60:.0f} 分あります。時刻の設定を確認してください。")
    log(f"{target:%H:%M} まで {seconds / 60:.1f} 分待ちます")
    time.sleep(seconds)


def try_edit(path: str) -> None:
    from .ai import AI

    cfg = Config.load()
    src = Path(path)
    data = src.read_bytes()
    img = media.video_frame(data, 0) if src.suffix.lower() in (".mov", ".mp4", ".m4v") else media.open_image(data)
    out_dir = ROOT / "out"
    out_dir.mkdir(exist_ok=True)
    dest = out_dir / f"{src.stem}-edited.jpg"
    dest.write_bytes(edit_to_jpeg(AI(cfg), cfg, img))
    log(f"保存しました：{dest}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="autopost")
    p.add_argument("command", choices=["prepare", "publish", "try"])
    p.add_argument("path", nargs="?")
    p.add_argument("--force", action="store_true", help="確認期間外でも prepare する")
    args = p.parse_args(argv)

    if args.command == "try":
        if not args.path:
            p.error("try には写真のパスが必要です")
        try_edit(args.path)
        return 0

    cfg = Config.load()
    state = st.State.load()
    now = cfg.now()
    app = App(cfg, state)
    try:
        if args.command == "prepare":
            if cfg.approval_required(now.date()) or args.force:
                app.prepare(now, ask=cfg.approval_required(now.date()))
            else:
                log("確認期間は終わっているので、準備は 20:00 の投稿と一緒に行います")
        else:
            app.publish(now)
    except Exception:
        traceback.print_exc()
        state.save()
        return 1
    state.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
