"""Server-controlled, semi-transparent brand mark for free exports."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Callable

from .ffmpeg import FFMPEG, probe_media, run_process
from .hardware import session_for


def brand_asset_path() -> Path:
    root = (Path(sys._MEIPASS) if getattr(sys, 'frozen', False)
            else Path(__file__).resolve().parents[2] / 'assets')
    return root / 'brand' / 'junxiaobai-watermark.png'


def apply_brand_watermark(
    output_path: str,
    config: dict,
    *,
    is_cancelled: Callable[[], bool] | None = None,
    on_process=None,
    on_progress: Callable[[float], None] | None = None,
) -> tuple[bool, str | None]:
    """Re-encode video once and copy its audio, replacing only after validation.

    Applying this *after* optional cover/variant processing prevents those
    stages from moving or obscuring the brand mark. No client-provided config
    can turn this operation off; callers decide using a server-side entitlement.
    """
    source = Path(output_path)
    asset = brand_asset_path()
    if not asset.is_file():
        return False, '内置俊小白水印资源缺失'
    media = probe_media(str(source))
    video = next((s for s in (media or {}).get('streams', [])
                  if s.get('codec_type') == 'video'), None)
    if not video:
        return False, '无法读取待输出视频画面'
    try:
        width, height = int(video['width']), int(video['height'])
        duration = float(video.get('duration') or media.get('format', {}).get('duration') or 0)
    except (TypeError, ValueError, KeyError):
        return False, '输出视频尺寸或时长无效'
    if width < 64 or height < 64 or duration <= 0:
        return False, '输出视频尺寸或时长无效'

    mark_h = max(20, round(height * 0.052))
    mark_w = max(32, round(mark_h * 600 / 160))
    graph = (
        f'[1:v]format=rgba,scale={mark_w}:{mark_h},'
        'colorchannelmixer=aa=0.42[brand];'
        '[0:v][brand]overlay=x=W-w-W*0.025:y=H*0.03:'
        'shortest=1:eof_action=pass:format=auto,format=yuv420p[vout]'
    )
    temporary = source.with_name(f'.{source.stem}.brand-{uuid.uuid4().hex[:8]}.mp4')
    base = [FFMPEG, '-y', '-i', str(source), '-loop', '1', '-i', str(asset),
            '-filter_complex', graph, '-map', '[vout]', '-map', '0:a?',
            '-c:a', 'copy', '-map_metadata', '0', '-pix_fmt', 'yuv420p']
    tail = ['-b:v', str(config.get('bitrate') or '8000k'), '-movflags', '+faststart',
            '-f', 'mp4', str(temporary)]
    try:
        success, error = session_for(config).run(
            base, tail, '品牌水印', is_cancelled, on_process,
            runner=lambda command: run_process(
                command, is_cancelled, on_process,
                progress_callback=on_progress, progress_duration=duration),
        )
        if not success:
            return False, error or '品牌水印叠加失败'
        checked = probe_media(str(temporary))
        checked_video = next((s for s in (checked or {}).get('streams', [])
                              if s.get('codec_type') == 'video'), None)
        if (not checked_video or not temporary.is_file() or temporary.stat().st_size < 1024
                or int(checked_video.get('width') or 0) != width
                or int(checked_video.get('height') or 0) != height):
            return False, '品牌水印输出校验失败'
        if is_cancelled and is_cancelled():
            return False, '已停止'
        os.replace(temporary, source)
        return True, None
    except (OSError, ValueError) as exc:
        return False, str(exc)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
