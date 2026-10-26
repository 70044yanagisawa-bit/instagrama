"""画像の読み込み（HEIC 対応）、動画からのコマ切り出し、AI に見せる一覧画像、4:5 への切り抜き。"""

import io
import re
import subprocess
import tempfile
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


HEIF_BRANDS = (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1", b"avif")
VIDEO_EXT = (".mov", ".mp4", ".m4v", ".3gp")


def is_video(mime: str, data: bytes = b"", name: str = "") -> bool:
    """動画かどうか。ショートカットが種類を付けずに送ることがあるので、名前と中身も見る。"""
    if mime.startswith("video/"):
        return True
    if mime.startswith("image/"):
        return False
    if name.lower().endswith(VIDEO_EXT):
        return True
    # MOV/MP4 と HEIC はどちらも先頭に ftyp があり、続く4文字で見分ける
    return data[4:8] == b"ftyp" and data[8:12] not in HEIF_BRANDS


def source(data: bytes, mime: str, frame_time: float | None) -> Image.Image:
    """投稿に使う元画像（動画なら選ばれたコマ）。"""
    if is_video(mime, data):
        return video_frame(data, frame_time or 0)
    return open_image(data)


def open_image(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    return ImageOps.exif_transpose(img).convert("RGB")


def video_duration(path: Path) -> float:
    out = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    if not m:
        raise ValueError("動画の長さが読めません")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def frame_at(path: Path, seconds: float) -> Image.Image:
    out = subprocess.run(
        [FFMPEG, "-ss", f"{seconds:.2f}", "-i", str(path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True,
        check=True,
    ).stdout
    return open_image(out)


def frame_times(duration: float, count: int) -> list[float]:
    """動画の頭と終わりを少し避けて、均等にコマの時刻を取る。"""
    return [round(duration * (i + 0.5) / count, 2) for i in range(count)]


def with_video_file(data: bytes, fn):
    with tempfile.NamedTemporaryFile(suffix=".mov") as f:
        f.write(data)
        f.flush()
        return fn(Path(f.name))


def video_frames(data: bytes, count: int) -> tuple[list[float], list[Image.Image]]:
    def run(path):
        times = frame_times(video_duration(path), count)
        return times, [frame_at(path, t) for t in times]

    return with_video_file(data, run)


def video_frame(data: bytes, seconds: float) -> Image.Image:
    return with_video_file(data, lambda path: frame_at(path, seconds))


def contact_sheet(frames: list[Image.Image], cell: int = 384) -> Image.Image:
    """コマを番号付きで並べた1枚（AI に一度に見せる用）。"""
    cols = 3 if len(frames) > 4 else 2 if len(frames) > 1 else 1
    rows = -(-len(frames) // cols)
    sheet = Image.new("RGB", (cols * cell, rows * cell), "white")
    draw = ImageDraw.Draw(sheet)
    for i, fr in enumerate(frames):
        thumb = ImageOps.contain(fr, (cell - 8, cell - 8))
        x, y = (i % cols) * cell, (i // cols) * cell
        sheet.paste(thumb, (x + (cell - thumb.width) // 2, y + (cell - thumb.height) // 2))
        draw.rectangle((x + 4, y + 4, x + 44, y + 40), fill="black")
        draw.text((x + 14, y + 10), str(i + 1), fill="white", font_size=24)
    return sheet


def thumbnail(img: Image.Image, size: int = 768) -> Image.Image:
    return ImageOps.contain(img, (size, size))


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def to_jpeg(img: Image.Image, quality: int = 92) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def fit_for_edit(img: Image.Image, max_side: int = 1536) -> Image.Image:
    return ImageOps.contain(img, (max_side, max_side))


def crop_to(img: Image.Image, width: int, height: int) -> Image.Image:
    """中央で切り抜いて width×height にする（Instagram の縦長 4:5 など）。"""
    return ImageOps.fit(img, (width, height), Image.LANCZOS, centering=(0.5, 0.5))
