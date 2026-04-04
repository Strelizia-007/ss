"""
media_utils.py — FFmpeg processing, MediaInfo, GDrive download, 18+ detection
"""

import os, re, asyncio, subprocess, tempfile, math, json, logging
from pathlib import Path
from typing import Optional
import httpx
from config import ADULT_KEYWORDS, GDRIVE_SERVICE_ACCOUNT_JSON

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  18+ DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def is_adult_content(filename: str, caption: str = "") -> bool:
    """Check filename and caption for adult content keywords."""
    text = (filename + " " + caption).lower()
    return any(kw in text for kw in ADULT_KEYWORDS)


# ══════════════════════════════════════════════════════════════════════════════
#  LINK DETECTION & DOWNLOAD
# ══════════════════════════════════════════════════════════════════════════════

GDRIVE_FILE_RE   = re.compile(r"drive\.google\.com/file/d/([^/\s?]+)")
GDRIVE_FOLDER_RE = re.compile(r"drive\.google\.com/drive/folders/([^/\s?]+)")
DIRECT_LINK_RE   = re.compile(r"https?://[^\s]+\.(mp4|mkv|avi|mov|webm|ts|m2ts|flv)", re.IGNORECASE)


def detect_link_type(text: str) -> tuple[str, str]:
    """Returns (type, identifier). type: direct | gdrive_file | gdrive_folder | unsupported"""
    if m := GDRIVE_FILE_RE.search(text):
        return "gdrive_file", m.group(1)
    if m := GDRIVE_FOLDER_RE.search(text):
        return "gdrive_folder", m.group(1)
    if m := DIRECT_LINK_RE.search(text):
        return "direct", m.group(0)
    if text.startswith("http"):
        return "unsupported", text
    return "none", ""


async def download_direct_link(url: str, dest: str) -> bool:
    """Download a direct HTTP/HTTPS link to dest path."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=300) as client:
            async with client.stream("GET", url) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in r.aiter_bytes(1024 * 512):
                        f.write(chunk)
        return True
    except Exception as e:
        logger.error(f"Direct download failed: {e}")
        return False


async def download_gdrive_file(file_id: str, dest: str) -> bool:
    """Download a Google Drive file using gdown."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "gdown", f"https://drive.google.com/uc?id={file_id}",
            "-O", dest, "--fuzzy",
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
#  MEDIA INFO
# ══════════════════════════════════════════════════════════════════════════════

def run_ffprobe(path: str) -> Optional[dict]:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path
    ]
    try:
        out = subprocess.check_output(cmd, timeout=60)
        return json.loads(out)
    except Exception as e:
        logger.error(f"ffprobe error: {e}")
        return None


def parse_simple_mediainfo(path: str) -> str:
    """Return a short human-readable media summary."""
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
    bit_depth = ""
    if video:
        h_px = video.get("height", 0)
        for res, label in [(2160,"2160p"),(1440,"1440p"),(1080,"1080p"),(720,"720p"),(480,"480p")]:
            if h_px >= res:
                quality = label
                break
        cname = video.get("codec_name","").upper()
        codec_map = {"HEVC":"HEVC","H265":"HEVC","H264":"H264","AVC":"H264","AV1":"AV1","VP9":"VP9"}
        codec = codec_map.get(cname, cname)
        pix  = video.get("pix_fmt","")
        bit_depth = " 10bit" if "10" in pix else " 8bit"

    langs = list({a.get("tags",{}).get("language","und") for a in audios})
    sub_langs = list({s.get("tags",{}).get("language","und") for s in subs})

    size  = int(fmt.get("size", 0))
    size_mb = round(size / 1024 / 1024, 1)

    lines = [
        f"🎬 **Quality:** `{quality} {codec}{bit_depth}`",
        f"🌐 **Language:** `{', '.join(langs) or 'N/A'}`",
        f"📝 **Subtitle:** `{', '.join(sub_langs) or 'None'}`",
        f"⏱ **Duration:** `{dur_str}`",
        f"💾 **Size:** `{size_mb} MB`",
    ]
    return "\n".join(lines)


def parse_detailed_mediainfo(path: str) -> str:
    """Full mediainfo output as text for Telegraph."""
    try:
        out = subprocess.check_output(["mediainfo", path], timeout=60)
        return out.decode("utf-8", errors="replace")
    except FileNotFoundError:
        # Fall back to ffprobe JSON if mediainfo binary not available
        data = run_ffprobe(path)
        return json.dumps(data, indent=2) if data else "mediainfo not available"
    except Exception as e:
        return f"Error: {e}"


# ══════════════════════════════════════════════════════════════════════════════
#  TELEGRAPH UPLOAD
# ══════════════════════════════════════════════════════════════════════════════

async def upload_to_telegraph(title: str, content: str, token: str = "") -> Optional[str]:
    """Upload text content to Telegraph and return URL."""
    node = [{"tag": "pre", "children": [content]}]
    payload = {
        "title": title,
        "author_name": "ScreenshotBot",
        "content": json.dumps(node),
    }
    base = "https://api.telegra.ph"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if token:
                r = await client.post(f"{base}/createPage?access_token={token}", data=payload)
            else:
                # Create an account first if no token
                acc = await client.post(f"{base}/createAccount",
                                        data={"short_name":"ScreenBot","author_name":"ScreenBot"})
                acc_data = acc.json()
                tok = acc_data["result"]["access_token"]
                payload["access_token"] = tok
                r = await client.post(f"{base}/createPage", data=payload)
            result = r.json()
            if result.get("ok"):
                return "https://telegra.ph" + result["result"]["path"]
    except Exception as e:
        logger.error(f"Telegraph upload error: {e}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  SCREENSHOTS
# ══════════════════════════════════════════════════════════════════════════════

async def generate_screenshots(
    input_path: str,
    count: int,
    output_dir: str,
    mode: str = "equally_spaced"
) -> list[str]:
    """Generate `count` screenshots. Returns list of output PNG paths."""
    data = run_ffprobe(input_path)
    if not data:
        return []

    duration = float(data.get("format", {}).get("duration", 0))
    if duration <= 0:
        return []

    timestamps = [duration * (i + 1) / (count + 1) for i in range(count)]

    paths = []
    for i, ts in enumerate(timestamps):
        out = os.path.join(output_dir, f"scht_{i:03d}.jpg")
        cmd = [
            "ffmpeg", "-ss", str(ts), "-i", input_path,
            "-vframes", "1", "-q:v", "2", "-y", out
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        if os.path.exists(out) and os.path.getsize(out) > 0:
            paths.append(out)
    return paths


async def make_tile_image(images: list[str], output_path: str, cols: int = 3) -> bool:
    """Combine images into a tile mosaic using ffmpeg."""
    rows = math.ceil(len(images) / cols)
    # Build ffmpeg tile filter
    inputs  = []
    for img in images:
        inputs += ["-i", img]

    # Scale each to uniform size then tile
    filters = []
    for j in range(len(images)):
        filters.append(f"[{j}:v]scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2[v{j}]")

    tile_inputs = "".join(f"[v{j}]" for j in range(len(images)))
    filters.append(f"{tile_inputs}tile={cols}x{rows}[out]")

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", ";".join(filters),
        "-map", "[out]", "-q:v", "2", output_path
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


# ══════════════════════════════════════════════════════════════════════════════
#  TRIM
# ══════════════════════════════════════════════════════════════════════════════

async def trim_video(input_path: str, start: str, end: str, output_path: str) -> bool:
    """Trim video from start to end (HH:MM:SS or seconds)."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-ss", start, "-to", end,
        "-c", "copy", output_path
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    return proc.returncode == 0 and os.path.exists(output_path)


def parse_time_range(text: str):
    """Parse 'HH:MM:SS - HH:MM:SS' or 'SS - SS' → (start, end) strings."""
    parts = re.split(r"\s*[-–]\s*", text.strip())
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None, None


# ══════════════════════════════════════════════════════════════════════════════
#  SAMPLE VIDEO
# ══════════════════════════════════════════════════════════════════════════════

async def generate_sample_video(input_path: str, duration_sec: int, output_path: str) -> bool:
    """Extract a sample clip from the middle of the video."""
    data = run_ffprobe(input_path)
    if not data:
        return False
    total = float(data.get("format", {}).get("duration", 0))
    start = max(0, (total / 2) - (duration_sec / 2))
    cmd = [
        "ffmpeg", "-y", "-ss", str(start), "-i", input_path,
        "-t", str(duration_sec),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", output_path
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    return proc.returncode == 0 and os.path.exists(output_path)


# ══════════════════════════════════════════════════════════════════════════════
#  THUMBNAILS / COVERS
# ══════════════════════════════════════════════════════════════════════════════

async def extract_thumbnail(input_path: str, output_path: str, time: str = "00:00:10") -> bool:
    cmd = [
        "ffmpeg", "-y", "-ss", time, "-i", input_path,
        "-vframes", "1", "-q:v", "1", output_path
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    return os.path.exists(output_path) and os.path.getsize(output_path) > 0
