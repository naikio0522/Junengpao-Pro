import os
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
import asyncio
import json
import threading

from ..models.schemas import (
    CreateTaskRequest, CreateTaskResponse, StopTaskRequest,
    TaskStatus, ScanRequest, ScanResponse, ProbeResult, VideoConfig
)
from ..services.task_service import task_service
from ..core.ffmpeg import probe_media, extract_media_info, extract_audio_duration
from .standalone_variants import standalone_variant_service

router = APIRouter()


@router.post("/tasks", response_model=CreateTaskResponse)
def create_task(req: CreateTaskRequest):
    task_id = task_service.create_task(req.config)
    return CreateTaskResponse(task_id=task_id, message="任务已创建")


@router.get("/tasks", response_model=list[TaskStatus])
def list_tasks():
    return task_service.get_all_tasks()


@router.get("/tasks/{task_id}", response_model=TaskStatus)
def get_task(task_id: str):
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.post("/tasks/{task_id}/stop")
def stop_task(task_id: str):
    success = task_service.stop_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"message": "停止指令已发送"}


@router.post("/tasks/stop-all")
def stop_all_tasks():
    stopped = task_service.stop_all_tasks()
    standalone_stopped = standalone_variant_service.stop_all()
    return {"message": "停止指令已发送", "stopped": stopped + standalone_stopped}


@router.get("/tasks/{task_id}/logs")
def get_logs(task_id: str):
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"logs": task_service.get_logs(task_id)}


@router.get("/tasks/{task_id}/stream")
async def stream_logs(task_id: str):
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_generator():
        last_len = 0
        while True:
            logs = task_service.get_logs(task_id)
            if len(logs) > last_len:
                new_logs = logs[last_len:]
                for line in new_logs:
                    yield f"data: {json.dumps({'log': line}, ensure_ascii=False)}\n\n"
                last_len = len(logs)

            current = task_service.get_task(task_id)
            if current and current.status in ("completed", "failed", "stopped"):
                yield f"data: {json.dumps({'status': current.status, 'progress': current.progress}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )


@router.post("/scan", response_model=ScanResponse)
def scan_directory(req: ScanRequest):
    paths = [part.strip() for part in req.dir_path.split(';') if part.strip()]
    if not paths or any(not os.path.exists(path) for path in paths):
        raise HTTPException(status_code=400, detail="素材路径不存在")
    extensions = tuple(ext.lower() for ext in req.extensions)
    files = []
    seen = set()
    for path in paths:
        if os.path.isfile(path):
            candidates = [path] if path.lower().endswith(extensions) else []
        else:
            candidates = (os.path.join(root, name)
                          for root, _, filenames in os.walk(path)
                          for name in filenames if name.lower().endswith(extensions))
        for file_path in candidates:
            key = os.path.normcase(os.path.abspath(file_path))
            if key not in seen:
                seen.add(key)
                files.append(file_path)
    return ScanResponse(files=files, count=len(files))


@router.post("/probe", response_model=ProbeResult)
def probe_file(file_path: str):
    if not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail="文件不存在")
    info = probe_media(file_path)
    if not info:
        raise HTTPException(status_code=500, detail="无法探测文件")
    dur, has_audio, width, height, fps = extract_media_info(info, file_path)
    return ProbeResult(
        file_path=file_path,
        duration=dur,
        source_duration=extract_media_info(info, file_path, safety_margin=0)[0],
        audio_duration=extract_audio_duration(info, safety_margin=0),
        has_audio=has_audio,
        width=width,
        height=height,
        fps=fps
    )


@router.post("/benchmark")
def benchmark(config: VideoConfig):
    return task_service.get_benchmark(config)


@router.post("/preflight")
async def preflight(config: VideoConfig, request: Request):
    # Keep the JSON contract for existing clients. The desktop UI opts in to
    # streaming so it can display real completed-work progress during offline ASR.
    if 'text/event-stream' not in request.headers.get('accept', ''):
        return await asyncio.to_thread(task_service.preflight, config)

    loop = asyncio.get_running_loop()
    events: asyncio.Queue[dict] = asyncio.Queue()

    def emit(event: dict):
        loop.call_soon_threadsafe(events.put_nowait, event)

    def run_preflight():
        try:
            result = task_service.preflight(
                config,
                progress_callback=lambda percent, message: emit({
                    'type': 'progress', 'percent': percent, 'message': message,
                }),
            )
            emit({'type': 'result', 'result': result})
        except Exception as exc:
            emit({'type': 'error', 'message': str(exc)})

    async def event_generator():
        threading.Thread(target=run_preflight, daemon=True).start()
        while True:
            event = await events.get()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event['type'] in ('result', 'error'):
                break

    return StreamingResponse(
        event_generator(), media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@router.post("/history/clear")
def clear_history():
    task_service.clear_history()
    return {"message": "使用记录已清除"}
