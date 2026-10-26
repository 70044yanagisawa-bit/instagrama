"""外部サービス：Cloudflare Worker（画像置き場・LINE の返事・トークン保管）、LINE、Instagram。"""

import time

import requests

from .config import env

TIMEOUT = 60


class Worker:
    """worker/ の Cloudflare Worker。画像を公開 URL で置き、LINE の OK/NG とトークンを覚えておく。"""

    def __init__(self):
        self.base = env("WORKER_URL").rstrip("/")
        self.headers = {"Authorization": f"Bearer {env('WORKER_TOKEN')}"}

    def put_image(self, key: str, jpeg: bytes) -> str:
        r = requests.put(
            f"{self.base}/media/{key}",
            data=jpeg,
            headers={**self.headers, "Content-Type": "image/jpeg"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return f"{self.base}/media/{key}"

    def set_pending(self, post_id: str) -> None:
        requests.post(f"{self.base}/pending/{post_id}", headers=self.headers, timeout=TIMEOUT).raise_for_status()

    def approval(self, post_id: str) -> str:
        r = requests.get(f"{self.base}/approval/{post_id}", headers=self.headers, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json().get("status", "pending")

    def close_approval(self, post_id: str) -> None:
        requests.delete(f"{self.base}/approval/{post_id}", headers=self.headers, timeout=TIMEOUT).raise_for_status()

    def get_value(self, key: str) -> str | None:
        r = requests.get(f"{self.base}/kv/{key}", headers=self.headers, timeout=TIMEOUT)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.text

    def put_value(self, key: str, value: str) -> None:
        requests.put(f"{self.base}/kv/{key}", data=value.encode(), headers=self.headers, timeout=TIMEOUT).raise_for_status()


class Line:
    def __init__(self):
        self.headers = {"Authorization": f"Bearer {env('LINE_CHANNEL_ACCESS_TOKEN')}"}
        self.to = env("LINE_USER_ID")

    def push(self, messages: list[dict]) -> None:
        r = requests.post(
            "https://api.line.me/v2/bot/message/push",
            json={"to": self.to, "messages": messages},
            headers=self.headers,
            timeout=TIMEOUT,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"LINE 送信に失敗: {r.status_code} {r.text}")

    def text(self, text: str) -> None:
        self.push([{"type": "text", "text": text[:5000]}])

    def ask_approval(self, post_id: str, image_url: str, caption: str, post_time: str) -> None:
        self.push(
            [
                {"type": "image", "originalContentUrl": image_url, "previewImageUrl": image_url},
                {"type": "text", "text": f"キャプション：\n{caption}"[:5000]},
                {
                    "type": "template",
                    "altText": f"今日の投稿の確認（{post_time} に投稿）",
                    "template": {
                        "type": "buttons",
                        "text": f"この内容で {post_time} に投稿していい？（返事がなければ投稿しません）",
                        "actions": [
                            {"type": "postback", "label": "OK", "data": f"approve:{post_id}", "displayText": "OK"},
                            {"type": "postback", "label": "NG", "data": f"reject:{post_id}", "displayText": "NG"},
                        ],
                    },
                },
            ]
        )


class Instagram:
    """Instagram API（Instagram ログイン版。Facebook ページの連携は不要）。"""

    API = "https://graph.instagram.com/v23.0"

    def __init__(self, token: str):
        self.token = token
        self.user = env("IG_USER_ID", required=False) or "me"

    def _call(self, method: str, path: str, **params) -> dict:
        r = requests.request(method, f"{self.API}/{path}", params={**params, "access_token": self.token}, timeout=TIMEOUT)
        data = r.json()
        if r.status_code >= 400 or "error" in data:
            raise RuntimeError(f"Instagram API エラー: {data.get('error', data)}")
        return data

    def publish(self, image_url: str, caption: str) -> str:
        container = self._call("POST", f"{self.user}/media", image_url=image_url, caption=caption)["id"]
        for _ in range(30):
            status = self._call("GET", container, fields="status_code").get("status_code")
            if status == "FINISHED":
                break
            if status in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Instagram が画像を受け付けませんでした（{status}）")
            time.sleep(5)
        media_id = self._call("POST", f"{self.user}/media_publish", creation_id=container)["id"]
        return media_id

    def permalink(self, media_id: str) -> str:
        return self._call("GET", media_id, fields="permalink").get("permalink", "")

    @staticmethod
    def refresh(token: str) -> str:
        r = requests.get(
            "https://graph.instagram.com/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": token},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["access_token"]
