"""OpenAI：素材を見て投稿に使えるか採点する／編集の型どおりに画像を仕上げる。"""

import base64
import json

from openai import OpenAI

from .config import Config, env, read_prompt

JUDGE_FORMAT = """
次の JSON だけで答えてください：
{"usable": true または false, "score": 0〜10 の整数, "best_frame": 一番良いコマの番号（1枚だけなら 1）, "reason": "理由を短く"}
"""


class AI:
    def __init__(self, cfg: Config):
        self.cfg = cfg.section("openai")
        self.client = OpenAI(api_key=env("OPENAI_API_KEY"))

    def judge(self, sheet_png: bytes, frames: int) -> dict:
        b64 = base64.b64encode(sheet_png).decode()
        prompt = read_prompt("select.md") + f"\n\nコマ数：{frames}\n" + JUDGE_FORMAT
        res = self.client.chat.completions.create(
            model=self.cfg.get("vision_model", "gpt-5.4-mini"),
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"}},
                    ],
                }
            ],
        )
        return parse_judgement(res.choices[0].message.content, frames)

    def edit(self, png: bytes) -> bytes:
        kwargs = {}
        if self.cfg.get("input_fidelity_high", False):
            kwargs["input_fidelity"] = "high"
        res = self.client.images.edit(
            model=self.cfg.get("image_model", "gpt-image-2"),
            image=("photo.png", png, "image/png"),
            prompt=read_prompt("edit.md"),
            size=self.cfg.get("image_size", "1088x1360"),
            quality=self.cfg.get("image_quality", "high"),
            **kwargs,
        )
        return base64.b64decode(res.data[0].b64_json)


def parse_judgement(text: str, frames: int) -> dict:
    data = json.loads(text)
    best = int(data.get("best_frame") or 1)
    return {
        "usable": bool(data.get("usable")),
        "score": max(0, min(10, int(data.get("score") or 0))),
        "best_frame": min(max(best, 1), frames),
        "reason": str(data.get("reason", ""))[:200],
    }
