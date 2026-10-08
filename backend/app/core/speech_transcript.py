"""Timecoded speech for an existing video, with optional offline ASR.

Only the ASR result is cached. Sidecars and the old raw-library transcript are
read on each request, so editing a subtitle is reflected immediately.
"""

from __future__ import annotations

import csv
import errno
import hashlib
import json
import math
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import TypedDict


class SpeechSegment(TypedDict):
    start: float
    end: float
    text: str


_TIMED_LINE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[–—-]\s*(\d+(?:\.\d+)?)\s+(.+?)\s*$")
_SRT_TIME = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{2}:\d{2}:\d{2}[,.]\d{1,3})"
)
_SENTENCE_END = re.compile(r"[。！？!?；;]\s*$")
_BUNDLE_ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else None
_WORKSPACE_ROOT = Path(__file__).resolve().parents[5] if _BUNDLE_ROOT is None else None
_DEFAULT_REFERENCE_DIR = (
    _BUNDLE_ROOT / "speech_transcripts" if _BUNDLE_ROOT is not None
    else _WORKSPACE_ROOT / "outputs" / "reference-study" / "transcripts-raw-library"
)
_DEFAULT_MANIFEST = (
    None if _BUNDLE_ROOT is not None
    else _WORKSPACE_ROOT / "outputs" / "auto-mix-pilot" / "source-inventory.json"
)
_CACHE_VERSION = 2


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]*>|\{\\[^}]*\}", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _cue(start: float, end: float, text: str) -> SpeechSegment | None:
    text = _clean_text(text)
    if not text or not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        return None
    return {"start": round(float(start), 3), "end": round(float(end), 3), "text": text}


def parse_timed_text(content: str) -> list[SpeechSegment]:
    """Parse the ``0.0–1.2 words`` format used by the existing raw library."""
    cues: list[SpeechSegment] = []
    for line in content.splitlines():
        match = _TIMED_LINE.match(line)
        if match:
            cue = _cue(float(match[1]), float(match[2]), match[3])
            if cue:
                cues.append(cue)
    return cues


def _srt_seconds(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_srt(content: str) -> list[SpeechSegment]:
    cues: list[SpeechSegment] = []
    for block in re.split(r"\n\s*\n", content.replace("\r\n", "\n").replace("\r", "\n")):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            match = _SRT_TIME.search(line)
            if match:
                cue = _cue(
                    _srt_seconds(match["start"]),
                    _srt_seconds(match["end"]),
                    " ".join(lines[index + 1:]),
                )
                if cue:
                    cues.append(cue)
                break
    return cues


def to_sentences(cues: list[SpeechSegment], *, max_duration: float = 8.0) -> list[SpeechSegment]:
    """Join short adjacent ASR/subtitle cues without inventing word timestamps."""
    ordered = sorted(cues, key=lambda item: (item["start"], item["end"]))
    sentences: list[SpeechSegment] = []
    current: SpeechSegment | None = None
    for cue in ordered:
        if current is None:
            current = dict(cue)
            continue
        gap = cue["start"] - current["end"]
        split = (
            gap > 0.45 or gap < -0.25
            or bool(_SENTENCE_END.search(current["text"]))
            or cue["end"] - current["start"] > max_duration
            or len(current["text"]) + len(cue["text"]) > 72
        )
        if split:
            sentences.append(current)
            current = dict(cue)
        else:
            current["end"] = max(current["end"], cue["end"])
            current["text"] = f"{current['text']} {cue['text']}"
    if current is not None:
        sentences.append(current)
    return sentences


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="gb18030")


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


class SpeechTranscriptService:
    """Load sentence-level ``start/end/text`` dictionaries for one video.

    The existing raw-library transcripts are associated using the *exact*
    video basename from ``raw-library-metadata.csv``. An ID substring alone is
    never enough to claim a match.
    """

    def __init__(
        self,
        *,
        cache_dir: str | Path | None = None,
        reference_dir: str | Path | None = None,
        reference_video_dir: str | Path | None = None,
        reference_manifest: str | Path | None = None,
        reference_index: str | Path | None = None,
        model_path: str | Path = "base",
    ) -> None:
        default_cache = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home() / ".cache")
        self.cache_dir = Path(cache_dir) if cache_dir is not None else default_cache / "VideoMatrix" / "speech_transcripts"
        self.reference_dir = Path(reference_dir) if reference_dir is not None else _DEFAULT_REFERENCE_DIR
        self.reference_video_dir = Path(reference_video_dir) if reference_video_dir is not None else None
        self.reference_index = Path(reference_index) if reference_index is not None else self.reference_dir / "index.json"
        if reference_manifest is not None:
            self.reference_manifest = Path(reference_manifest)
        elif reference_dir is not None:
            self.reference_manifest = self.reference_dir.parent.parent / "auto-mix-pilot" / "source-inventory.json"
        else:
            self.reference_manifest = _DEFAULT_MANIFEST
        self.model_id = str(model_path)
        bundled_model = _BUNDLE_ROOT / "speech_model" / "base" if _BUNDLE_ROOT is not None else None
        if self.model_id == "base" and bundled_model is not None and (bundled_model / "model.bin").is_file():
            self.model_path = str(bundled_model)
        else:
            self.model_path = self.model_id
        self._model = None

    def get_segments(self, video_path: str | Path, *, allow_asr: bool = False) -> list[SpeechSegment]:
        """Return approximate sentence groups for display or general search."""
        return to_sentences(self.get_cues(video_path, allow_asr=allow_asr))

    def get_cues(self, video_path: str | Path, *, allow_asr: bool = False) -> list[SpeechSegment]:
        """Return timecoded speech, or ``[]`` when none exists and ASR is off.

        Priority: same-stem SRT, same-stem timed TXT, exact old-library match,
        valid ASR cache, then optional local faster-whisper CPU transcription.
        Untimed sidecars raise ValueError because timing cannot be inferred.
        """
        video = Path(video_path).resolve(strict=True)
        if not video.is_file():
            raise ValueError(f"视频路径不是文件：{video}")
        fingerprint = self._fingerprint(video)

        for suffix, parser in ((".srt", parse_srt), (".txt", parse_timed_text)):
            sidecar = video.with_suffix(suffix)
            if sidecar.is_file():
                cues = parser(_read_text(sidecar))
                if not cues:
                    raise ValueError(f"同名转录缺少有效时间码：{sidecar}")
                return cues

        legacy = self._legacy_transcript(video, fingerprint)
        if legacy is not None:
            return legacy

        cache_file = self._cache_file(video)
        cached = self._read_cache(cache_file, fingerprint)
        if cached is not None:
            return cached
        if not allow_asr:
            return []

        cues = self._transcribe_audio(video)
        self._write_cache(cache_file, fingerprint, cues)
        return cues

    @staticmethod
    def _fingerprint(video: Path) -> dict[str, int | str]:
        stat = video.stat()
        return {"path": str(video), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}

    def _legacy_transcript(self, video: Path, fingerprint: dict[str, int | str]) -> list[SpeechSegment] | None:
        if not self.reference_dir.is_dir():
            return None
        if self.reference_video_dir is not None and not _same_path(video.parent, self.reference_video_dir):
            return None
        if self.reference_index.is_file():
            video_id = self._indexed_transcript_id(video, fingerprint)
            if video_id is None:
                return None
            transcript = self.reference_dir / f"{video_id}.txt"
            cues = parse_timed_text(_read_text(transcript)) if transcript.is_file() else []
            return cues or None

        metadata_file = self.reference_dir.parent / "raw-library-metadata.csv"
        if not metadata_file.is_file():
            return None
        with metadata_file.open(encoding="utf-8-sig", newline="") as handle:
            matching = [row for row in csv.DictReader(handle) if row.get("Name") == video.name]
        if len(matching) != 1:
            return None
        video_id = matching[0].get("ID", "")
        if not video_id.isdigit() or not re.match(rf"^原片{re.escape(video_id)}_", video.stem):
            return None
        transcript = self.reference_dir / f"{video_id}.txt"
        if not transcript.is_file() or not self._manifest_matches(video, fingerprint):
            return None
        cues = parse_timed_text(_read_text(transcript))
        return cues or None

    def _indexed_transcript_id(self, video: Path, fingerprint: dict[str, int | str]) -> str | None:
        """Resolve a bundled transcript without storing an absolute source path."""
        try:
            index = json.loads(_read_text(self.reference_index))
            if index.get("version") != 1 or not isinstance(index.get("records"), list):
                return None
            matching = [record for record in index["records"] if record.get("name") == video.name]
            if len(matching) != 1:
                return None
            record = matching[0]
            video_id = str(record["id"])
            if not video_id.isdigit() or not re.match(rf"^原片{re.escape(video_id)}_", video.stem):
                return None
            if int(record["size_bytes"]) != fingerprint["size"]:
                return None
            if int(record["mtime_ns"]) != fingerprint["mtime_ns"]:
                return None
            return video_id
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def _manifest_matches(self, video: Path, fingerprint: dict[str, int | str]) -> bool:
        if self.reference_manifest is None or not self.reference_manifest.is_file():
            return False
        try:
            records = json.loads(_read_text(self.reference_manifest))["records"]
            matching = [
                record for record in records
                if record.get("source_group") == "raw"
                and record.get("path") == str(video)
            ]
            return len(matching) == 1 and all(
                int(matching[0][field]) == fingerprint[target]
                for field, target in (("size_bytes", "size"), ("mtime_ns", "mtime_ns"))
            )
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def _cache_file(self, video: Path) -> Path:
        key = hashlib.sha256(os.path.normcase(str(video)).encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.json"

    def _read_cache(self, cache_file: Path, fingerprint: dict[str, int | str]) -> list[SpeechSegment] | None:
        try:
            data = json.loads(_read_text(cache_file))
            if (
                data.get("version") != _CACHE_VERSION
                or data.get("video") != fingerprint
                or data.get("model") != self.model_id
            ):
                return None
            segments = data["segments"]
            if not isinstance(segments, list):
                return None
            cues = [_cue(item["start"], item["end"], item["text"]) for item in segments]
            if any(cue is None for cue in cues):
                return None
            return [cue for cue in cues if cue is not None]
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write_cache(self, cache_file: Path, fingerprint: dict[str, int | str], segments: list[SpeechSegment]) -> None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_file.with_name(f"{cache_file.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps({
                    "version": _CACHE_VERSION,
                    "video": fingerprint,
                    "model": self.model_id,
                    "segments": segments,
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            try:
                os.replace(temporary, cache_file)
            except OSError as exc:
                # Some Windows storage providers reject rename even when both
                # names are in one directory. A readable cache is preferable
                # to treating a successful transcription as a failed video.
                if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:
                    raise
                shutil.copyfile(temporary, cache_file)
        finally:
            temporary.unlink(missing_ok=True)

    def _transcribe_audio(self, video: Path) -> list[SpeechSegment]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("离线转写需要安装 faster-whisper；可先提供同名 SRT/TXT。") from exc
        if self._model is None:
            try:
                self._model = WhisperModel(
                    self.model_path, device="cpu", compute_type="int8", local_files_only=True,
                )
            except Exception as exc:
                raise RuntimeError("未找到本地 faster-whisper base 模型，无法离线转写。") from exc
        segments, _info = self._model.transcribe(
            str(video), language="zh", beam_size=1, vad_filter=True,
            condition_on_previous_text=False,
        )
        cues = []
        for segment in segments:
            cue = _cue(segment.start, segment.end, segment.text)
            if cue:
                cues.append(cue)
        return cues
