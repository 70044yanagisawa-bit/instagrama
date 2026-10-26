"""config.yaml・prompts/・環境変数（GitHub の Secrets）の読み込み。"""

import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent


def read_prompt(name: str) -> str:
    text = (ROOT / "prompts" / name).read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()


@dataclass
class Config:
    raw: dict

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
            return cls(yaml.safe_load(f))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.raw.get("timezone", "Asia/Tokyo"))

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def post_time(self, day: date) -> datetime:
        h, m = (int(x) for x in str(self.raw.get("post_time", "20:00")).split(":"))
        return datetime(day.year, day.month, day.day, h, m, tzinfo=self.tz)

    def approval_required(self, day: date) -> bool:
        until = str(self.raw.get("approval", {}).get("until", "always"))
        if until == "always":
            return True
        if until == "never":
            return False
        return day <= date.fromisoformat(until)

    def section(self, name: str) -> dict:
        return self.raw.get(name, {})

    def caption(self, day: date, n: int) -> str:
        return self.raw["caption"].strip().format(date=f"{day.month}/{day.day}", n=n)


def env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "")
    if required and not value:
        raise RuntimeError(f"環境変数 {name} が設定されていません（GitHub の Settings → Secrets を確認）")
    return value
