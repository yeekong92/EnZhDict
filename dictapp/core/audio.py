"""Pronunciation playback.

Resolution order for a word + accent:
  1. recorded audio URL from the Free Dictionary API (downloaded once, cached)
  2. edge-tts neural voice (en-GB / en-US / zh-CN), cached as mp3
  3. offline system TTS (Windows SAPI / WinRT via QtTextToSpeech)
Everything here is blocking; ui/audio_player.py runs it on worker threads and
does playback (and the offline TTS fallback) on the UI thread.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

import requests

from ..config import audio_cache_dir

log = logging.getLogger(__name__)

VOICES = {"uk": "en-GB-SoniaNeural", "us": "en-US-JennyNeural", "zh": "zh-CN-XiaoxiaoNeural",
          "tl": "fil-PH-BlessicaNeural"}


def _cache_path(kind: str, key: str) -> Path:
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return audio_cache_dir() / f"{kind}-{h}.mp3"


def download_audio(url: str) -> Path:
    path = _cache_path("rec", url)
    if path.exists() and path.stat().st_size > 0:
        return path
    r = requests.get(url, timeout=(3, 6))  # fall back to TTS quickly if the host is slow
    r.raise_for_status()
    tmp = path.with_suffix(".part")
    tmp.write_bytes(r.content)
    tmp.replace(path)
    return path


def synthesize(text: str, accent: str) -> Path:
    voice = VOICES.get(accent, VOICES["us"])
    path = _cache_path("tts", f"{voice}|{text}")
    if path.exists() and path.stat().st_size > 0:
        return path
    import edge_tts

    tmp = path.with_suffix(".part")
    asyncio.run(edge_tts.Communicate(text, voice).save(str(tmp)))
    if not tmp.exists() or tmp.stat().st_size == 0:
        raise RuntimeError("edge-tts produced no audio")
    tmp.replace(path)
    return path


def resolve_audio(text: str, accent: str, url: str = "", online: bool = True) -> Path:
    """Blocking: return a local mp3 for `text` (raises if nothing is available)."""
    if url.startswith(("http://", "https://")):
        cached = _cache_path("rec", url)
        if cached.exists() or online:
            try:
                return download_audio(url)
            except Exception as e:  # noqa: BLE001
                log.info("recorded audio failed (%s), trying TTS", e)
    elif url and Path(url).exists():
        return Path(url)
    cached = _cache_path("tts", f"{VOICES.get(accent, VOICES['us'])}|{text}")
    if cached.exists():
        return cached
    if not online:
        raise RuntimeError("offline")
    return synthesize(text, accent)
