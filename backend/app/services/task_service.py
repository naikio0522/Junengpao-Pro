import os
import json
import time
import uuid
import threading
import concurrent.futures
import random
import subprocess
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Callable

from ..core.video_matrix import VideoMatrixCore, SharedMediaCache
from ..core.video_variant import VideoVariantProcessor, derive_variant_seed
from ..core.timeline import apply_timeline_totals
from ..core.video_cover import RandomCoverProcessor
from ..core.brand_watermark import apply_brand_watermark
from ..core.hardware import HardwareSession
from ..models.schemas import VideoConfig, TaskStatus


class TaskService:
    # 压测只是选并发，不应让一轮异常编码无限占用机器。
    BENCHMARK_ROUND_TIMEOUT_SECONDS = 300

    def __init__(self, shared_cache: Optional[SharedMediaCache] = None):
        self.shared_cache = shared_cache or SharedMediaCache()
        self.tasks: Dict[str, TaskStatus] = {}
        self.active_cores: Dict[str, List[VideoMatrixCore]] = {}
        self.cores_lock = threading.Lock()
        self.log_buffers: Dict[str, List[str]] = {}
        self._log_lock = threading.Lock()
        self.variant_processor = VideoVariantProcessor()
        self.cover_processor = RandomCoverProcessor()
        self.variant_processes: Dict[str, set[subprocess.Popen]] = {}

    def _create_log_callback(self, task_id: str) -> Callable:
        def callback(message: str):
            with self._log_lock:
                if task_id not in self.log_buffers:
                    self.log_buffers[task_id] = []
                self.log_buffers[task_id].append(message)
            if task_id in self.tasks:
                self.tasks[task_id].log_lines.append(message)
        return callback

    def _normalize_config(self, config: dict) -> dict:
        normalized = config.copy()
        body_dirs = normalized.get('body_dirs') or []
        if isinstance(body_dirs, str):
            body_dirs = [p.strip() for p in body_dirs.split(';') if p.strip()]
        normalized['body_dirs'] = body_dirs

        if normalized.get('body_mode', 'normal') == 'grouped':
            normalized['body_groups'] = list(normalized.get('body_groups') or [])
        apply_timeline_totals(normalized)

        if normalized.get('enable_variants') and normalized.get('variant_seed') is None:
            normalized['variant_seed'] = random.SystemRandom().randrange(0, 2**63)
        if normalized.get('enable_random_cover'):
            # Cover randomness is intentionally independent from optional variant settings.
            normalized['_cover_seed'] = random.SystemRandom().randrange(0, 2**63)

        if not normalized.get('base_out_dir', '').strip():
            h_dir = (normalized.get('hook_dir', '').split(';')[0]).strip().rstrip('/\\')
            parent = os.path.dirname(h_dir) or h_dir
            name = os.path.basename(h_dir) or "VideoMatrix"
            if os.path.isfile(h_dir):
                name = os.path.splitext(name)[0]
            if len([part for part in normalized.get('hook_dir', '').split(';') if part.strip()]) > 1:
                name += '_Multi_Hook'
            normalized['base_out_dir'] = os.path.join(parent, f"{name}_VideoMatrix_Output")

        return normalized

    def _get_tasks_from_config(self, config: dict) -> List[dict]:
        tasks = []
        h_dir = config['hook_dir'].strip()
        hook_paths = [part.strip() for part in h_dir.split(';') if part.strip()]
        body_dirs = config.get('body_dirs') or [h_dir]
        out_base = config['base_out_dir'].strip()
        # A single Hook video is a first-class input, not a directory to list.
        sub_folders = (
            [f for f in os.listdir(h_dir) if os.path.isdir(os.path.join(h_dir, f))]
            if len(hook_paths) == 1 and os.path.isdir(h_dir) else []
        )
        if sub_folders:
            for sub in sub_folders:
                task_h = os.path.join(h_dir, sub)
                task_b = []
                for body_dir in body_dirs:
                    potential_b_sub = os.path.join(body_dir, sub)
                    task_b.append(potential_b_sub if os.path.isdir(potential_b_sub) else body_dir)
                tasks.append({
                    'name': sub,
                    'hook_dir': task_h,
                    'body_dirs': task_b,
                    'out': os.path.join(out_base, sub)
                })
        else:
            first_hook = hook_paths[0] if hook_paths else h_dir
            task_name = os.path.basename(first_hook.rstrip('/\\')) or "Single_Task"
            if os.path.isfile(first_hook):
                task_name = os.path.splitext(task_name)[0]
            if len(hook_paths) > 1:
                task_name += '_Multi_Hook'
            tasks.append({
                'name': task_name,
                'hook_dir': h_dir,
                'body_dirs': body_dirs,
                'out': os.path.join(out_base, task_name)
            })
        return tasks

    def create_task(self, config: VideoConfig, *, is_member: bool = False) -> str:
        task_id = str(uuid.uuid4())
        raw_config = self._normalize_config(config.model_dump())
        task_cfg = raw_config.copy()
        # The client cannot choose whether to suppress the brand watermark.
        # Entitlement is resolved from the server-side account session.
        task_cfg['_brand_watermark_required'] = not is_member

        status = TaskStatus(
            task_id=task_id,
            task_name=task_cfg.get('task_name', 'Task'),
            status="pending",
            created_at=datetime.now()
        )
        self.tasks[task_id] = status
        self.log_buffers[task_id] = []

        thread = threading.Thread(
            target=self._run_pipeline,
            args=(task_id, task_cfg),
            daemon=True
        )
        thread.start()
        return task_id

    def _run_pipeline(self, task_id: str, config: dict):
        status = self.tasks[task_id]
        log_cb = self._create_log_callback(task_id)
        status.status = "running"
        status.updated_at = datetime.now()
        cores = []

        try:
            # One verified hardware session is shared by all SKU cores and all
            # post-processing stages. This prevents each stage from probing or
            # oversubscribing the same encoder independently.
            hardware_session = HardwareSession(
                config,
                log=log_cb,
                update=lambda values: self._update_acceleration(status, values),
                cancelled=lambda: status.status == "stopped",
            )
            config['_hardware_session'] = hardware_session
            tasks = self._get_tasks_from_config(config)
            if not tasks:
                log_cb(">>> [错误] 找不到任何素材目录！")
                status.status = "failed"
                status.message = "找不到素材目录"
                return

            for task_number, t in enumerate(tasks):
                if status.status == "stopped":
                    break
                task_cfg = config.copy()
                task_cfg['task_name'] = t['name']
                task_cfg['hook_dir'] = t['hook_dir']
                task_cfg['body_dirs'] = t['body_dirs']
                # Group folders are global configuration, not inferred from Hook subfolders.
                task_cfg['body_groups'] = config.get('body_groups') or []
                task_cfg['out_dir'] = t['out']
                task_cfg['_hardware_session'] = hardware_session
                os.makedirs(task_cfg['out_dir'], exist_ok=True)

                core = VideoMatrixCore(task_cfg, log_cb, self.shared_cache)

                def report_preflight(stage: str, current: int, total: int,
                                     task_number: int = task_number) -> None:
                    if status.status == "stopped":
                        return
                    fraction = min(1.0, max(0.0, current / max(1, total)))
                    if stage == 'scan':
                        local_progress = 0.02
                    elif stage == 'probe':
                        end = 0.45 if task_cfg.get('selection_mode') == 'speech_logic' else 0.95
                        local_progress = 0.02 + (end - 0.02) * fraction
                    elif stage == 'transcribe':
                        local_progress = 0.45 + 0.50 * fraction
                    else:
                        return
                    status.progress = max(status.progress, min(14, int(
                        15 * (task_number + local_progress) / len(tasks)
                    )))
                    status.updated_at = datetime.now()

                ok, msg = core.pre_flight_check(progress_callback=report_preflight)
                status.progress = max(status.progress, min(15, int(15 * (task_number + 1) / len(tasks))))
                if ok:
                    cores.append(core)
                    with self.cores_lock:
                        if task_id not in self.active_cores:
                            self.active_cores[task_id] = []
                        self.active_cores[task_id].append(core)
                else:
                    log_cb(f"[{t['name']}] 预检拦截: {msg}")
                    core.temp_dir.cleanup()

            if not cores:
                log_cb(">>> 所有库均被拦截，任务终止。")
                status.status = "failed"
                status.message = "所有库预检失败"
                return

            jobs = []
            max_t = max([c.config['target_count'] for c in cores], default=0)
            for i in range(1, max_t + 1):
                for core in cores:
                    if i <= core.config['target_count']:
                        jobs.append((core, i))

            status.total = len(jobs)
            status.progress = max(status.progress, 15)
            log_cb(f">>> [系统] 任务池组装完毕，即将并线生成 {len(jobs)} 个视频。")

            success_counts = {core.task_name: 0 for core in cores}
            completed = 0
            partial_progress: Dict[tuple[int, int], float] = {}
            progress_lock = threading.Lock()

            def update_render_progress(key: tuple[int, int], fraction: float) -> None:
                if status.status == 'stopped':
                    return
                with progress_lock:
                    partial_progress[key] = max(partial_progress.get(key, 0.0), min(0.98, max(0.0, fraction)))
                    status.progress = max(status.progress, min(99, int(
                        15 + 85 * (completed + sum(partial_progress.values())) / len(jobs)
                    )))
                    status.updated_at = datetime.now()

            concurrent_limit = max(1, int(config.get('concurrent_tasks', 3)))
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrent_limit) as executor:
                futures = {
                    executor.submit(
                        self._render_job, core, idx, status,
                        lambda fraction, key=(id(core), idx): update_render_progress(key, fraction),
                    ): (core, idx)
                    for core, idx in jobs
                }
                for future in concurrent.futures.as_completed(futures):
                    if status.status == "stopped":
                        break
                    core, idx = futures[future]
                    try:
                        if future.result():
                            success_counts[core.task_name] += 1
                    except Exception as e:
                        log_cb(f"[{core.task_name}] 渲染异常: {e}")
                    with progress_lock:
                        partial_progress.pop((id(core), idx), None)
                        completed += 1
                        status.current = completed
                        status.progress = max(status.progress, min(99, int(
                            15 + 85 * (completed + sum(partial_progress.values())) / len(jobs)
                        )))
                        status.updated_at = datetime.now()

            if status.status != "stopped":
                output_count = sum(success_counts.values())
                if output_count == 0:
                    status.status = "failed"
                    status.message = f"渲染失败：{len(jobs)} 个任务均未输出视频；请查看日志"
                    log_cb(f">>> [失败] {status.message}")
                else:
                    status.status = "completed"
                    status.progress = 100
                    if output_count < len(jobs):
                        status.message = f"部分完成：成功 {output_count}/{len(jobs)} 条；请查看失败日志"
                        log_cb(f">>> [警告] {status.message}")
                    else:
                        log_cb(">>> [完成] 渲染任务队列执行完毕！")
                for core_name, cnt in success_counts.items():
                    target = next(c.config['target_count'] for c in cores if c.task_name == core_name)
                    log_cb(f"    [{core_name}] 最终产量: {cnt}/{target}")

        except Exception as e:
            log_cb(f"[严重错误] 调度引擎崩溃: {str(e)}")
            status.status = "failed"
            status.message = str(e)
        finally:
            status.updated_at = datetime.now()
            with self.cores_lock:
                self.active_cores.pop(task_id, None)
                self.variant_processes.pop(task_id, None)
            for core in cores:
                core.temp_dir.cleanup()

    @staticmethod
    def _update_acceleration(status: TaskStatus, values: dict):
        """Expose the effective backend without making users run a report."""
        if "acceleration" in values:
            status.acceleration = str(values["acceleration"] or "")
        if "acceleration_warning" in values:
            status.acceleration_warning = str(values["acceleration_warning"] or "")
        if "effective_concurrency" in values:
            status.effective_concurrency = max(0, int(values["effective_concurrency"]))

    def _render_job(self, core: VideoMatrixCore, idx: int, status: TaskStatus,
                    on_progress: Optional[Callable[[float], None]] = None) -> bool:
        if status.status == "stopped" or not core.is_running:
            return False
        variant_share = 0.13 if core.config.get('enable_variants') else 0.0
        cover_share = 0.06 if core.config.get('enable_random_cover') else 0.0
        brand_share = 0.16 if core.config.get('_brand_watermark_required') else 0.0
        render_share = 1.0 - variant_share - cover_share - brand_share
        render_kwargs = {'return_result': True}
        if on_progress is not None:
            render_kwargs['on_progress'] = lambda fraction: on_progress(render_share * fraction)
        result, output_path, elapsed = core.render_single_video(idx, **render_kwargs)
        output_config = getattr(core, 'output_configs', {}).pop(idx, core.config)
        if on_progress is not None and result:
            on_progress(render_share)
        if result and output_path and core.config.get('enable_variants') and not output_config.get('_variant_applied'):
            variant_started = time.time()
            seed = derive_variant_seed(
                int(core.config.get('variant_seed') or 0), core.task_name, idx,
            )
            try:
                ok, error, summary = self.variant_processor.process(
                    output_path,
                    output_config,
                    seed,
                    is_cancelled=lambda: status.status == "stopped" or not core.is_running,
                    on_process=lambda process: self._track_variant_process(status.task_id, process),
                    on_stage=(lambda _message, fraction: on_progress(
                        render_share + variant_share * fraction,
                    ) if fraction is not None else None) if on_progress is not None else None,
                )
            except Exception as exc:
                ok, error, summary = False, str(exc), None
            elapsed = round((elapsed or 0) + time.time() - variant_started, 1)
            if status.status == "stopped" or not core.is_running:
                return False
            if ok:
                core.log(
                    f"    [{core.task_name}] 成品变换完成：{summary['segments']} 段独立随机，"
                    f"镜像 {summary['mirrored']} 段，帧混合 {summary['frame_mixed']} 段，"
                    f"seed={summary['seed']}"
                )
            else:
                core.log(f"    [{core.task_name}] 成品变换警告：{error or '处理失败'}，已保留原成片。")
        if on_progress is not None and result:
            on_progress(render_share + variant_share)
        if result and output_path and core.config.get('enable_random_cover'):
            cover_started = time.time()
            seed = derive_variant_seed(
                int(core.config.get('_cover_seed') or 0), core.task_name + ':cover', idx,
            )
            try:
                ok, error, summary = self.cover_processor.process(
                    output_path, output_config, seed,
                    is_cancelled=lambda: status.status == "stopped" or not core.is_running,
                    on_process=lambda process: self._track_variant_process(status.task_id, process),
                )
            except Exception as exc:
                ok, error, summary = False, str(exc), None
            elapsed = round((elapsed or 0) + time.time() - cover_started, 1)
            if status.status == "stopped" or not core.is_running:
                return False
            if ok:
                core.log(f"    [{core.task_name}] 随机封面完成（{summary['mode']}）：取样 {summary['sample_time']:.3f} 秒，zoom={summary['zoom']:.3f}")
            else:
                core.log(f"    [{core.task_name}] 随机封面警告：{error or '处理失败'}，已保留原成片。")
        if on_progress is not None and result:
            on_progress(render_share + variant_share + cover_share)
        if result and output_path and output_config.get('_brand_watermark_required'):
            brand_started = time.time()
            ok, error = apply_brand_watermark(
                output_path, output_config,
                is_cancelled=lambda: status.status == 'stopped' or not core.is_running,
                on_process=lambda process: self._track_variant_process(status.task_id, process),
                on_progress=(lambda fraction: on_progress(
                    render_share + variant_share + cover_share + brand_share * fraction,
                )) if on_progress is not None else None,
            )
            elapsed = round((elapsed or 0) + time.time() - brand_started, 1)
            if not ok:
                # Fail closed: a free export must never be handed off without
                # its required brand mark, including when FFmpeg is unavailable.
                VideoVariantProcessor._unlink_with_retry(output_path)
                core.log(f"    [{core.task_name}] 品牌水印失败：{error or '未知错误'}；未保留无水印成片。")
                return False
            core.log(f"    [{core.task_name}] 已为非会员成片叠加半透明俊小白水印。")
        if on_progress is not None and result:
            on_progress(1.0)
        if result and output_path and status.task_id in self.tasks:
            if output_path not in self.tasks[status.task_id].output_files:
                self.tasks[status.task_id].output_files.append(output_path)
            if elapsed is not None:
                self.tasks[status.task_id].output_elapsed[output_path] = elapsed
        return result

    def _track_variant_process(self, task_id: str, process: Optional[subprocess.Popen]):
        with self.cores_lock:
            processes = self.variant_processes.setdefault(task_id, set())
            if process is None:
                processes_copy = {item for item in processes if item.poll() is None}
                if processes_copy:
                    self.variant_processes[task_id] = processes_copy
                else:
                    self.variant_processes.pop(task_id, None)
            else:
                processes.add(process)

    def stop_task(self, task_id: str) -> bool:
        if task_id not in self.tasks:
            return False
        self.tasks[task_id].status = "stopped"
        with self.cores_lock:
            cores = self.active_cores.get(task_id, [])
            for core in cores:
                core.stop()
            for process in list(self.variant_processes.get(task_id, set())):
                try:
                    process.terminate()
                except Exception:
                    pass
            self.variant_processes.pop(task_id, None)
        return True

    def stop_all_tasks(self) -> int:
        stopped = 0
        active_ids = [
            task_id for task_id, task in self.tasks.items()
            if task.status in ("pending", "running")
        ]
        for task_id in active_ids:
            if self.stop_task(task_id):
                stopped += 1
        return stopped

    def get_task(self, task_id: str) -> Optional[TaskStatus]:
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> List[TaskStatus]:
        return list(self.tasks.values())

    def get_logs(self, task_id: str) -> List[str]:
        with self._log_lock:
            return list(self.log_buffers.get(task_id, []))

    def clear_history(self):
        self.shared_cache.clear_history()

    def preflight(self, config: VideoConfig,
                  progress_callback: Optional[Callable[[int, str], None]] = None) -> dict:
        raw_config = self._normalize_config(config.model_dump())
        tasks = self._get_tasks_from_config(raw_config)
        if not tasks:
            if progress_callback:
                progress_callback(100, '预检结束：找不到素材目录')
            return {"ok": False, "error": "找不到任何素材目录", "report": []}

        report = []
        total_capacity = 0
        if progress_callback:
            progress_callback(0, f'待检查 {len(tasks)} 个素材库')
        for task_index, t in enumerate(tasks):
            task_cfg = raw_config.copy()
            task_cfg['task_name'] = t['name']
            task_cfg['hook_dir'] = t['hook_dir']
            task_cfg['body_dirs'] = t['body_dirs']
            task_cfg['body_groups'] = raw_config.get('body_groups') or []

            def on_core_progress(phase: str, completed: int, total: int):
                # Percent advances only when scanning, probing or transcription
                # reports completed work. It never advances on a timer.
                is_speech = raw_config.get('selection_mode') == 'speech_logic'
                phase_ranges = {
                    'scan': (0, 5, '扫描完成'),
                    'probe': (5, 45 if is_speech else 95, '探测媒体'),
                    'transcribe': (45, 95, '转录口播'),
                }
                start, end, label = phase_ranges[phase]
                fraction = min(1.0, max(0.0, completed / total)) if total else 1.0
                local_percent = start + (end - start) * fraction
                overall = min(99, int((task_index * 100 + local_percent) / len(tasks)))
                if progress_callback:
                    progress_callback(overall, f'{t["name"]}：{label} {completed}/{total}')

            core = VideoMatrixCore(task_cfg, lambda _x: None, self.shared_cache)
            ok, msg = core.pre_flight_check(
                progress_callback=on_core_progress if progress_callback else None)
            speech_preview = None
            if raw_config.get('selection_mode') == 'speech_logic':
                first_plan = core.semantic_plans[0] if core.semantic_plans else None
                speech_preview = {
                    'fallback': bool(first_plan.get('fallback')) if first_plan else False,
                    'fallback_mode': first_plan.get('fallback_mode') if first_plan else None,
                    'sku_id': first_plan['sku_id'] if first_plan else '',
                    'product_id': first_plan['product_id'] if first_plan else '',
                    'topic_id': first_plan['topic_id'] if first_plan else '',
                    'pain_id': first_plan.get('pain_id', '') if first_plan else '',
                    'mechanism_id': first_plan.get('mechanism_id', '') if first_plan else '',
                    'capacity': core.n_total if first_plan else 0,
                    'transcripts': core.semantic_transcripts,
                    'segments': [
                        {
                            'role': 'hook' if item['role'] == 'hook' else 'body',
                            'source_file': item['file'],
                            'start_s': item['in_s'], 'end_s': item['out_s'],
                            'text': item['text'], 'sku_id': item['sku_id'],
                            'product_id': item.get('product_id', ''),
                            'hook_opening_prefix': (item.get('hook_whole_source') is False),
                            'source_full_duration_s': item.get('source_full_duration_s'),
                            'topic_id': item['topic_id'],
                            'pain_id': item.get('pain_id', ''),
                            'mechanism_id': item.get('mechanism_id', ''),
                        }
                        for item in first_plan['segments']
                    ] if first_plan else [],
                    'transcript': first_plan['transcript'] if first_plan else '',
                    'hook_duration_s': first_plan['hook_duration_s'] if first_plan else 0,
                    'hook_segment_count': first_plan['hook_segment_count'] if first_plan else 0,
                    'warnings': first_plan['warnings'] if first_plan else [],
                }
            if ok:
                capacity = core.n_total if isinstance(core.n_total, int) else "无限"
                total_capacity += core.n_total if isinstance(core.n_total, int) else 9999
                item = {"name": t['name'], "ok": True, "capacity": capacity,
                        "message": msg if speech_preview else f"可产出: {capacity} 个"}
            else:
                item = {"name": t['name'], "ok": False, "capacity": 0, "message": msg}
            if speech_preview is not None:
                item['speech_logic_preview'] = speech_preview
            report.append(item)
            core.temp_dir.cleanup()
            if progress_callback and task_index + 1 < len(tasks):
                progress_callback(int((task_index + 1) / len(tasks) * 100),
                                  f'{t["name"]}：检查完成')

        cap_text = '充足/无限' if total_capacity > 9000 else total_capacity
        if progress_callback:
            progress_callback(100, '全部素材预检完成')
        return {"ok": any(item["ok"] for item in report), "capacity": cap_text, "report": report}

    def get_benchmark(self, config: VideoConfig,
                      progress_callback: Optional[Callable[[int, str], None]] = None) -> dict:
        raw_config = self._normalize_config(config.model_dump())
        tasks = self._get_tasks_from_config(raw_config)
        if not tasks:
            if progress_callback:
                progress_callback(100, '压测结束：素材不足')
            return {"error": "素材不足以支撑压测"}

        results = {}
        log_cb = lambda x: None
        completed_steps = 0
        total_steps = sum(1 + n for n in range(1, 5))
        warnings = []

        def report(message: str, *, finished: bool = False):
            if progress_callback:
                percent = 100 if finished else min(99, int(completed_steps / total_steps * 100))
                progress_callback(percent, message)

        report('准备压测素材')
        # All rounds share probe metadata, but never production usage history.
        # Reset the temporary usage history before each round so one round does
        # not exhaust Hook slices for the next measurement.
        with tempfile.TemporaryDirectory(prefix='videomatrix-benchmark-') as benchmark_root:
            benchmark_cache = SharedMediaCache(os.path.join(benchmark_root, 'cache'))
            for n in range(1, 5):
                test_cfg = raw_config.copy()
                benchmark_dir = os.path.join(benchmark_root, f'{n}-workers')
                test_cfg.update({
                    'hook_dir': tasks[0]['hook_dir'],
                    'body_dirs': tasks[0]['body_dirs'],
                    'body_groups': raw_config.get('body_groups') or [],
                    'out_dir': benchmark_dir,
                    'target_count': n,
                    'task_name': 'Test'
                })
                os.makedirs(benchmark_dir, exist_ok=True)
                with benchmark_cache.lock:
                    benchmark_cache.usage_history.clear()
                core = None
                try:
                    report(f'{n} 路并发：检查素材')
                    core = VideoMatrixCore(test_cfg, log_cb, benchmark_cache)
                    ok, message = core.pre_flight_check()
                    completed_steps += 1
                    report(f'{n} 路并发：素材检查完成')
                    if not ok:
                        if n == 1:
                            report('压测结束：素材不足', finished=True)
                            return {"error": f"素材不足以支撑压测：{message}"}
                        warnings.append(f'{n} 路及更高并发未测试：{message}')
                        completed_steps += n
                        report(f'{n} 路并发：素材不足，已跳过')
                        break
                    if (int(core.config.get('target_count', n)) < n
                            or isinstance(core.n_total, int) and core.n_total < n):
                        warnings.append(f'{n} 路及更高并发未测试：当前素材不足以生成 {n} 条不同成片')
                        completed_steps += n
                        report(f'{n} 路并发：可用成片不足，已跳过')
                        break

                    benchmark_status = TaskStatus(
                        task_id=f"benchmark-{n}", task_name="Benchmark", status="running",
                        created_at=datetime.now(), total=n,
                    )
                    start_t = time.monotonic()
                    completed_round = 0
                    successful = 0
                    timed_out = False
                    report(f'{n} 路并发：正在渲染 0/{n} 条')
                    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as executor:
                        futures = [executor.submit(self._render_job, core, i, benchmark_status)
                                   for i in range(1, n + 1)]
                        try:
                            for future in concurrent.futures.as_completed(
                                    futures, timeout=self.BENCHMARK_ROUND_TIMEOUT_SECONDS):
                                try:
                                    successful += bool(future.result())
                                except Exception as exc:
                                    warnings.append(f'{n} 路并发有成片失败：{exc}')
                                completed_round += 1
                                completed_steps += 1
                                report(f'{n} 路并发：已完成 {completed_round}/{n} 条')
                        except concurrent.futures.TimeoutError:
                            timed_out = True
                            benchmark_status.status = 'stopped'
                            core.stop()
                            for future in futures:
                                future.cancel()
                            completed_steps += n - completed_round
                            warnings.append(f'{n} 路并发超过 {self.BENCHMARK_ROUND_TIMEOUT_SECONDS} 秒，已停止该轮压测')
                            report(f'{n} 路并发：超时，已停止该轮')
                    if timed_out:
                        break
                    elapsed = time.monotonic() - start_t
                    if successful != n:
                        warnings.append(f'{n} 路并发仅成功 {successful}/{n} 条，未用于推荐')
                        continue
                    results[n] = {
                        "concurrent": n,
                        "total_time": round(elapsed, 2),
                        "avg_per_video": round(elapsed / n, 2),
                    }
                finally:
                    if core is not None:
                        core.temp_dir.cleanup()

        if not results:
            report('压测结束：没有成功的测试成片', finished=True)
            return {"error": "压测期间没有成功完成可比较的成品", "warnings": warnings}
        best_n = min(results, key=lambda k: results[k]["avg_per_video"])
        report('智能压测完成', finished=True)
        return {
            "results": results,
            "best_concurrent": best_n,
            "best_result": results[best_n],
            "warnings": warnings,
        }


# 全局单例
task_service = TaskService()
