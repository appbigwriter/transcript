"""Configuration, supported publication URLs and transcript exports."""
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

LANGUAGES = {"auto", "pt", "en", "es", "fr", "de", "it"}
PROVIDERS = {
    "groq": {"endpoint": "https://api.groq.com/openai/v1/audio/transcriptions", "key": "GROQ_API_KEY", "default": "whisper-large-v3-turbo", "models": {"whisper-large-v3-turbo", "whisper-large-v3"}},
    "openai": {"endpoint": "https://api.openai.com/v1/audio/transcriptions", "key": "OPENAI_API_KEY", "default": "gpt-4o-mini-transcribe", "models": {"gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"}},
}

@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    key: str
    data_dir: Path
    max_duration: int
    max_download: int
    max_queue: int
    timeout: int
    retention: int
    username: str
    password: str
    allow_public: bool
    cookies_file: str

    @property
    def timestamps(self):
        return self.provider == "groq" or self.model == "whisper-1"

    @classmethod
    def load(cls):
        provider = os.getenv("FBR_PROVIDER", "groq").lower()
        if provider not in PROVIDERS:
            raise ValueError("FBR_PROVIDER deve ser groq ou openai.")
        info = PROVIDERS[provider]
        model = os.getenv("FBR_MODEL", "").strip() or info["default"]
        if model not in info["models"]:
            raise ValueError("FBR_MODEL não é compatível com o provedor selecionado.")
        def number(name, default, minimum, maximum):
            value = int(os.getenv(name, str(default)))
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} fora do intervalo permitido.")
            return value
        return cls(provider, model, os.getenv(info["key"], "").strip(), Path(os.getenv("FBR_DATA_DIR", "data")),
                   number("FBR_MAX_DURATION_SECONDS", 3600, 1, 14400), number("FBR_MAX_DOWNLOAD_MB", 150, 1, 1000) * 1024 * 1024,
                   number("FBR_MAX_QUEUE", 10, 1, 100), number("FBR_JOB_TIMEOUT_SECONDS", 1800, 10, 7200),
                   number("FBR_RETENTION_HOURS", 24, 1, 168) * 3600,
                   os.getenv("FBR_USERNAME", "fbr"), os.getenv("FBR_PASSWORD", ""), os.getenv("FBR_ALLOW_PUBLIC") == "1",
                   os.getenv("FBR_COOKIES_FILE", ""))

def publication_url(raw):
    """Only supported social-publication paths; never arbitrary network/file URLs."""
    if not isinstance(raw, str) or len(raw) > 2048 or any(ord(c) < 32 for c in raw):
        raise ValueError("Link inválido.")
    try:
        parsed = urlsplit(raw.strip())
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError()
    except ValueError:
        raise ValueError("Use um link HTTPS de uma publicação.") from None
    path = parsed.path
    query = parse_qs(parsed.query)
    platform = None
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if path == "/watch" and re.fullmatch(r"[A-Za-z0-9_-]{11}", query.get("v", [""])[0]):
            path, clean_query = "/watch", "v=" + query["v"][0]
            platform = "YouTube"
        elif re.fullmatch(r"/(shorts|live|embed)/[A-Za-z0-9_-]{11}/?", path):
            clean_query, platform = "", "YouTube"
    elif host == "youtu.be" and re.fullmatch(r"/[A-Za-z0-9_-]{11}/?", path):
        clean_query, platform = "", "YouTube"
    elif host in {"instagram.com", "www.instagram.com"} and re.fullmatch(r"/(p|reel|reels)/[A-Za-z0-9_-]+/?|/stories/[A-Za-z0-9_.]+/\d+/?", path):
        clean_query, platform = "", "Instagram"
    elif host in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"} and re.fullmatch(r"/@[^/]+/video/\d+/?|/t/[A-Za-z0-9]+/?", path):
        clean_query, platform = "", "TikTok"
    elif host in {"vm.tiktok.com", "vt.tiktok.com"} and re.fullmatch(r"/[A-Za-z0-9]+/?", path):
        clean_query, platform = "", "TikTok"
    elif host in {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com"}:
        if re.fullmatch(r"/(reel/\d+|[^/]+/videos/\d+|share/(v|r)/[A-Za-z0-9]+)/?", path):
            clean_query, platform = "", "Facebook"
        elif path in {"/watch", "/watch/", "/video.php"} and query.get("v", [""])[0].isdigit():
            clean_query, platform = "v=" + query["v"][0], "Facebook"
        elif path == "/stories.php" and query.get("story_fbid", [""])[0].isdigit() and query.get("id", [""])[0].isdigit():
            clean_query, platform = f"story_fbid={query['story_fbid'][0]}&id={query['id'][0]}", "Facebook"
    elif host == "fb.watch" and re.fullmatch(r"/[A-Za-z0-9_-]+/?", path):
        clean_query, platform = "", "Facebook"
    elif host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"} and re.fullmatch(r"/[A-Za-z0-9_]+/status/\d+(?:/video/\d+)?/?|/i/status/\d+/?", path):
        clean_query, platform = "", "X"
    if not platform:
        raise ValueError("Cole o link de um vídeo, short, reel, story ou publicação com vídeo do YouTube, Instagram, TikTok, Facebook ou X.")
    return urlunsplit(("https", host, path, clean_query, "")), platform

def timestamp(seconds, separator=","):
    milliseconds = max(0, round(float(seconds) * 1000))
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}"

def subtitles(segments, vtt=False):
    blocks = ["WEBVTT\n"] if vtt else []
    for index, segment in enumerate(segments, 1):
        text = re.sub(r"\s+", " ", segment["text"]).strip().replace("-->", "→")
        if not text:
            continue
        separator = "." if vtt else ","
        lines = [] if vtt else [str(index)]
        lines.extend([f"{timestamp(segment['start'], separator)} --> {timestamp(segment['end'], separator)}", text])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"

def normalize_segments(raw, offset, duration):
    result = []
    for segment in raw:
        start, end = float(segment["start"]), float(segment["end"])
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("A API retornou tempos inválidos.")
        start, end = max(0, start), min(duration, end)
        if end > start and segment.get("text", "").strip():
            result.append({"start": round(offset + start, 3), "end": round(offset + end, 3), "text": segment["text"].strip()})
    return result
