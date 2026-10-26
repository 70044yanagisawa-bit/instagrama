"""Google ドライブの受け取りフォルダ（iPhone のショートカットが写真・動画を送る先）を読む。"""

import io
import json
from datetime import datetime, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from .config import env

FIELDS = "nextPageToken, files(id, name, mimeType, createdTime, imageMediaMetadata(time))"


class Drive:
    def __init__(self):
        info = json.loads(env("GOOGLE_SERVICE_ACCOUNT_JSON"))
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        self.service = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.folder_id = env("DRIVE_FOLDER_ID")

    def list_media(self, since: datetime) -> list[dict]:
        """since 以降にアップロードされた写真・動画。taken（撮影日時）を付けて返す。"""
        q = (
            f"'{self.folder_id}' in parents and trashed = false"
            " and (mimeType contains 'image/' or mimeType contains 'video/')"
            f" and createdTime > '{since.astimezone().isoformat()}'"
        )
        files, token = [], None
        while True:
            res = (
                self.service.files()
                .list(q=q, fields=FIELDS, pageSize=1000, pageToken=token, orderBy="createdTime desc")
                .execute()
            )
            files += res.get("files", [])
            token = res.get("nextPageToken")
            if not token:
                break
        for f in files:
            f["taken"] = taken_at(f, since.tzinfo)
        return files

    def download(self, file_id: str) -> bytes:
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, self.service.files().get_media(fileId=file_id), chunksize=32 << 20)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()


def taken_at(f: dict, tz) -> str:
    """写真の EXIF の撮影日時（なければアップロード日時）を ISO 形式で。"""
    exif = (f.get("imageMediaMetadata") or {}).get("time")
    if exif:
        try:
            return datetime.strptime(exif, "%Y:%m:%d %H:%M:%S").replace(tzinfo=tz).isoformat()
        except ValueError:
            pass
    created = datetime.fromisoformat(f["createdTime"].replace("Z", "+00:00"))
    return created.astimezone(tz).isoformat()


def since(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)
