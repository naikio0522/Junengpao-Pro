"""Local watermark cleanup for footage the user is authorized to edit."""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from ..core.watermark_removal import (
    Region, detect_region, inspect_video, preview_data_url, render_delogo,
    scale_region,
)
from ..core.brand_watermark import apply_brand_watermark
from ..services.account_service import AccountService, InvalidSession
from ..services.remote_account_service import AccountServiceUnavailable, RemoteAccountService
from .accounts import bearer, get_account_service
from .standalone_variants import _collect_inputs


router = APIRouter(prefix='/watermark-removal', tags=['watermark-removal'])


class RegionInput(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=4)
    height: int = Field(ge=4)

    def to_region(self) -> Region:
        return Region(self.x, self.y, self.width, self.height)


class DetectRequest(BaseModel):
    input_path: str


class CleanupRequest(BaseModel):
    input_paths: list[str] = Field(min_length=1)
    output_dir: str = ''
    mode: Literal['auto', 'manual'] = 'auto'
    region: RegionInput | None = None
    reference_width: int | None = Field(default=None, ge=16)
    reference_height: int | None = Field(default=None, ge=16)
    authorized: bool = False


class WatermarkRemovalService:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.events: dict[str, threading.Event] = {}
        self.lock = threading.Lock()

    def create(self, request: CleanupRequest, *, is_member: bool = False) -> dict:
        if not request.authorized:
            raise ValueError('请确认您拥有素材版权或处理授权')
        if request.mode == 'manual' and request.region is None:
            raise ValueError('手动模式请先选择要处理的画面区域')
        first = Path(request.input_paths[0]).expanduser().resolve()
        default_parent = first.parent if first.is_file() else first
        output_dir = (Path(request.output_dir).expanduser().resolve() if request.output_dir.strip()
                      else (default_parent / '去水印输出').resolve())
        if output_dir.exists() and not output_dir.is_dir():
            raise ValueError('输出位置不是文件夹')
        inputs = _collect_inputs(request.input_paths, output_dir)
        task_id = uuid.uuid4().hex
        job = {
            'task_id': task_id, 'status': 'pending', 'progress': 0,
            'current': 0, 'total': len(inputs), 'output_files': [],
            'errors': [], 'log_lines': [], 'created_at': datetime.now(timezone.utc).isoformat(),
        }
        with self.lock:
            self.jobs[task_id] = job
            self.events[task_id] = threading.Event()
        threading.Thread(target=self._run, args=(task_id, inputs, output_dir, request, is_member), daemon=True).start()
        return {'task_id': task_id}

    def _run(self, task_id: str, inputs: list[Path], output_dir: Path,
             request: CleanupRequest, is_member: bool = False) -> None:
        cancelled = self.events[task_id]
        self._update(task_id, status='running')
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            for index, source in enumerate(inputs):
                if cancelled.is_set():
                    break
                self._log(task_id, f'{source.name}：正在定位水印')
                destination = None
                try:
                    info = inspect_video(source)
                    if request.mode == 'auto':
                        region, confidence = detect_region(source, info)
                        if region is None:
                            raise ValueError('未可靠定位静态角标；请改用手动框选，避免误伤画面')
                        self._log(task_id, f'{source.name}：自动定位区域 {region.as_dict()}，置信度 {confidence:.0%}')
                    else:
                        region = scale_region(
                            request.region.to_region(), request.reference_width,
                            request.reference_height, info,
                        )
                        self._log(task_id, f'{source.name}：使用手动区域 {region.as_dict()}')
                    destination = self._destination(output_dir, source)
                    render_delogo(
                        source, destination, region, info, cancelled,
                        on_progress=lambda fraction, index=index: self._update(
                            task_id, progress=min(99, round(100 * (index + .05 +
                                (.73 if not is_member else .94) * fraction) / len(inputs))),
                        ),
                    )
                    if cancelled.is_set():
                        raise InterruptedError('已停止')
                    if not is_member:
                        self._log(task_id, f'{source.name}：免费版正在叠加俊小白品牌水印')
                        bitrate_k = max(500, min(12000, round(info.width * info.height * .0025)))
                        ok, error = apply_brand_watermark(
                            str(destination),
                            {'enable_gpu': False, 'bitrate': f'{bitrate_k}k', 'concurrent_tasks': 1},
                            is_cancelled=cancelled.is_set,
                            on_progress=lambda fraction, index=index: self._update(
                                task_id, progress=min(99, round(100 * (index + .78 + .21 * fraction) / len(inputs))),
                            ),
                        )
                        if not ok:
                            if cancelled.is_set():
                                raise InterruptedError('已停止')
                            raise RuntimeError(error or '俊小白品牌水印叠加失败')
                    self._append(task_id, 'output_files', str(destination))
                    self._log(task_id, f'{source.name}：完成 → {destination.name}')
                except InterruptedError:
                    if destination is not None:
                        destination.unlink(missing_ok=True)
                    break
                except Exception as exc:
                    if destination is not None:
                        destination.unlink(missing_ok=True)
                    self._append(task_id, 'errors', f'{source.name}：{exc}')
                    self._log(task_id, f'{source.name}：失败，{exc}')
                self._update(task_id, current=index + 1,
                             progress=min(99, round(100 * (index + 1) / len(inputs))))
            if cancelled.is_set():
                self._update(task_id, status='stopped')
            else:
                self._update(task_id, status='completed' if self.jobs[task_id]['output_files'] else 'failed',
                             progress=100)
        except Exception as exc:
            self._append(task_id, 'errors', str(exc))
            self._update(task_id, status='failed')

    @staticmethod
    def _destination(output_dir: Path, source: Path) -> Path:
        # Unique name avoids two concurrent jobs selecting the same numbered
        # path; on failure we only remove a file created by this job.
        return output_dir / f'{source.stem}_去水印_{uuid.uuid4().hex[:12]}.mp4'

    def _update(self, task_id: str, **values) -> None:
        with self.lock:
            self.jobs[task_id].update(values)

    def _append(self, task_id: str, field: str, value: str) -> None:
        with self.lock:
            self.jobs[task_id][field].append(value)

    def _log(self, task_id: str, line: str) -> None:
        self._append(task_id, 'log_lines', line)

    def get(self, task_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(task_id)
            return {**job, 'output_files': list(job['output_files']),
                    'errors': list(job['errors']), 'log_lines': list(job['log_lines'])} if job else None

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

    def active_count(self) -> int:
        with self.lock:
            return sum(job['status'] in ('pending', 'running') for job in self.jobs.values())


watermark_removal_service = WatermarkRemovalService()


@router.post('/detect')
def detect(request: DetectRequest):
    try:
        source = Path(request.input_path).expanduser().resolve()
        info = inspect_video(source)
        region, confidence = detect_region(source, info)
        return {
            'width': info.width, 'height': info.height,
            'region': region.as_dict() if region else None,
            'confidence': confidence,
            'preview': preview_data_url(source, info),
            'message': ('已找到可能的静态角标，请核对画面。' if region
                        else '未可靠定位角标，请手动框选后处理。'),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/jobs')
def create_job(
    request: CleanupRequest,
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
        return watermark_removal_service.create(request, is_member=is_member)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get('/jobs/{task_id}')
def get_job(task_id: str):
    job = watermark_removal_service.get(task_id)
    if job is None:
        raise HTTPException(status_code=404, detail='任务不存在')
    return job


@router.post('/jobs/{task_id}/stop')
def stop_job(task_id: str):
    if not watermark_removal_service.stop(task_id):
        raise HTTPException(status_code=404, detail='任务不存在')
    return {'message': '停止指令已发送'}
