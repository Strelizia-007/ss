"""
media_utils.py — FFmpeg processing, MediaInfo, GDrive download, 18+ detection
"""

import os, re, asyncio, subprocess, tempfile, math, json, logging
from pathlib import Path
from typing import Optional
import httpx
from config import ADULT_KEYWORDS

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  18+ DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def is_adult_content(filename: str, caption: str = "") -> bool:
    text = (filename + " " + caption).lower()
    return any(kw in text for kw in ADULT_KEYWORDS)


# ══════════════════════════════════════════════════════════════════════════════
#  LINK DETECTION & DOWNLOAD
# ══════════════════════════════════════════════════════════════════════════════

GDRIVE_FILE_RE  = re.compile(r"drive\.google\.com/file/d/([^/\s?]+)")
GDRIVE_FOLDER_RE= re.compile(r"drive\.google\.com/drive/folders/([^/\s?]+)")
DIRECT_LINK_RE  = re.compile(r"https?://[^\s]+\.(mp4|mkv|avi|mov|webm|ts|m2ts|flv)", re.IGNORECASE)


def detect_link_type(text: str) -> tuple[str, str]:
    if m := GDRIVE_FILE_RE.search(text):  return "gdrive_file",   m.group(1)
    if m := GDRIVE_FOLDER_RE.search(text):return "gdrive_folder", m.group(1)
    if m := DIRECT_LINK_RE.search(text):  return "direct",        m.group(0)
    if text.startswith("http"):           return "unsupported",   text
    return "none", ""


async def download_direct_link(url: str, dest: str) -> bool:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=300) as client:
            async with client.stream("GET", url) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in r.aiter_bytes(512 * 1024):
                        f.write(chunk)
        return True
    except Exception as e:
        logger.error(f"Direct download failed: {e}")
        return False


async def download_gdrive_file(file_id: str, dest: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "gdown", f"https://drive.google.com/uc?id={file_id}", "-O", dest, "--fuzzy",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error(f"gdown error: {stderr.decode()}")
            return False
        return True
    except Exception as e:
        logger.error(f"GDrive download failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  FFPROBE  (accepts local path or HTTPS URL)
# ══════════════════════════════════════════════════════════════════════════════

def run_ffprobe(path: str) -> Optional[dict]:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams",
        "-timeout", "30000000",
        path
    ]
    try:
        out = subprocess.check_output(cmd, timeout=60)
        return json.loads(out)
    except Exception as e:
        logger.error(f"ffprobe error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  MEDIA INFO
# ══════════════════════════════════════════════════════════════════════════════

def parse_simple_mediainfo(path: str) -> str:
    data = run_ffprobe(path)
    if not data:
        return "❌ Could not read media info."
    fmt      = data.get("format", {})
    streams  = data.get("streams", [])
    duration = float(fmt.get("duration", 0))
    h, rem   = divmod(int(duration), 3600)
    m, s     = divmod(rem, 60)
    dur_str  = f"{h:02d}:{m:02d}:{s:02d}"
    video    = next((s for s in streams if s.get("codec_type") == "video"), None)
    audios   = [s for s in streams if s.get("codec_type") == "audio"]
    subs     = [s for s in streams if s.get("codec_type") == "subtitle"]
    quality  = "Unknown"
    codec    = ""
    bit_depth= ""
    if video:
        h_px = video.get("height", 0)
        for res, label in [(2160,"2160p"),(1440,"1440p"),(1080,"1080p"),(720,"720p"),(480,"480p")]:
            if h_px >= res:
                quality = label; break
        cname     = video.get("codec_name","").upper()
        codec_map = {"HEVC":"HEVC","H265":"HEVC","H264":"H264","AVC":"H264","AV1":"AV1","VP9":"VP9"}
        codec     = codec_map.get(cname, cname)
        bit_depth = " 10bit" if "10" in video.get("pix_fmt","") else " 8bit"
    langs     = list({a.get("tags",{}).get("language","und") for a in audios})
    sub_langs = list({s.get("tags",{}).get("language","und") for s in subs})
    size_mb   = round(int(fmt.get("size", 0)) / 1024 / 1024, 1)
    return "\n".join([
        f"🎬 **Quality:** `{quality} {codec}{bit_depth}`",
        f"🌐 **Language:** `{', '.join(langs) or 'N/A'}`",
        f"📝 **Subtitle:** `{', '.join(sub_langs) or 'None'}`",
        f"⏱ **Duration:** `{dur_str}`",
        f"💾 **Size:** `{size_mb} MB`",
    ])


def parse_detailed_mediainfo(path: str) -> str:
    try:
        out = subprocess.check_output(["mediainfo", path], timeout=60)
        return out.decode("utf-8", errors="replace")
    except FileNotFoundError:
        data = run_ffprobe(path)
        return json.dumps(data, indent=2) if data else "mediainfo not available"
    except Exception as e:
        return f"Error: {e}"


# ══════════════════════════════════════════════════════════════════════════════
#  TELEGRAPH UPLOAD
# ══════════════════════════════════════════════════════════════════════════════

async def upload_to_telegraph(title: str, content: str, token: str = "") -> Optional[str]:
    node    = [{"tag": "pre", "children": [content]}]
    payload = {"title": title, "author_name": "ScreenshotBot", "content": json.dumps(node)}
    base    = "https://api.telegra.ph"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if not token:
                acc   = await client.post(f"{base}/createAccount",
                            data={"short_name":"ScreenBot","author_name":"ScreenBot"})
                token = acc.json()["result"]["access_token"]
            payload["access_token"] = token
            r      = await client.post(f"{base}/createPage", data=payload)
            result = r.json()
            if result.get("ok"):
                return "https://telegra.ph" + result["result"]["path"]
    except Exception as e:
        logger.error(f"Telegraph upload error: {e}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  SCREENSHOTS  — parallel fast-seek, all frames in one ffmpeg call
# ══════════════════════════════════════════════════════════════════════════════

async def get_duration(input_path: str) -> float:
    """Get video duration via ffprobe. Works with URLs."""
    data = run_ffprobe(input_path)
    if not data:
        return 0.0
    return float(data.get("format", {}).get("duration", 0))


async def _grab_one_frame(input_path: str, ts: float, out: str) -> Optional[str]:
    """Extract a single frame at timestamp ts. Fast: -ss BEFORE -i for keyframe seek."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{ts:.3f}",        # seek BEFORE input = fast keyframe seek
        "-i", input_path,
        "-vframes", "1",
        "-q:v", "3",               # JPEG quality 3 (good balance)
        "-vf", "scale=1280:-2",    # cap width at 1280px, keep aspect
        out
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    return out if os.path.exists(out) and os.path.getsize(out) > 0 else None


async def generate_screenshots(
    input_path: str,
    count: int,
    output_dir: str,
    mode: str = "equally_spaced"
) -> list[str]:
    """
    Generate `count` screenshots in PARALLEL.
    All ffmpeg processes run concurrently — total time ≈ single frame time.
    """
    duration = await get_duration(input_path)
    if duration <= 0:
        return []

    # Equally spaced, skip first/last 2% to avoid black frames
    margin     = duration * 0.02
    span       = duration - 2 * margin
    timestamps = [margin + span * (i / max(count - 1, 1)) for i in range(count)]

    # Launch all frame grabs concurrently
    tasks = [
        _grab_one_frame(input_path, ts, os.path.join(output_dir, f"scht_{i:03d}.jpg"))
        for i, ts in enumerate(timestamps)
    ]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r]


# ══════════════════════════════════════════════════════════════════════════════
#  TILE IMAGE  — Pillow montage (fast, no ffmpeg filter_complex hang)
# ══════════════════════════════════════════════════════════════════════════════

async def make_tile_image(images: list[str], output_path: str, cols: int = 3) -> bool:
    """
    Stitch screenshots into a tile grid using Pillow.
    Much faster and more reliable than ffmpeg filter_complex for this use case.
    """
    def _build_tile():
        try:
            from PIL import Image
        except ImportError:
            logger.error("Pillow not installed — run: pip install Pillow")
            return False

        thumb_w, thumb_h = 640, 360
        rows  = math.ceil(len(images) / cols)
        tile  = Image.new("RGB", (thumb_w * cols, thumb_h * rows), (20, 20, 20))

        for idx, img_path in enumerate(images):
            try:
                img = Image.open(img_path).convert("RGB")
                img.thumbnail((thumb_w, thumb_h), Image.LANCZOS)
                # Center-pad to thumb_w x thumb_h
                bg  = Image.new("RGB", (thumb_w, thumb_h), (20, 20, 20))
                off = ((thumb_w - img.width) // 2, (thumb_h - img.height) // 2)
                bg.paste(img, off)
                col = idx % cols
                row = idx // cols
                tile.paste(bg, (col * thumb_w, row * thumb_h))
            except Exception as e:
                logger.warning(f"Tile: skipping {img_path}: {e}")

        tile.save(output_path, "JPEG", quality=85, optimize=True)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0

    # Run in thread so it doesn't block the event loop
    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _build_tile)
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  TRIM
# ══════════════════════════════════════════════════════════════════════════════

async def trim_video(input_path: str, start: str, end: str, output_path: str) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-ss", start, "-to", end,
        "-i", input_path,
        "-c", "copy", output_path
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    return proc.returncode == 0 and os.path.exists(output_path)


def parse_time_range(text: str):
    parts = re.split(r"\s*[-–]\s*", text.strip())
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None, None


# ══════════════════════════════════════════════════════════════════════════════
#  SAMPLE VIDEO
# ══════════════════════════════════════════════════════════════════════════════

async def generate_sample_video(input_path: str, duration_sec: int, output_path: str) -> bool:
    data  = run_ffprobe(input_path)
    if not data:
        return False
    total = float(data.get("format", {}).get("duration", 0))
    start = max(0, (total / 2) - (duration_sec / 2))
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", input_path,
        "-t", str(duration_sec),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
        "-c:a", "aac", "-b:a", "96k",
        output_path
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    return proc.returncode == 0 and os.path.exists(output_path)


# ══════════════════════════════════════════════════════════════════════════════
#  THUMBNAIL — use embedded cover art first, fall back to frame grab
# ══════════════════════════════════════════════════════════════════════════════

async def extract_thumbnail(input_path: str, output_path: str) -> bool:
    """
    Priority:
    1. Embedded cover art / attached pic (fast, already in the file header)
    2. Frame grab at 10s (fallback)
    """
    # Try extracting embedded cover art (stream type=video, codec=mjpeg/png/bmp)
    cmd_cover = [
        "ffmpeg", "-y", "-i", input_path,
        "-map", "0:v", "-map", "-0:V",   # select video streams, exclude actual video
        "-frames:v", "1",
        "-q:v", "1",
        output_path
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd_cover,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        logger.info("Thumbnail: extracted embedded cover art")
        return True

    # Fallback: grab frame at 10s
    cmd_frame = [
        "ffmpeg", "-y",
        "-ss", "00:00:10",
        "-i", input_path,
        "-vframes", "1", "-q:v", "1",
        "-vf", "scale=1280:-2",
        output_path
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd_frame,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        logger.info("Thumbnail: extracted frame at 10s")
        return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
#  COVER ART EXTRACTION — all embedded image streams from file header
# ══════════════════════════════════════════════════════════════════════════════

async def extract_covers(input_path: str, output_dir: str) -> list[str]:
    """
    Extract all embedded cover art / attached image streams.
    MKV/MP4/MKA files often have cover.jpg, poster.jpg etc as attachment streams.
    Returns list of extracted image paths.
    """
    covers = []

    # Method 1: ffmpeg attached pics (stream type video, disposition attached_pic)
    probe = run_ffprobe(input_path)
    if probe:
        streams = probe.get("streams", [])
        img_streams = [
            s for s in streams
            if s.get("codec_type") == "video"
            and s.get("disposition", {}).get("attached_pic", 0) == 1
        ]
        for i, s in enumerate(img_streams):
            out = os.path.join(output_dir, f"cover_{i}.jpg")
            cmd = [
                "ffmpeg", "-y", "-i", input_path,
                "-map", f"0:{s['index']}",
                "-frames:v", "1", "-q:v", "1", out
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
            if os.path.exists(out) and os.path.getsize(out) > 0:
                covers.append(out)

    # Method 2: ffmpeg attachment streams (MKV attachments like cover.jpg)
    if probe:
        att_streams = [
            s for s in probe.get("streams", [])
            if s.get("codec_type") == "attachment"
        ]
        for i, s in enumerate(att_streams):
            fname = s.get("tags", {}).get("filename", f"attach_{i}")
            ext   = os.path.splitext(fname)[1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                continue
            out = os.path.join(output_dir, f"attach_{i}{ext}")
            cmd = [
                "ffmpeg", "-y", "-i", input_path,
                "-map", f"0:{s['index']}",
                "-frames:v", "1", "-q:v", "1", out
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
            if os.path.exists(out) and os.path.getsize(out) > 0:
                covers.append(out)

    return covers
