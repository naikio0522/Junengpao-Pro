"""Export editable SRT sidecars for finished videos using local transcripts."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..core.speech_transcript import SpeechSegment, SpeechTranscriptService


router = APIRouter(prefix="/subtitles", tags=["subtitles"])
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
_transcripts = SpeechTranscriptService()


class ExportSrtRequest(BaseModel):
    video_path: str = Field(min_length=1)


class ExportSrtResponse(BaseModel):
    video_path: str
    srt_path: str
    folder: str
    cue_count: int
    status: Literal["created", "existing"]


def _timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render_srt(cues: list[SpeechSegment]) -> str:
    """Preserve measured cue boundaries; do not invent alignment or text."""
    return "\n\n".join(
        f"{index}\n{_timestamp(cue['start'])} --> {_timestamp(cue['end'])}\n{cue['text']}"
        for index, cue in enumerate(cues, start=1)
    ) + "\n"


def export_srt(
    video_path: str | Path, *, transcripts: SpeechTranscriptService | None = None,
) -> ExportSrtResponse:
    video = Path(video_path).expanduser().resolve(strict=True)
    if not video.is_file() or video.suffix.lower() not in _VIDEO_EXTENSIONS:
        raise ValueError("请选择受支持的成片视频文件。")
    service = transcripts or _transcripts
    subtitle = video.with_suffix(".srt")
    # get_cues reads an existing sidecar first. A hand-edited subtitle is never
    # replaced by a fresh ASR result.
    cues = service.get_cues(video, allow_asr=True)
    if not cues:
        raise ValueError("未识别到带时间码的口播，未生成空白字幕。")
    status: Literal["created", "existing"] = "existing" if subtitle.is_file() else "created"
    if status == "created":
        try:
            with subtitle.open("x", encoding="utf-8-sig", newline="\n") as handle:
                try:
                    handle.write(render_srt(cues))
                except BaseException:
                    handle.close()
                    subtitle.unlink(missing_ok=True)
                    raise
        except FileExistsError:
            # Another request may have written the sidecar during transcription.
            cues = service.get_cues(video, allow_asr=False)
            status = "existing"
    return ExportSrtResponse(
        video_path=str(video), srt_path=str(subtitle), folder=str(video.parent),
        cue_count=len(cues), status=status,
    )


@router.post("/srt", response_model=ExportSrtResponse)
def export_srt_route(request: ExportSrtRequest) -> ExportSrtResponse:
    try:
        return export_srt(request.video_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="成片视频不存在，请重新选择文件。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存字幕失败：{exc}") from exc
