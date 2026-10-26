"""state/state.json：どの素材を AI が見たか・投稿したか、日ごとの投稿の状態を記録する。

GitHub Actions が実行のたびにこのファイルをリポジトリへコミットして持ち越す。
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from .config import ROOT

STATE_PATH = ROOT / "state" / "state.json"

# media[id]["status"]
CANDIDATE = "candidate"  # 投稿に使える（まだ使っていない）
POSTED = "posted"
REJECTED = "rejected"  # LINE で NG にした
UNUSABLE = "unusable"  # 筋トレと関係ない・点数が低い

# posts[i]["status"]
PENDING = "pending"  # LINE で確認待ち
READY = "ready"  # 確認なしモードで、投稿待ち
DONE = "posted"
DECLINED = "rejected"
EXPIRED = "expired"  # 返事がなかった
FAILED = "failed"


class State:
    def __init__(self, data: dict, path: Path = STATE_PATH):
        self.data = data
        self.path = path
        data.setdefault("media", {})
        data.setdefault("posts", [])

    @classmethod
    def load(cls, path: Path = STATE_PATH) -> "State":
        if path.exists():
            return cls(json.loads(path.read_text(encoding="utf-8")), path)
        return cls({}, path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @property
    def media(self) -> dict:
        return self.data["media"]

    @property
    def posts(self) -> list:
        return self.data["posts"]

    def post_for(self, day_id: str) -> dict | None:
        for p in reversed(self.posts):
            if p["id"] == day_id:
                return p
        return None

    def posted_count(self) -> int:
        return sum(1 for p in self.posts if p["status"] == DONE)

    def choose(self, now: datetime, fresh_hours: float, min_score: float) -> str | None:
        """投稿する素材を1つ選ぶ。最近撮ったものを優先し、なければ過去のストックから。"""
        usable = [
            (mid, m)
            for mid, m in self.media.items()
            if m.get("status") == CANDIDATE and m.get("score", 0) >= min_score
        ]
        if not usable:
            return None
        cutoff = now - timedelta(hours=fresh_hours)

        def taken(m):
            return datetime.fromisoformat(m["taken"])

        fresh = [x for x in usable if taken(x[1]) >= cutoff]
        pool = fresh or usable
        pool.sort(key=lambda x: (x[1]["score"], taken(x[1])), reverse=True)
        return pool[0][0]
