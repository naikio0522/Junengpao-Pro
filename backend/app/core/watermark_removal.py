"""Local, non-destructive removal of small stationary video overlays.

The implementation uses FFmpeg's delogo filter.  Automatic detection is a
conservative temporal-edge heuristic; it deliberately returns no region when
the scene does not contain enough motion to distinguish a logo from scenery.
No third-party watermark-removal source or model is bundled here.
"""
from __future__ import annotations

import base64
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .ffmpeg import FFMPEG, probe_media


VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"})


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    duration: float
    has_audio: bool


@dataclass(frozen=True)
class Region:
    x: int
    y: int
    width: int
    height: int

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


def inspect_video(source: Path) -> VideoInfo:
    if not source.is_file() or source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("请选择支持的本地视频文件（MP4、MOV、M4V、MKV、AVI、WEBM）")
    media = probe_media(str(source))
    if not media:
        raise ValueError(f"无法读取视频：{source.name}")
    video = next((stream for stream in media.get("streams", [])
                  if stream.get("codec_type") == "video"), None)
    if not video:
        raise ValueError(f"文件没有视频画面：{source.name}")
    try:
        width, height = int(video["width"]), int(video["height"])
        duration = float(video.get("duration") or media.get("format", {}).get("duration") or 0)
    except (TypeError, KeyError, ValueError) as exc:
        raise ValueError(f"视频尺寸或时长无效：{source.name}") from exc
    if width < 16 or height < 16 or duration <= 0:
        raise ValueError(f"视频尺寸或时长无效：{source.name}")
    rotation = video.get("tags", {}).get("rotate", 0)
    for item in video.get("side_data_list", []):
        if "rotation" in item:
            rotation = item["rotation"]
            break
    try:
        if round(float(rotation)) % 180 == 90:
            width, height = height, width
    except (TypeError, ValueError):
        pass
    return VideoInfo(width, height, duration,
                     any(stream.get("codec_type") == "audio" for stream in media.get("streams", [])))


def validate_region(region: Region, info: VideoInfo) -> Region:
    if (region.x < 0 or region.y < 0 or region.width < 4 or region.height < 4
            or region.x + region.width > info.width
            or region.y + region.height > info.height):
        raise ValueError("去水印框必须在画面内，且宽高至少为 4 像素")
    if region.width * region.height > info.width * info.height * 0.20:
        raise ValueError("去水印框超过画面 20%，请缩小范围")
    return region


def scale_region(region: Region, reference_width: int | None,
                 reference_height: int | None, info: VideoInfo) -> Region:
    if not reference_width or not reference_height:
        return validate_region(region, info)
    if reference_width < 16 or reference_height < 16:
        raise ValueError("参考画面尺寸无效")
    scaled = Region(
        round(region.x * info.width / reference_width),
        round(region.y * info.height / reference_height),
        max(4, round(region.width * info.width / reference_width)),
        max(4, round(region.height * info.height / reference_height)),
    )
    return validate_region(scaled, info)


def _scaled_dimensions(info: VideoInfo, longest: int) -> tuple[int, int]:
    ratio = min(1.0, longest / max(info.width, info.height))
    return max(2, round(info.width * ratio / 2) * 2), max(2, round(info.height * ratio / 2) * 2)


def _sample_gray(source: Path, second: float, width: int, height: int) -> bytes:
    command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-ss", f"{second:.3f}", "-i", str(source), "-map", "0:v:0",
               "-frames:v", "1", "-vf", f"scale={width}:{height},format=gray",
               "-f", "rawvideo", "pipe:1"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("无法读取视频画面，请确认 FFmpeg 可用") from exc
    if result.returncode or len(result.stdout) != width * height:
        raise ValueError("无法读取视频画面，请检查视频格式")
    return result.stdout


def _motion_ratio(ranges: bytearray, width: int, height: int,
                  box: tuple[int, int, int, int]) -> float:
    x0, y0, x1, y1 = box
    x0, x1 = max(0, x0), min(width, x1)
    y0, y1 = max(0, y0), min(height, y1)
    count = max(1, (x1 - x0) * (y1 - y0))
    moving = sum(ranges[y * width + x] >= 28
                 for y in range(y0, y1) for x in range(x0, x1))
    return moving / count


def locate_static_overlay(frames: list[bytes], width: int, height: int) -> tuple[Region | None, float]:
    """Find a compact high-contrast, temporally stable component near a corner.

    Returns a low confidence/no-region result on still scenes, moving marks,
    or weak evidence.  This is a suggestion, not a universal watermark model.
    """
    if len(frames) < 4 or any(len(frame) != width * height for frame in frames):
        return None, 0.0
    size = width * height
    low = bytearray(frames[0])
    high = bytearray(frames[0])
    for frame in frames[1:]:
        for index, value in enumerate(frame):
            if value < low[index]:
                low[index] = value
            if value > high[index]:
                high[index] = value
    ranges = bytearray(high[index] - low[index] for index in range(size))
    # A static scene cannot safely separate the actual image from an overlay.
    if _motion_ratio(ranges, width, height,
                     (width // 4, height // 4, 3 * width // 4, 3 * height // 4)) < 0.12:
        return None, 0.0

    first = frames[0]
    cell = 4
    grid_w, grid_h = (width + cell - 1) // cell, (height + cell - 1) // cell
    counts = [0] * (grid_w * grid_h)
    for y in range(2, height - 2):
        row = y * width
        for x in range(2, width - 2):
            index = row + x
            if ranges[index] > 16:
                continue
            value = first[index]
            contrast = max(abs(value - first[index - 2]),
                           abs(value - first[index + 2]),
                           abs(value - first[index - 2 * width]),
                           abs(value - first[index + 2 * width]))
            if contrast >= 32:
                counts[(y // cell) * grid_w + x // cell] += 1

    marked = {index for index, count in enumerate(counts) if count >= 2}
    visited: set[int] = set()
    candidates: list[tuple[float, Region]] = []
    max_w, max_h = round(width * .38), round(height * .30)
    for start in marked:
        if start in visited:
            continue
        component = []
        queue = [start]
        visited.add(start)
        while queue:
            index = queue.pop()
            component.append(index)
            cx, cy = index % grid_w, index // grid_w
            for dy in (-2, -1, 0, 1, 2):
                for dx in (-2, -1, 0, 1, 2):
                    nx, ny = cx + dx, cy + dy
                    neighbor = ny * grid_w + nx
                    if (0 <= nx < grid_w and 0 <= ny < grid_h
                            and neighbor in marked and neighbor not in visited):
                        visited.add(neighbor)
                        queue.append(neighbor)
        if len(component) < 5:
            continue
        xs = [index % grid_w for index in component]
        ys = [index // grid_w for index in component]
        x0, y0 = min(xs) * cell, min(ys) * cell
        x1, y1 = min(width, (max(xs) + 1) * cell), min(height, (max(ys) + 1) * cell)
        box_w, box_h = x1 - x0, y1 - y0
        count = sum(counts[index] for index in component)
        if (box_w < 10 or box_h < 8 or box_w > max_w or box_h > max_h
                or count < max(32, size // 1100)):
            continue
        near_left = x0 < width * .13
        near_right = x1 > width * .87
        near_top = y0 < height * .15
        near_bottom = y1 > height * .85
        if not ((near_left or near_right) and (near_top or near_bottom)):
            continue
        # An ordinary stationary object embedded in a still corner is not
        # evidence.  Motion just outside the candidate adds discrimination.
        ring = _motion_ratio(ranges, width, height,
                             (x0 - 12, y0 - 12, x1 + 12, y1 + 12))
        if ring < .10:
            continue
        density = count / max(1, box_w * box_h)
        if density < .018:
            continue
        score = min(.98, .48 + .002 * count + .35 * min(ring, 1) + min(density, .15))
        margin = max(2, min(6, round(min(width, height) * .015)))
        region = Region(max(0, x0 - margin), max(0, y0 - margin),
                        min(width, x1 + margin) - max(0, x0 - margin),
                        min(height, y1 + margin) - max(0, y0 - margin))
        candidates.append((score, region))

    if not candidates:
        return None, 0.0
    confidence, region = max(candidates, key=lambda item: item[0])
    return region, round(confidence, 2)


def detect_region(source: Path, info: VideoInfo | None = None) -> tuple[Region | None, float]:
    info = info or inspect_video(source)
    width, height = _scaled_dimensions(info, 256)
    # Spread samples through the clip.  Very short clips may yield fewer unique
    # frames, which naturally produces no confident automatic result.
    frames = [_sample_gray(source, info.duration * fraction, width, height)
              for fraction in (.08, .22, .36, .50, .64, .78, .92)]
    found, confidence = locate_static_overlay(frames, width, height)
    if not found or confidence < .70:
        return None, confidence
    region = Region(round(found.x * info.width / width),
                    round(found.y * info.height / height),
                    max(4, round(found.width * info.width / width)),
                    max(4, round(found.height * info.height / height)))
    try:
        return validate_region(region, info), confidence
    except ValueError:
        return None, 0.0


def preview_data_url(source: Path, info: VideoInfo | None = None) -> str:
    info = info or inspect_video(source)
    width, height = _scaled_dimensions(info, 640)
    command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-ss", f"{info.duration * .25:.3f}", "-i", str(source),
               "-map", "0:v:0", "-frames:v", "1",
               "-vf", f"scale={width}:{height}", "-q:v", "5",
               "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("无法生成视频预览") from exc
    if result.returncode or not result.stdout.startswith(b"\xff\xd8"):
        raise ValueError("无法生成视频预览")
    return "data:image/jpeg;base64," + base64.b64encode(result.stdout).decode("ascii")


def render_delogo(source: Path, destination: Path, region: Region,
                  info: VideoInfo, cancelled: threading.Event,
                  on_progress: Callable[[float], None] | None = None) -> None:
    """Render to a new MP4 with container-compatible AAC audio streams."""
    validate_region(region, info)
    edge_pad = 12 if (region.x == 0 or region.y == 0
                      or region.x + region.width == info.width
                      or region.y + region.height == info.height) else 0
    if edge_pad:
        # FFmpeg delogo rejects regions on the image boundary because it
        # needs pixels beyond the rectangle for interpolation. Temporarily
        # extend the edge, then crop back to the original dimensions.
        filter_spec = (
            f"pad=iw+{2 * edge_pad}:ih+{2 * edge_pad}:{edge_pad}:{edge_pad},"
            f"fillborders=left={edge_pad}:right={edge_pad}:top={edge_pad}:"
            f"bottom={edge_pad}:mode=smear,"
            f"delogo=x={region.x + edge_pad}:y={region.y + edge_pad}:"
            f"w={region.width}:h={region.height}:show=0,"
            f"crop=iw-{2 * edge_pad}:ih-{2 * edge_pad}:{edge_pad}:{edge_pad}"
        )
    else:
        filter_spec = (f"delogo=x={region.x}:y={region.y}:w={region.width}:"
                       f"h={region.height}:show=0")
    command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
               "-progress", "pipe:1", "-i", str(source), "-map", "0:v:0",
               "-map", "0:a?", "-vf", filter_spec, "-c:v", "libx264",
               "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "192k", "-map_metadata", "0", "-map_chapters", "0"]
    if destination.suffix.lower() == ".mp4":
        command.extend(["-movflags", "+faststart"])
    command.append(str(destination))
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    import tempfile
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as error_log:
        try:
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=error_log,
                                    text=True, encoding="utf-8", errors="replace",
                                    creationflags=flags)
        except OSError as exc:
            raise RuntimeError("无法启动 FFmpeg，请检查本机视频处理组件") from exc

        def watch_cancel() -> None:
            while proc.poll() is None:
                if cancelled.wait(.2):
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break

        watcher = threading.Thread(target=watch_cancel, daemon=True)
        watcher.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        elapsed = int(line.split("=", 1)[1]) / 1_000_000
                        if on_progress:
                            on_progress(min(.99, max(0, elapsed / info.duration)))
                    except (ValueError, ZeroDivisionError):
                        pass
            code = proc.wait()
            if cancelled.is_set():
                raise InterruptedError("已停止")
            if code:
                error_log.seek(0)
                detail = error_log.read()[-600:].strip()
                raise RuntimeError(f"FFmpeg 处理失败：{detail or '未知错误'}")
            if not destination.is_file() or destination.stat().st_size == 0:
                raise RuntimeError("处理完成后没有生成有效视频")
            checked = probe_media(str(destination))
            video = next((stream for stream in (checked or {}).get('streams', [])
                          if stream.get('codec_type') == 'video'), None)
            has_audio = any(stream.get('codec_type') == 'audio'
                            for stream in (checked or {}).get('streams', []))
            if (video is None or int(video.get('width') or 0) != info.width
                    or int(video.get('height') or 0) != info.height
                    or (info.has_audio and not has_audio)):
                raise RuntimeError('输出视频校验失败：画面尺寸或音频丢失')
            if on_progress:
                on_progress(1.0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            watcher.join(timeout=.5)
