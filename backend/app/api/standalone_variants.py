"""Independent, non-destructive video variant jobs for existing media files."""
from __future__ import annotations

import os
import random
import threading
import uuid
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Callable, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..core.ffmpeg import probe_media
from ..core.video_variant import VideoVariantProcessor, derive_variant_seed


VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"})


class StandaloneVariantRequest(BaseModel):
    input_paths: list[str] = Field(min_length=1)
    output_dir: str = ""
    strength: Literal["mild", "balanced", "strong"] = "balanced"
    copies_per_video: int = Field(default=1, ge=1, le=10)
    allow_mirror: bool = False
    frame_mix: bool = True
    use_gpu: bool = False


def _inside(candidate: Path, parent: Path) -> bool:
    return candidate == parent or parent in candidate.parents


def _collect_inputs(paths: list[str], output_dir: Path) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        source = Path(raw).expanduser().resolve()
        if not source.exists():
            raise ValueError(f"素材不存在：{source}")
        if source.is_file():
            if source.suffix.lower() not in VIDEO_EXTENSIONS:
                raise ValueError(f"不支持的视频格式：{source.name}")
            candidates = [source]
        elif source.is_dir():
            if _inside(source, output_dir):
                raise ValueError("输出目录不能是素材文件夹或其上级目录")
            candidates = []
            for root, dirs, names in os.walk(source, followlinks=False):
                # An output folder inside an input tree must never become new input.
                dirs[:] = [name for name in dirs if not _inside((Path(root) / name).resolve(), output_dir)]
                candidates.extend(
                    Path(root, name).resolve() for name in names
                    if Path(name).suffix.lower() in VIDEO_EXTENSIONS
                    and not _inside(Path(root, name).resolve(), output_dir)
                )
        else:
            raise ValueError(f"无法读取素材：{source}")
        for item in candidates:
            key = os.path.normcase(str(item))
            if key not in seen:
                seen.add(key)
                result.append(item)
    result.sort(key=lambda path: str(path).casefold())
    if not result:
        raise ValueError("没有找到可处理的视频。支持 MP4、MOV、MKV、AVI、WEBM、M4V")
    if len(result) > 1000:
        raise ValueError("一次最多处理 1000 个视频，请分批选择")
    return result


def _video_settings(source: Path) -> dict:
    info = probe_media(str(source))
    if not info:
        raise ValueError("无法读取视频信息")
    video = next((stream for stream in info.get("streams", []) if stream.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("文件没有视频画面")
    try:
        duration = float(video.get("duration") or info.get("format", {}).get("duration") or 0)
        width, height = int(video["width"]), int(video["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("无法读取视频的尺寸或时长") from exc
    if duration <= 0.1 or width < 2 or height < 2:
        raise ValueError("视频时长或尺寸无效")

    # FFmpeg autorotates sources with phone orientation metadata on decode.
    rotation = video.get("tags", {}).get("rotate", 0)
    for side_data in video.get("side_data_list", []):
        if "rotation" in side_data:
            rotation = side_data["rotation"]
            break
    try:
        if round(float(rotation)) % 180 == 90:
            width, height = height, width
    except (TypeError, ValueError):
        pass
    width -= width % 2
    height -= height % 2

    fps = None
    for raw_fps in (video.get("avg_frame_rate"), video.get("r_frame_rate")):
        try:
            value = Fraction(str(raw_fps))
            if 0 < value <= 120:
                fps = str(value)
                break
        except (ValueError, ZeroDivisionError):
            pass
    if fps is None:
        fps = "30"
    try:
        source_bitrate = int(video.get("bit_rate") or 0)
    except (TypeError, ValueError):
        source_bitrate = 0
    bitrate = max(500_000, min(source_bitrate or 8_000_000, 30_000_000))
    return {
        "t_hook": duration, "total_clips": 1, "t_body": 0,
        "resolution": f"{width}*{height}", "fps": fps, "bitrate": str(bitrate),
        "variant_hook": True, "variant_body": False,
        "source_has_audio": any(stream.get("codec_type") == "audio" for stream in info.get("streams", [])),
    }


def _copy_without_overwriting(source: Path, destination: Path, cancelled: threading.Event,
                              on_progress: Callable[[float], None] | None = None) -> None:
    created = False
    total_bytes = max(1, source.stat().st_size)
    copied_bytes = 0
    try:
        with source.open("rb") as original, destination.open("xb") as target:
            created = True
            while chunk := original.read(4 * 1024 * 1024):
                if cancelled.is_set():
                    raise InterruptedError("已停止")
                target.write(chunk)
                copied_bytes += len(chunk)
                if on_progress:
                    on_progress(min(1.0, copied_bytes / total_bytes))
    except Exception:
        if created:
            VideoVariantProcessor._unlink_with_retry(destination)
        raise


class StandaloneVariantService:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.events: dict[str, threading.Event] = {}
        self.lock = threading.Lock()
        self.processor = VideoVariantProcessor()

    def create(self, request: StandaloneVariantRequest) -> dict:
        raw_paths = [value.strip() for value in request.input_paths if value.strip()]
        if not raw_paths:
            raise ValueError("请选择视频或素材文件夹")
        first = Path(raw_paths[0]).expanduser().resolve()
        default_parent = first.parent if first.is_file() else first
        output_dir = Path(request.output_dir).expanduser().resolve() if request.output_dir.strip() else (default_parent / "去重变换输出").resolve()
        if output_dir.exists() and not output_dir.is_dir():
            raise ValueError("输出位置不是文件夹")
        inputs = _collect_inputs(raw_paths, output_dir)
        task_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        job = {
            "task_id": task_id, "status": "pending", "progress": 0,
            "current": 0, "total": len(inputs) * request.copies_per_video,
            "file_progress": 0, "phase_detail": "等待开始处理",
            "input_count": len(inputs), "output_dir": str(output_dir),
            "output_files": [], "errors": [], "log_lines": [],
            "message": f"已找到 {len(inputs)} 个视频，等待处理", "created_at": now,
        }
        event = threading.Event()
        with self.lock:
            self.jobs[task_id] = job
            self.events[task_id] = event
        worker = threading.Thread(
            target=self._run, args=(task_id, inputs, output_dir, request, event),
            daemon=True, name=f"standalone-variant-{task_id[:8]}",
        )
        worker.start()
        return self.get(task_id)

    def get(self, task_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(task_id)
            if not job:
                return None
            return {**job, "output_files": list(job["output_files"]),
                    "errors": list(job["errors"]), "log_lines": list(job["log_lines"])}

    def stop(self, task_id: str) -> bool:
        with self.lock:
            event = self.events.get(task_id)
            if event is None:
                return False
            event.set()
            return True

    def stop_all(self) -> int:
        with self.lock:
            active = [task_id for task_id, job in self.jobs.items() if job["status"] in ("pending", "running")]
            for task_id in active:
                self.events[task_id].set()
            return len(active)

    def _update(self, task_id: str, **changes) -> None:
        with self.lock:
            self.jobs[task_id].update(changes)

    def _append(self, task_id: str, field: str, item) -> None:
        with self.lock:
            self.jobs[task_id][field].append(item)

    def _set_file_progress(self, task_id: str, completed: int, total: int,
                           value: float, detail: str) -> None:
        file_progress = max(0, min(99, round(value)))
        self._update(
            task_id, file_progress=file_progress, phase_detail=detail,
            progress=min(99, round((completed + file_progress / 100) / total * 100)),
        )

    def _log_stage(self, task_id: str, message: str) -> None:
        stamp = datetime.now().astimezone().strftime("%H:%M:%S")
        self._append(task_id, "log_lines", f"{stamp}  {message}")
        self._update(task_id, phase_detail=message)

    def _run(self, task_id: str, inputs: list[Path], output_dir: Path,
             request: StandaloneVariantRequest, cancelled: threading.Event) -> None:
        self._update(task_id, status="running", message="正在准备输出目录")
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._update(task_id, status="failed", message=f"无法创建输出目录：{exc}")
            return

        base_seed = random.SystemRandom().randrange(0, 2**63)
        completed = 0
        total = len(inputs) * request.copies_per_video
        for source_index, source in enumerate(inputs, 1):
            if cancelled.is_set():
                break
            try:
                settings = _video_settings(source)
                settings.update({
                    "variant_strength": request.strength,
                    "variant_mirror": request.allow_mirror,
                    "variant_frame_mix": request.frame_mix,
                    "enable_gpu": request.use_gpu,
                    "concurrent_tasks": 1,
                })
            except ValueError as exc:
                self._log_stage(task_id, f"跳过 {source.name}：{exc}")
                for _ in range(request.copies_per_video):
                    completed += 1
                    self._append(task_id, "errors", f"{source.name}：{exc}")
                self._update(task_id, current=completed, file_progress=0,
                             progress=round(completed / total * 100))
                continue

            for copy_index in range(1, request.copies_per_video + 1):
                if cancelled.is_set():
                    break
                destination = output_dir / f"{source.stem}_去重_{source_index:03d}_{copy_index:02d}_{task_id[:8]}.mp4"
                self._update(task_id, message=f"正在处理 {source.name}（{copy_index}/{request.copies_per_video}）")
                self._log_stage(task_id, f"开始：{source.name} → {destination.name}")
                self._log_stage(
                    task_id, f"读取源视频：{settings['resolution'].replace('*', '×')}，"
                    f"{settings['fps']} fps，时长 {settings['t_hook']:.1f} 秒；"
                    f"{'有原声音轨' if settings['source_has_audio'] else '无原声音轨'}。",
                )
                created_output = False
                try:
                    self._log_stage(task_id, "复制原视频到新文件；源文件保持不变。")
                    _copy_without_overwriting(
                        source, destination, cancelled,
                        on_progress=lambda fraction: self._set_file_progress(
                            task_id, completed, total, fraction * 8,
                            f"正在复制原视频：{fraction * 100:.0f}%",
                        ),
                    )
                    created_output = True
                    seed = derive_variant_seed(base_seed, str(source), copy_index)

                    def on_stage(message: str, encode_fraction: float | None) -> None:
                        if encode_fraction is None:
                            self._log_stage(task_id, message)
                        else:
                            self._set_file_progress(
                                task_id, completed, total, 10 + encode_fraction * 85, message,
                            )

                    ok, error, summary = self.processor.process(
                        str(destination), dict(settings), seed, is_cancelled=cancelled.is_set,
                        on_stage=on_stage,
                    )
                    if not ok:
                        raise RuntimeError(error or "去重变换失败")
                    self._set_file_progress(task_id, completed, total, 98, "正在复核已生成的视频文件。")
                    if not destination.exists() or not probe_media(str(destination)):
                        raise RuntimeError("输出视频校验失败")
                    self._append(task_id, "output_files", str(destination))
                    self._log_stage(task_id, f"完成：{destination.name}（{summary['segments']} 段变换）")
                except Exception as exc:
                    if created_output:
                        self.processor._unlink_with_retry(destination)
                    if not cancelled.is_set():
                        self._append(task_id, "errors", f"{source.name}：{str(exc).strip()[-400:]}")
                        self._log_stage(task_id, f"失败：{source.name}：{str(exc).strip()[-400:]}")
                completed += 1
                self._update(task_id, current=completed, file_progress=100,
                             progress=round(completed / total * 100))

        final = self.get(task_id)
        assert final is not None
        if cancelled.is_set():
            status, message = "stopped", f"已停止，保留已完成的 {len(final['output_files'])} 个视频"
        elif final["output_files"]:
            status = "completed"
            message = f"完成 {len(final['output_files'])} 个视频"
            if final["errors"]:
                message += f"，{len(final['errors'])} 个失败"
        else:
            status, message = "failed", "处理失败，请查看具体原因"
        self._update(task_id, status=status, message=message)


standalone_variant_service = StandaloneVariantService()
router = APIRouter()


@router.post("/standalone-variants", status_code=201)
def create_standalone_variant(request: StandaloneVariantRequest):
    try:
        return standalone_variant_service.create(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/standalone-variants/{task_id}")
def get_standalone_variant(task_id: str):
    task = standalone_variant_service.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.post("/standalone-variants/{task_id}/stop")
def stop_standalone_variant(task_id: str):
    if not standalone_variant_service.stop(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"message": "停止指令已发送"}


@router.on_event("shutdown")
def stop_standalone_variants_on_shutdown():
    standalone_variant_service.stop_all()
