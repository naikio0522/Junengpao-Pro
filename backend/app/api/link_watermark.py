"""Public short-video link resolution and authorized direct-stream export."""

from __future__ import annotations

import threading
import time
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from ..core.brand_watermark import apply_brand_watermark
from ..core.link_watermark import (
    LinkResolutionError, canonical_public_video, download_cover_image,
    download_direct_video, remux_to_mp4, resolve_public_video,
)
from ..services.account_service import AccountService, InvalidSession
from ..services.remote_account_service import AccountServiceUnavailable, RemoteAccountService
from .accounts import bearer, get_account_service


router = APIRouter(prefix='/link-watermark', tags=['link-watermark'])
RESOLVE_TTL_SECONDS = 10 * 60
JOB_TTL_SECONDS = 60 * 60
MAX_RETAINED_JOBS = 40


class ResolveRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4000)
    authorized: bool = False
    douyin_detail: dict | None = None


class LinkDownloadRequest(BaseModel):
    resolve_id: str = Field(min_length=8, max_length=80)
    format_id: str | None = None
    output_dir: str = Field(min_length=1, max_length=4096)
    authorized: bool = False
    save_cover: bool = False


class LinkWatermarkService:
    def __init__(self):
        self.lock = threading.Lock()
        self.resolved: dict[str, tuple[float, dict]] = {}
        self.jobs: dict[str, dict] = {}
        self.events: dict[str, threading.Event] = {}
        self.finished_at: dict[str, float] = {}

    def _prune_jobs_locked(self) -> None:
        now = time.monotonic()
        expired = [task_id for task_id, finished in self.finished_at.items()
                   if now - finished > JOB_TTL_SECONDS]
        excess = max(0, len(self.finished_at) - MAX_RETAINED_JOBS)
        oldest = sorted(self.finished_at, key=self.finished_at.get)[:excess]
        for task_id in set(expired + oldest):
            # Running tasks never enter finished_at and are never pruned.
            self.finished_at.pop(task_id, None)
            self.jobs.pop(task_id, None)
            self.events.pop(task_id, None)

    def resolve(self, request: ResolveRequest) -> dict:
        if not request.authorized:
            raise LinkResolutionError('请确认您拥有视频版权或处理授权')
        if request.douyin_detail is not None and len(json.dumps(request.douyin_detail)) > 128 * 1024:
            raise LinkResolutionError('播放器返回的数据过大')
        result = resolve_public_video(request.url, douyin_detail=request.douyin_detail)
        resolve_id = uuid.uuid4().hex
        with self.lock:
            now = time.monotonic()
            self.resolved = {key: value for key, value in self.resolved.items()
                             if value[0] > now}
            # No unbounded collection of signed URLs or metadata in memory.
            if len(self.resolved) >= 32:
                oldest = min(self.resolved, key=lambda key: self.resolved[key][0])
                del self.resolved[oldest]
            self.resolved[resolve_id] = (now + RESOLVE_TTL_SECONDS, result)
        return {
            'resolve_id': resolve_id,
            'platform': result['platform'], 'title': result['title'],
            'thumbnail_url': result['thumbnail_url'],
            'webpage_url': result['webpage_url'],
            'video_url': result['video_url'],
            'watermark_status': result['watermark_status'],
            'warning': result['warning'],
            'formats': [
                {key: item[key] for key in ('id', 'label', 'width', 'height', 'ext',
                                             'filesize', 'watermark_status')}
                for item in result['formats']
            ],
        }

    def create(self, request: LinkDownloadRequest, *, is_member: bool = False) -> dict:
        if not request.authorized:
            raise LinkResolutionError('请确认您拥有视频版权或处理授权')
        output_dir = Path(request.output_dir).expanduser().resolve()
        if output_dir.exists() and not output_dir.is_dir():
            raise LinkResolutionError('输出位置不是文件夹')
        with self.lock:
            self._prune_jobs_locked()
            cached = self.resolved.get(request.resolve_id)
            if not cached or cached[0] <= time.monotonic():
                raise LinkResolutionError('解析结果已过期，请重新解析链接')
            source = cached[1]
            format_id = request.format_id or source['formats'][0]['id']
            selected = next((item for item in source['formats'] if item['id'] == format_id), None)
            if selected is None:
                raise LinkResolutionError('画质选项无效，请重新解析链接')
            active = sum(job['status'] in ('pending', 'running') for job in self.jobs.values())
            if active >= 2:
                raise LinkResolutionError('已有 2 个链接任务在处理，请稍后再试')
            task_id = uuid.uuid4().hex
            self.jobs[task_id] = {
                'task_id': task_id, 'status': 'pending', 'stage': '排队中',
                'progress': 0, 'output_files': [], 'errors': [], 'log_lines': [],
                'created_at': datetime.now(timezone.utc).isoformat(),
            }
            self.events[task_id] = threading.Event()
        thread = threading.Thread(
            target=self._run, args=(task_id, source, selected, output_dir,
                                    is_member, request.save_cover),
            daemon=True,
        )
        thread.start()
        return {'task_id': task_id}

    def _run(self, task_id: str, source: dict, selected: dict,
             output_dir: Path, is_member: bool, save_cover: bool) -> None:
        cancelled = self.events[task_id]
        destination = None
        temp_path = None
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            name = f"{source['platform_key']}_{source['source_id']}_{task_id[:8]}"
            temp_path = output_dir / f'.{name}.download'
            destination = output_dir / f'{name}.mp4'
            self._update(task_id, status='running', stage='下载视频流', progress=2)
            self._log(task_id, '正在下载平台提供的直接视频流；无法保证画面本身无水印。')
            download_direct_video(
                selected, source['platform_key'], temp_path, cancelled.is_set,
                lambda fraction: self._update(task_id, progress=min(68, round(2 + fraction * 66))),
            )
            if cancelled.is_set():
                raise InterruptedError('已停止')
            self._update(task_id, stage='校验并封装 MP4', progress=68)
            remux_to_mp4(
                temp_path, destination, cancelled.is_set,
                lambda fraction: self._update(task_id, progress=min(78, round(68 + fraction * 10))),
            )
            if cancelled.is_set():
                raise InterruptedError('已停止')
            if not is_member:
                self._update(task_id, stage='叠加免费版品牌水印', progress=79)
                self._log(task_id, '免费版正在叠加俊小白品牌水印。')
                ok, error = apply_brand_watermark(
                    str(destination), {'enable_gpu': False, 'bitrate': '5000k',
                                       'concurrent_tasks': 1},
                    is_cancelled=cancelled.is_set,
                    on_progress=lambda fraction: self._update(
                        task_id, progress=min(99, round(79 + fraction * 20))),
                )
                if not ok:
                    if cancelled.is_set():
                        raise InterruptedError('已停止')
                    raise RuntimeError(error or '俊小白品牌水印叠加失败')
            if cancelled.is_set():
                raise InterruptedError('已停止')
            cover = None
            if save_cover and source.get('thumbnail_url'):
                try:
                    cover = download_cover_image(
                        source['thumbnail_url'], source['platform_key'],
                        output_dir / f'{name}_封面', cancelled.is_set,
                    )
                except InterruptedError:
                    raise
                except Exception:
                    self._log(task_id, '封面保存失败；视频已正常输出。')
                else:
                    self._log(task_id, f'封面已保存：{cover.name}')
            self._append(task_id, 'output_files', str(destination))
            if cover is not None:
                self._append(task_id, 'output_files', str(cover))
            self._update(task_id, stage='已完成', status='completed', progress=100)
            self._log(task_id, f'下载完成：{destination.name}')
        except InterruptedError:
            self._update(task_id, stage='已停止', status='stopped')
            self._log(task_id, '任务已停止。')
        except Exception as exc:
            # No raw signed CDN URL, cookies, stack trace, or output path in UI.
            message = str(exc) if isinstance(exc, (LinkResolutionError, RuntimeError)) else '视频下载失败，请重新解析或稍后重试'
            self._append(task_id, 'errors', message)
            self._update(task_id, stage='失败', status='failed')
            self._log(task_id, message)
        finally:
            if self.jobs[task_id]['status'] != 'completed' and destination is not None:
                destination.unlink(missing_ok=True)
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def _update(self, task_id: str, **values) -> None:
        with self.lock:
            self.jobs[task_id].update(values)
            if values.get('status') in ('completed', 'failed', 'stopped'):
                self.finished_at[task_id] = time.monotonic()
                self._prune_jobs_locked()

    def _append(self, task_id: str, field: str, value: str) -> None:
        with self.lock:
            self.jobs[task_id][field].append(value)

    def _log(self, task_id: str, line: str) -> None:
        self._append(task_id, 'log_lines', line)

    def get(self, task_id: str) -> dict | None:
        with self.lock:
            self._prune_jobs_locked()
            job = self.jobs.get(task_id)
            return {**job, 'output_files': list(job['output_files']),
                    'errors': list(job['errors']),
                    'log_lines': list(job['log_lines'])} if job else None

    def stop(self, task_id: str) -> bool:
        with self.lock:
            event = self.events.get(task_id)
            if event is None:
                return False
            event.set()
            return True

    def stop_all(self) -> int:
        with self.lock:
            active = [task_id for task_id, job in self.jobs.items()
                      if job['status'] in ('pending', 'running')]
            for task_id in active:
                self.events[task_id].set()
            return len(active)


link_watermark_service = LinkWatermarkService()


@router.post('/canonicalize')
def canonicalize_link(request: ResolveRequest):
    if not request.authorized:
        raise HTTPException(status_code=422, detail='请确认您拥有视频版权或处理授权')
    try:
        page_url, platform = canonical_public_video(request.url)
        return {
            'page_url': page_url, 'platform_key': platform,
            'source_id': page_url.split('?', 1)[0].rstrip('/').rsplit('/', 1)[-1],
        }
    except LinkResolutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/resolve')
def resolve_link(request: ResolveRequest):
    try:
        return link_watermark_service.resolve(request)
    except LinkResolutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/jobs')
def create_job(
    request: LinkDownloadRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    accounts: AccountService | RemoteAccountService = Depends(get_account_service),
):
    try:
        is_member = False
        if credentials is not None:
            try:
                is_member = accounts.is_member(credentials.credentials)
            except (InvalidSession, AccountServiceUnavailable):
                pass
        return link_watermark_service.create(request, is_member=is_member)
    except LinkResolutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get('/jobs/{task_id}')
def get_job(task_id: str):
    job = link_watermark_service.get(task_id)
    if job is None:
        raise HTTPException(status_code=404, detail='任务不存在')
    return job


@router.post('/jobs/{task_id}/stop')
def stop_job(task_id: str):
    if not link_watermark_service.stop(task_id):
        raise HTTPException(status_code=404, detail='任务不存在')
    return {'message': '停止指令已发送'}
