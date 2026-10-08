import os
import sys
import math
import random
import json
import tempfile
import threading
import concurrent.futures
import time
import uuid
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Callable

from .ffmpeg import (
    FFMPEG, MEDIA_END_SAFETY_MARGIN, probe_media, extract_media_info, extract_audio_duration,
    render_video
)
from .timeline import apply_timeline_totals, body_segment_specs

MEDIA_CACHE_VERSION = 2
DURATION_EPSILON = 0.001
ASS_PLAY_RES_Y = 288
SOURCE_HAN_FONT_NAME = 'Source Han Sans SC'
SOURCE_HAN_FONT_FILE = 'SourceHanSansSC-Regular.otf'

APP_STATE_DIR = os.path.join(
    os.environ.get('APPDATA') or os.path.expanduser('~'),
    'VideoMatrix'
)
os.makedirs(APP_STATE_DIR, exist_ok=True)


def state_path(filename: str) -> str:
    return os.path.join(APP_STATE_DIR, filename)


def slice_count(usable_duration: float, clip_duration: float, step: float) -> int:
    """Count safe slices while allowing one exact-length clip from the start."""
    if clip_duration <= 0 or step <= 0:
        return 0
    if usable_duration >= clip_duration:
        return int(math.floor((usable_duration - clip_duration) / step)) + 1
    actual_duration = usable_duration + MEDIA_END_SAFETY_MARGIN
    return 1 if actual_duration + DURATION_EPSILON >= clip_duration else 0


def duration_bounds(config: dict, name: str) -> Tuple[float, float]:
    """Resolve a new duration range, falling back to a legacy fixed duration."""
    fixed = float(config.get(name, 3.0))
    minimum = float(config.get(f'{name}_min', fixed))
    maximum = float(config.get(f'{name}_max', fixed))
    if minimum < 0.5 or maximum < minimum:
        raise ValueError(f'{name} 时长范围无效：最短秒数须不小于 0.5，且不大于最长秒数。')
    return minimum, maximum


def bundled_font_dir_safe() -> Optional[str]:
    if getattr(sys, 'frozen', False):
        font_dir = os.path.join(getattr(sys, '_MEIPASS', ''), 'fonts')
    else:
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        font_dir = os.path.join(backend_dir, 'assets', 'fonts', 'files')
    if not os.path.isfile(os.path.join(font_dir, SOURCE_HAN_FONT_FILE)):
        return None
    return font_dir.replace('\\', '/').replace(':', '\\:').replace("'", "'\\''")


def build_subtitle_filter(
    srt_path_safe: str,
    y_percent: float = 92.0,
    font_size_percent: float = 5.6,
    fonts_dir_safe: Optional[str] = None,
) -> str:
    """Build a libass subtitle filter with a resolution-independent vertical anchor."""
    y = max(8.0, min(92.0, float(y_percent)))
    font_size = round(ASS_PLAY_RES_Y * max(3.0, min(9.0, float(font_size_percent))) / 100.0)
    if abs(y - 50.0) < 0.5:
        alignment, margin_v = 5, 0
    elif y < 50.0:
        alignment = 8
        margin_v = round(ASS_PLAY_RES_Y * y / 100.0)
    else:
        alignment = 2
        margin_v = round(ASS_PLAY_RES_Y * (100.0 - y) / 100.0)
    fonts_dir_option = f":fontsdir='{fonts_dir_safe}'" if fonts_dir_safe else ""
    return (
        f"subtitles='{srt_path_safe}'{fonts_dir_option}:"
        f"force_style='FontName={SOURCE_HAN_FONT_NAME},FontSize={font_size},"
        f"Alignment={alignment},MarginV={margin_v}'"
    )


class SharedMediaCache:
    """全局线程安全缓存管理"""
    def __init__(self, state_dir: Optional[str] = None):
        self.media_cache: Dict[str, dict] = {}
        self.usage_history: set = set()
        root = state_dir or APP_STATE_DIR
        os.makedirs(root, exist_ok=True)
        self.media_cache_file = os.path.join(root, 'media_cache.json')
        self.usage_history_file = os.path.join(root, 'usage_history.json')
        self.lock = threading.Lock()
        self.load_state()

    def load_state(self):
        if os.path.exists(self.media_cache_file):
            try:
                with open(self.media_cache_file, 'r', encoding='utf-8') as f:
                    self.media_cache = json.load(f)
            except Exception:
                pass
        if os.path.exists(self.usage_history_file):
            try:
                with open(self.usage_history_file, 'r', encoding='utf-8') as f:
                    self.usage_history = set(json.load(f))
            except Exception:
                pass

    def save_state(self):
        with self.lock:
            try:
                with open(self.media_cache_file, 'w', encoding='utf-8') as f:
                    json.dump(self.media_cache, f, ensure_ascii=False)
                with open(self.usage_history_file, 'w', encoding='utf-8') as f:
                    json.dump(list(self.usage_history), f, ensure_ascii=False)
            except Exception:
                pass

    def clear_history(self):
        with self.lock:
            self.usage_history.clear()
            if os.path.exists(self.usage_history_file):
                os.remove(self.usage_history_file)


class VideoMatrixCore:
    """单库视频处理实例"""
    def __init__(self, config: dict, log_callback: Callable, shared_cache: SharedMediaCache):
        self.config = config
        self.rng = random.Random(config['_selection_seed']) if '_selection_seed' in config else random
        self.log = log_callback
        self.shared = shared_cache
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_dir_path = self.temp_dir.name

        self.task_name = config.get('task_name', 'SingleTask')
        self.hook_pool: List[dict] = []
        self.hook_cycle: List[dict] = []
        self.output_configs: Dict[int, dict] = {}
        self.body_pool: List[dict] = []
        self.body_group_pools: List[List[dict]] = []
        self.bgm_pool: List[dict] = []
        self.voice_pool: List[dict] = []
        self.semantic_plans: List[dict] = []
        self.semantic_transcripts: List[dict] = []
        self.n_total = 0
        self.last_output_path: Optional[str] = None
        self.last_elapsed: Optional[float] = None

        self.is_running = True
        self.current_process = None
        self.processes = set()
        self.core_lock = threading.Lock()

    def _scan_files(self, dir_path: str, exts: tuple) -> List[str]:
        if not dir_path or not os.path.exists(dir_path):
            return []
        if os.path.isfile(dir_path):
            return [dir_path] if dir_path.lower().endswith(exts) else []
        res = []
        for root, _, files in os.walk(dir_path):
            for f in files:
                if f.lower().endswith(exts):
                    res.append(os.path.join(root, f))
        return res

    @staticmethod
    def _ranged_clip(file_path: str, start: float, minimum: float, maximum: float,
                     usable_duration: float, has_audio: bool, clip_id: Optional[str] = None) -> dict:
        # probe_media_cached subtracts a tiny tail margin. Restore it only for
        # the same exact-length edge case supported by slice_count().
        remaining = usable_duration + MEDIA_END_SAFETY_MARGIN - start
        result = {
            'file': file_path, 'start': start, 'duration': minimum,
            'max_duration': min(maximum, max(minimum, remaining)),
            'has_audio': has_audio,
        }
        if clip_id is not None:
            result['id'] = clip_id
        return result

    def _sample_ranged_clip(self, clip: dict) -> dict:
        result = dict(clip)
        minimum = float(result['duration'])
        maximum = float(result.get('max_duration', minimum))
        result['duration'] = (
            min(maximum, max(minimum, round(self.rng.uniform(minimum, maximum), 6)))
            if maximum > minimum else minimum
        )
        return result

    @staticmethod
    def _max_duration_clip(clip: dict) -> dict:
        result = dict(clip)
        result['duration'] = float(clip.get('max_duration', clip['duration']))
        return result

    def probe_media_cached(self, file_path: str) -> Tuple[str, float, bool, float]:
        mtime = None
        try:
            mtime = os.path.getmtime(file_path)
            with self.shared.lock:
                cached = self.shared.media_cache.get(file_path)
                if (
                    cached
                    and cached.get('version') == MEDIA_CACHE_VERSION
                    and cached.get('mtime') == mtime
                    and 'audio_dur' in cached
                ):
                    return file_path, cached['dur'], cached['has_audio'], cached['audio_dur']
        except Exception:
            pass

        info = probe_media(file_path)
        if info:
            dur, has_audio, _, _, _ = extract_media_info(info, file_path)
            audio_dur = extract_audio_duration(info)
            with self.shared.lock:
                self.shared.media_cache[file_path] = {
                    'version': MEDIA_CACHE_VERSION,
                    'mtime': mtime, 'dur': dur, 'has_audio': has_audio,
                    'audio_dur': audio_dur,
                }
            return file_path, dur, has_audio, audio_dur

        self.log(f"[{self.task_name}] WARNING: 素材损坏或无法读取 -> {os.path.basename(file_path)}")
        return file_path, 0.0, False, 0.0

    def parse_time_to_ms(self, t_str: str) -> int:
        h, m, s, ms = map(int, re.split('[:,]', t_str.replace('.', ',').strip()))
        return (h * 3600 + m * 60 + s) * 1000 + ms

    def format_ms_to_time(self, ms: int) -> str:
        h, ms = int(ms // 3600000), ms % 3600000
        m, ms = int(ms // 60000), ms % 60000
        s, ms = int(ms // 1000), int(ms % 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def process_srt(self, srt_dir: str, duration_sec: float, offset_sec: float = 0.0) -> Optional[str]:
        srt_files = self._scan_files(srt_dir, ('.srt',))
        if not srt_files:
            return None
        srt_path = random.choice(srt_files)
        try:
            with open(srt_path, 'r', encoding='utf-8-sig') as f:
                content = f.read()
        except Exception:
            try:
                with open(srt_path, 'r', encoding='gbk') as f:
                    content = f.read()
            except Exception:
                return None

        slice_start_ms, slice_end_ms = 0, int(duration_sec * 1000)
        offset_ms = int(offset_sec * 1000)
        new_subs, index = [], 1
        blocks = re.compile(
            r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\Z)',
            re.DOTALL
        ).findall(content + "\n\n")

        for _, t_start_str, t_end_str, text in blocks:
            t_start = self.parse_time_to_ms(t_start_str)
            t_end = self.parse_time_to_ms(t_end_str)
            if t_end > slice_start_ms and t_start < slice_end_ms:
                new_start = max(0, t_start - slice_start_ms) + offset_ms
                new_end = min(duration_sec * 1000, t_end - slice_start_ms) + offset_ms
                new_subs.append(
                    f"{index}\n{self.format_ms_to_time(new_start)} --> {self.format_ms_to_time(new_end)}\n{text.strip()}"
                )
                index += 1

        if not new_subs:
            return None
        temp_srt_path = os.path.join(self.temp_dir_path, f"temp_{uuid.uuid4().hex[:8]}.srt")
        with open(temp_srt_path, 'w', encoding='utf-8') as f:
            f.write("\n\n".join(new_subs) + "\n\n")
        return temp_srt_path.replace('\\', '/').replace(':', '\\:').replace("'", "'\\''")

    def _preflight_speech_logic(self, cfg: dict, hook_files: List[str], body_files: List[str],
                                bgm_files: List[str], probe_results: dict) -> Tuple[bool, str]:
        """Plan complete spoken units before the existing FFmpeg renderer runs.

        This branch deliberately does not use the fixed-second/random Hook and
        Body pools.  It never relabels an unknown product merely because a name
        was typed into the UI, and it never falls back to a random edit.
        """
        from collections import Counter
        from .semantic_selection import (
            ASR_COLOR_SUSPECT, TIME_SENSITIVE, SemanticSelectionError,
            complete_hook_opening_before_promo, make_full_hook_unit,
            infer_sku_id, make_units, normalize_sku_id, plan_clip_level_fallback,
            plan_semantic_mix, unit_rejection_reason,
        )
        from .speech_transcript import SpeechTranscriptService

        if cfg.get('body_mode', 'normal') != 'normal':
            return False, '口播逻辑模式目前只支持普通 Body，不支持随机分组。'
        if cfg.get('duration_mode') != 'clips':
            return False, '口播逻辑模式按完整话段定长，不支持按 BGM 时长裁断口播。'
        if cfg.get('voice_dir') or cfg.get('enable_srt'):
            return False, '口播逻辑模式暂不支持外部随机配音或随机字幕；请先清空配音并关闭字幕。'
        if cfg.get('vol_orig', 0) <= 0 or cfg.get('vol_hook_orig', cfg.get('vol_orig')) == 0:
            return False, '口播逻辑模式需要保留 Hook 和 Body 原声，请提高原声音量。'
        if int(cfg.get('total_clips', 0)) < 2 or int(cfg.get('total_clips', 0)) > 6:
            return False, '口播逻辑模式每条成片请选择 2–6 段完整话段。'
        if not hook_files or not body_files:
            return False, '口播逻辑模式需要 Hook 与 Body 原始视频。'

        requested_product = str(cfg.get('semantic_sku') or '').strip()
        target_product = normalize_sku_id(requested_product)
        # semantic_topic is accepted for older saved presets, but topic/pain/
        # mechanism labels no longer act as consistency gates.
        invalid_product_hint = bool(requested_product and not target_product)

        hook_set, body_set = set(hook_files), set(body_files)
        if hook_set & body_set:
            return False, '口播逻辑模式的 Hook 与 Body 需使用不同视频文件，避免同一段原声重复。'
        transcript_service = SpeechTranscriptService()
        units: List[dict] = []
        units_by_file: dict[str, List[dict]] = {}
        skipped = Counter()
        self.semantic_transcripts = []
        for file_path in dict.fromkeys(hook_files + body_files):
            if not self.is_running:
                return False, '已停止'
            diagnostic = {
                'source_file': file_path,
                'source_type': 'hook' if file_path in hook_set else 'body',
                'text': '', 'cues': [], 'status': 'blocked', 'reasons': [],
                'sku_id': infer_sku_id(file_path) or '',
                'product_id': infer_sku_id(file_path) or '',
                'topic_id': '', 'source_unit_count': 0,
                'usable_unit_count': 0, 'selected_in_s': None,
                'selected_out_s': None, 'selected_text': '',
            }
            self.semantic_transcripts.append(diagnostic)
            safe_duration, has_audio, _ = probe_results[file_path]
            if not has_audio or safe_duration <= 0:
                skipped['无有效音轨'] += 1
                diagnostic['reasons'].append('视频没有有效原声音轨')
                continue
            try:
                cues = transcript_service.get_cues(file_path, allow_asr=True)
                if not cues:
                    skipped['无口播转录'] += 1
                    diagnostic['reasons'].append('没有可用口播转录；可提供同名带时间码 SRT/TXT')
                    continue
                diagnostic['text'] = ''.join(str(cue['text']) for cue in cues)
                diagnostic['cues'] = [
                    {'start_s': float(cue['start']), 'end_s': float(cue['end']),
                     'text': str(cue['text'])} for cue in cues
                ]
                if ASR_COLOR_SUSPECT.search(diagnostic['text']):
                    diagnostic['reasons'].append('转录疑似把“色修”听成“四球/社羞”等词；请复听确认产品')
                source_units = make_units(
                    cues, source_id=file_path, path=file_path,
                    is_hook_candidate=file_path in hook_set,
                    is_body_candidate=file_path in body_set,
                    default_visual_offer_status='unknown',
                    max_unit_duration_s=max(15.0, safe_duration + 1.0) if file_path in hook_set else 15.0,
                )
                diagnostic['source_unit_count'] = len(source_units)
                sku_ids = {item['sku_id'] for item in source_units if item.get('sku_id')}
                topics = {item['topic_id'] for item in source_units if item.get('topic_id')}
                diagnostic['sku_id'] = next(iter(sku_ids)) if len(sku_ids) == 1 else ''
                diagnostic['product_id'] = diagnostic['sku_id']
                diagnostic['topic_id'] = next(iter(topics)) if len(topics) == 1 else ''
                if not diagnostic['product_id']:
                    diagnostic['reasons'].append('产品无法从该素材明确确认，需核对画面与口播')
                if file_path in hook_set:
                    # The shared probe subtracts a 0.2 s slicing margin for
                    # random cuts.  A whole Hook must retain the original end.
                    full_duration = safe_duration + MEDIA_END_SAFETY_MARGIN
                    whole_hook = make_full_hook_unit(
                        source_units,
                        full_duration_s=full_duration,
                    )
                    selected_hook = whole_hook
                    if not whole_hook['sentence_complete']:
                        opening = complete_hook_opening_before_promo(
                            cues, full_duration_s=full_duration)
                        if opening is not None:
                            opening_cues, opening_end = opening
                            opening_units = make_units(
                                opening_cues, source_id=file_path, path=file_path,
                                is_hook_candidate=True,
                                default_visual_offer_status='unknown',
                                max_unit_duration_s=max(15.0, opening_end + 1.0),
                            )
                            opening_hook = make_full_hook_unit(
                                opening_units, full_duration_s=opening_end)
                            if opening_hook['sentence_complete'] and opening_hook['offer_status'] == 'none':
                                selected_hook = opening_hook
                                selected_hook['hook_whole_source'] = False
                                selected_hook['source_full_duration_s'] = round(full_duration, 3)
                                diagnostic['reasons'].append(
                                    f'仅选完整开场对白 0–{opening_end:.2f}s；后续价促/促单原声未纳入成片，请复听切点')
                    if selected_hook['sentence_complete']:
                        units.append(selected_hook)
                        units_by_file[file_path] = [selected_hook]
                        diagnostic['selected_in_s'] = selected_hook['in_s']
                        diagnostic['selected_out_s'] = selected_hook['out_s']
                        diagnostic['selected_text'] = selected_hook['text']
                        diagnostic['status'] = 'review'
                    else:
                        diagnostic['reasons'].extend(whole_hook.get('completeness_reasons', ()))
                        if (TIME_SENSITIVE.search(diagnostic['text'])
                                and whole_hook.get('offer_status') != 'current'):
                            diagnostic['reasons'].append('活动、价格或赠品话术未核对当期有效')
                        skipped['Hook 不完整、含提前促单或重拍'] += 1
                    continue
            except Exception as exc:
                # One malformed subtitle or offline ASR failure should become
                # a per-source review diagnostic, not abort the whole render.
                self.log(f'[{self.task_name}] 口播素材跳过：{os.path.basename(file_path)} — {exc}')
                skipped['转录或时间轴不可用'] += 1
                diagnostic['reasons'].append(f'转录或时间轴不可用：{exc}')
                continue
            eligible = []
            for item in source_units:
                if item['out_s'] <= safe_duration + MEDIA_END_SAFETY_MARGIN - 0.02:
                    units.append(item)
                    eligible.append(item)
            units_by_file[file_path] = eligible
            out_of_range = len(source_units) - len(eligible)
            if out_of_range:
                diagnostic['reasons'].append(f'{out_of_range} 段转录超出视频时长')
            if not eligible:
                skipped['句段超出视频'] += 1
            else:
                diagnostic['status'] = 'review'
        strict_failure = ''
        if not units:
            strict_failure = f'没有可用的完整口播句段；逐条诊断：{dict(skipped)}'
        if invalid_product_hint:
            strict_failure = (strict_failure + '；' if strict_failure else '') + (
                f'无法识别指定产品「{requested_product}」')
        if not target_product:
            counts = Counter(item['product_id'] for item in units if item.get('product_id'))
            if not counts:
                strict_failure = (strict_failure + '；' if strict_failure else '') + '无法从素材明确判断产品'
            else:
                target_product = counts.most_common(1)[0][0]

        for diagnostic in self.semantic_transcripts:
            file_path = diagnostic['source_file']
            file_units = units_by_file.get(file_path, [])
            if not file_units:
                continue
            rejected = Counter(
                reason for item in file_units
                if (reason := unit_rejection_reason(
                    item, sku_id=target_product, topic_id='',
                    packaging_version=''))
            ) if target_product else Counter()
            accepted = len(file_units) - sum(rejected.values())
            diagnostic['usable_unit_count'] = accepted
            diagnostic['reasons'].extend(
                f'{reason}（{count} 段）' for reason, count in rejected.items()
            )
            if not accepted:
                diagnostic['status'] = 'blocked'
            else:
                if any(item.get('boundary_basis') == 'inferred' for item in file_units):
                    diagnostic['reasons'].append('无标点转录的句末由规则推断，请复听切点')
                if any(item.get('needs_visual_review') for item in file_units):
                    diagnostic['reasons'].append('画面包装和价促尚未核对，导出投放前请复核')
                diagnostic['status'] = 'review' if diagnostic['reasons'] else 'usable'

        target_clips = int(cfg['total_clips'])
        desired_duration = float(cfg.get('total_duration') or 0)
        first_plan = None
        if not strict_failure:
            relevant = Counter(
                item['role'] for item in units
                if item.get('product_id') == target_product
                and item.get('sentence_complete')
            )
            self.log(f'[{self.task_name}] 口播候选产品 {target_product}：{dict(relevant)}；'
                     f'总话段 {len(units)}，请复核机器转录与缺失角色。')
            try:
                first_plan = plan_semantic_mix(
                    units, sku_id=target_product, topic_id='',
                    target_clips=target_clips, max_segments=target_clips,
                    target_duration_s=min(45.0, max(9.0, desired_duration)),
                    max_duration_s=60.0,
                    hook_files=hook_files, body_files=body_files,
                )
            except SemanticSelectionError as exc:
                strict_failure = (f'{exc}；可用句段 {len(units)}，'
                                  f'筛除原因 {exc.details.get("rejections", {})}')

        requested_count = int(cfg['target_count'])
        if first_plan is None:
            diagnostic_by_file = {item['source_file']: item for item in self.semantic_transcripts}
            def fallback_source(path: str) -> dict:
                diagnostic = diagnostic_by_file[path]
                safe_duration, has_audio, _ = probe_results[path]
                # BGM/voice may be audio-only, so validate the picture stream
                # here, only for Hook/Body fallback candidates.  Synthetic
                # direct unit tests provide a probe tuple without a real file.
                video_ok = safe_duration > 0 and (
                    not os.path.isfile(path) or any(
                        stream.get('codec_type') == 'video'
                        for stream in (probe_media(path) or {}).get('streams', [])))
                return {
                    'file': path, 'duration_s': (
                        safe_duration + MEDIA_END_SAFETY_MARGIN if video_ok else 0.0),
                    'has_audio': has_audio, 'text': diagnostic['text'],
                    'selected_text': diagnostic['selected_text'],
                    'selected_in_s': diagnostic['selected_in_s'],
                    'selected_out_s': diagnostic['selected_out_s'],
                    'status': diagnostic['status'], 'reasons': diagnostic['reasons'],
                    'product_id': diagnostic['product_id'],
                }
            fallback_plans = plan_clip_level_fallback(
                [fallback_source(path) for path in hook_files],
                [fallback_source(path) for path in body_files],
                target_product=target_product or '', target_clips=target_clips,
                strict_failure=strict_failure, max_variants=requested_count,
            )
            if not fallback_plans:
                return False, f'无可播放的 Hook/Body 视频，无法制作保底草稿；{strict_failure}。'
            capacity = len(fallback_plans)
            self.semantic_plans = fallback_plans[:min(requested_count, capacity)]
            cfg['target_count'] = len(self.semantic_plans)
            self.n_total = capacity
            first_plan = self.semantic_plans[0]
            if cfg['target_count'] < requested_count:
                shortage = (f'目标 {requested_count} 条仅找到 {cfg["target_count"]} 条'
                            '不同的可播放 Hook/Body 组合；未重复或伪造素材凑数')
                for plan in self.semantic_plans:
                    plan['warnings'].append(shortage)
            self.log(f'[{self.task_name}] 口播预检：语义编排未通过，已降级为完整原片拼接；'
                     f'有风险但可渲染，需人工复核；可产出 {capacity} 组。原因：{strict_failure}。')
            for item in first_plan['segments']:
                diag = diagnostic_by_file[item['file']]
                self.log(f'[{self.task_name}] 保底选材 {item["role"]}：'
                         f'{os.path.basename(item["file"])}，分级 {diag["status"]}，'
                         f'产品 {item["product_id"] or "未知"}，'
                         f'区间 {item["in_s"]:.2f}–{item["out_s"]:.2f}s；'
                         f'诊断：{("；".join(diag["reasons"]) or "台词顺接需人工复核")}。')
        else:
            capacity = int(first_plan['available_variants'])
            cfg['target_count'] = min(requested_count, capacity)
            self.semantic_plans = [first_plan]
            for index in range(1, cfg['target_count']):
                self.semantic_plans.append(plan_semantic_mix(
                    units, sku_id=target_product, topic_id='',
                    target_clips=target_clips, max_segments=target_clips,
                    target_duration_s=min(45.0, max(9.0, desired_duration)),
                    max_duration_s=60.0, variant_index=index,
                    hook_files=hook_files, body_files=body_files,
                ))
            self.n_total = capacity
            self.log(f'[{self.task_name}] 口播预检：{len(units)} 个带时间码话段，产品 {target_product}，可编排 {capacity} 个不同组合。')
        if cfg['target_count'] < requested_count:
            self.log(f'[{self.task_name}] 仅找到 {capacity} 组不同片段，目标产量从 {requested_count} 调至 {cfg["target_count"]}；'
                     '优先保持同产品且不重复镜头，未用跨产品组合凑数。')
        for warning in dict.fromkeys(
            warning for plan in self.semantic_plans for warning in plan.get('warnings', [])
        ):
            self.log(f'[{self.task_name}] 预览待复核：{warning}。')
        if first_plan.get('needs_visual_review'):
            self.log(f'[{self.task_name}] 注意：转录只保证声音段落；画面包装、旧价促和功效表述仍需人工复核。')
        if skipped:
            self.log(f'[{self.task_name}] 跳过素材：{dict(skipped)}。')

        if cfg.get('bgm_dir'):
            needed = max(plan['duration_s'] for plan in self.semantic_plans)
            if not cfg.get('apply_bgm_to_hook', True):
                needed -= min(plan['hook_duration_s'] for plan in self.semantic_plans)
            for file_path in bgm_files:
                _, has_audio, audio_duration = probe_results[file_path]
                if has_audio and audio_duration >= needed:
                    self.bgm_pool.append({'file': file_path, 'start': 0.0, 'duration': needed})
            if not self.bgm_pool:
                return False, f'所选 BGM 没有足够长的音轨覆盖口播（至少 {needed:.1f} 秒）；请清空 BGM 或换长音频。'
        if first_plan.get('fallback'):
            return True, f'口播逻辑已降级：有风险但可渲染，需人工复核；可编排 {capacity} 组。'
        return True, f'口播逻辑预检通过：产品 {target_product}，可编排 {capacity} 组。'

    def pre_flight_check(self) -> Tuple[bool, str]:
        self.hook_pool.clear()
        self.hook_cycle.clear()
        self.body_pool.clear()
        self.body_group_pools.clear()
        self.bgm_pool.clear()
        self.voice_pool.clear()
        self.semantic_plans.clear()
        self.semantic_transcripts.clear()

        cfg = self.config
        cfg['bgm_dir'] = str(cfg.get('bgm_dir') or '').strip()
        audio_mode = cfg.get('duration_mode') == 'bgm'
        if audio_mode and not cfg['bgm_dir']:
            return False, '按 BGM 时长生成需要选择 BGM，请选择音频或切回按片段数量。'
        if cfg['bgm_dir'] and not os.path.exists(cfg['bgm_dir']):
            return False, 'BGM 路径不存在，请重新选择；不需要配乐时请清空路径。'
        try:
            t_hook, t_hook_max = duration_bounds(cfg, 't_hook')
            t_body_min, t_body_max = duration_bounds(cfg, 't_body')
        except (TypeError, ValueError) as exc:
            return False, str(exc)
        apply_timeline_totals(cfg)
        body_specs = body_segment_specs(cfg)
        body_duration = cfg['body_duration']
        t_total = cfg['total_duration']
        bgm_duration = t_total if cfg.get('apply_bgm_to_hook', True) else body_duration

        if cfg.get('enable_srt'):
            srt_dir = str(cfg.get('srt_dir') or '').strip()
            if not srt_dir or not os.path.isdir(srt_dir):
                return False, "字幕已开启，但字幕目录不存在或未设置。"
            if not self._scan_files(srt_dir, ('.srt',)):
                return False, "字幕已开启，但字幕目录中没有找到 SRT 文件。"

        hook_files = self._scan_files(cfg['hook_dir'], ('.mp4', '.mov'))
        grouped_body = cfg.get('body_mode', 'normal') == 'grouped'
        if grouped_body and not body_specs:
            return False, f"库 [{self.task_name}] Body 分组模式至少需要启用一个分组。"
        body_files = []
        group_files: List[List[str]] = []
        if grouped_body:
            for spec in body_specs:
                files = self._scan_files(spec['folder'], ('.mp4', '.mov'))
                group_files.append(files)
                body_files.extend(files)
        else:
            body_files = [f for bd in cfg['body_dirs'] for f in self._scan_files(bd, ('.mp4', '.mov'))]
        bgm_files = self._scan_files(
            cfg['bgm_dir'],
            ('.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.mp4', '.mov', '.mkv', '.avi', '.webm')
        )
        voice_files = self._scan_files(cfg.get('voice_dir', ''), ('.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus'))

        probe_results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            all_media = list(set(hook_files + body_files + bgm_files + voice_files))
            futures = {executor.submit(self.probe_media_cached, f): f for f in all_media}
            for future in concurrent.futures.as_completed(futures):
                if not self.is_running:
                    return False, "已停止"
                f_path, dur, has_audio, audio_dur = future.result()
                probe_results[f_path] = (dur, has_audio, audio_dur)

        self.shared.save_state()

        if cfg.get('selection_mode') == 'speech_logic':
            return self._preflight_speech_logic(
                cfg, hook_files, body_files, bgm_files, probe_results
            )

        hook_candidate_count = 0
        for f in hook_files:
            dur, has_audio, _ = probe_results[f]
            if cfg.get('hook_full_duration'):
                full_duration = dur + MEDIA_END_SAFETY_MARGIN if dur > 0 else extract_media_info(probe_media(f) or {}, f, safety_margin=0)[0]
                if full_duration > 0:
                    self.hook_pool.append({'file': f, 'start': 0.0, 'duration': full_duration, 'has_audio': has_audio})
                    hook_candidate_count += 1
                continue
            step = max(t_hook * (1 - cfg['hook_r']), 0.1)
            n = slice_count(dur, t_hook, step)
            if n <= 0:
                continue
            hook_candidate_count += n
            if cfg['hook_r'] >= 0.99:
                self.hook_pool.append(self._ranged_clip(f, 0.0, t_hook, t_hook_max, dur, has_audio))
                continue
            for i in range(n):
                hook_id = f"{f}_{i*step:.2f}"
                if hook_id not in self.shared.usage_history:
                    self.hook_pool.append(self._ranged_clip(
                        f, i * step, t_hook, t_hook_max, dur, has_audio, hook_id
                    ))

        if grouped_body:
            for spec, files in zip(body_specs, group_files):
                if not spec['folder'] or not os.path.isdir(spec['folder']):
                    return False, f"库 [{self.task_name}] Body 分组 {spec['group_index']} 目录不存在或未设置。"
                pool: List[dict] = []
                duration = spec['clip_duration']
                for f in files:
                    dur, has_audio, _ = probe_results[f]
                    if spec.get('full_duration'):
                        full_duration = dur + MEDIA_END_SAFETY_MARGIN if dur > 0 else extract_media_info(probe_media(f) or {}, f, safety_margin=0)[0]
                        if full_duration > 0:
                            pool.append({'file': f, 'start': 0.0, 'duration': full_duration, 'has_audio': has_audio})
                        continue
                    step = max(duration * (1 - cfg['body_r']), 0.1)
                    for i in range(slice_count(dur, duration, step)):
                        pool.append({'file': f, 'start': i * step, 'duration': duration, 'has_audio': has_audio})
                if not audio_mode and len(pool) < spec['clip_count']:
                    return False, (
                        f"库 [{self.task_name}] Body 分组 {spec['group_index']} 素材不足："
                        f"需要 {spec['clip_count']} 段，可用 {len(pool)} 段。"
                    )
                self.body_group_pools.append(pool)
        else:
            t_body = t_body_min
            for f in dict.fromkeys(body_files):
                dur, has_audio, _ = probe_results[f]
                if cfg.get('body_full_duration'):
                    full_duration = dur + MEDIA_END_SAFETY_MARGIN if dur > 0 else extract_media_info(probe_media(f) or {}, f, safety_margin=0)[0]
                    if full_duration > 0:
                        self.body_pool.append({'file': f, 'start': 0.0, 'duration': full_duration, 'has_audio': has_audio})
                    continue
                step = max(t_body * (1 - cfg['body_r']), 0.1)
                n = slice_count(dur, t_body, step)
                for i in range(n):
                    self.body_pool.append(self._ranged_clip(
                        f, i * step, t_body, t_body_max, dur, has_audio
                    ))

        if not self.hook_pool:
            if not hook_files:
                return False, f"库 [{self.task_name}] 未找到 MP4/MOV 首段视频。"
            if hook_candidate_count == 0:
                max_duration = max(
                    (probe_results[f][0] + MEDIA_END_SAFETY_MARGIN for f in hook_files),
                    default=0.0,
                )
                return False, (
                    f"库 [{self.task_name}] 找到 {len(hook_files)} 个视频，但最长视频轨约 "
                    f"{max_duration:.2f} 秒，短于首段设置 {t_hook:g} 秒。"
                )
            return False, (
                f"库 [{self.task_name}] 找到 {len(hook_files)} 个合规视频，"
                "但对应首段切片已全部使用；请清理记录后重试。"
            )

        if cfg.get('hook_full_duration') or cfg['hook_r'] >= 0.99:
            self.n_total = "无限"
        else:
            self.n_total = len(self.hook_pool)
            if self.n_total == 0:
                return False, f"库 [{self.task_name}] 首段剩余 0 个片段，无法生产。"
            if self.n_total < cfg['target_count']:
                self.log(f"[{self.task_name}] 提示: 首段仅剩 {self.n_total} 个片段，已自动下调目标产量。")
                cfg['target_count'] = self.n_total

        if not audio_mode and not grouped_body and len(self.body_pool) < cfg['total_clips'] - 1:
            return False, f"库 [{self.task_name}] 后段素材不足拼凑 1 个视频。"

        if not audio_mode:
            pools = self.body_group_pools if grouped_body else [self.body_pool]
            body_duration = sum(
                sum(sorted((c.get('max_duration', c['duration']) for c in pool), reverse=True)[:spec['clip_count']])
                for spec, pool in zip(body_specs, pools)
            )
            bgm_duration = body_duration + (
                max(clip.get('max_duration', clip['duration']) for clip in self.hook_pool)
                if cfg.get('apply_bgm_to_hook', True) else 0
            )
        for f in bgm_files:
            _, has_audio, audio_dur = probe_results[f]
            if has_audio:
                if audio_mode:
                    # Cached audio durations subtract the safety margin for slicing.
                    # Full-track mode must restore it, including the audio tail.
                    duration = audio_dur + MEDIA_END_SAFETY_MARGIN if audio_dur > 0 else extract_audio_duration(probe_media(f) or {}, safety_margin=0)
                    if duration > 0:
                        self.bgm_pool.append({'file': f, 'start': 0.0, 'duration': duration})
                    continue
                step = max(bgm_duration * (1 - cfg['bgm_r']), 0.1)
                n = slice_count(audio_dur, bgm_duration, step)
                for i in range(n):
                    self.bgm_pool.append({'file': f, 'start': i * step, 'duration': bgm_duration})

        if cfg['bgm_dir'] and not self.bgm_pool:
            return False, "BGM 素材不足，视频文件需包含音轨且时长足够。"

        if audio_mode:
            from .audio_timeline import fit_body_to_audio
            try:
                candidate_pools = self.body_group_pools if grouped_body else [self.body_pool]
                fit_body_to_audio(cfg, [
                    [self._max_duration_clip(clip) for clip in pool]
                    for pool in candidate_pools
                ],
                                  min(c['duration'] for c in self.hook_pool),
                                  max(c['duration'] for c in self.bgm_pool), None)
            except ValueError as exc:
                return False, str(exc)
            self.log(f'[{self.task_name}] 完整 BGM 模式：随机抽取整条音频；Body 按组循环补齐，超长裁尾。')

        for f in voice_files:
            _, has_audio, audio_dur = probe_results[f]
            if has_audio and audio_dur > 0:
                self.voice_pool.append({'file': f, 'duration': audio_dur})

        if not self.bgm_pool:
            self.log(f'[{self.task_name}] 未选择 BGM：不添加背景音乐。')
        if not (self.bgm_pool and cfg.get('vol_bgm', 0) > 0) and not (self.voice_pool and cfg.get('vol_voice', 0) > 0) and not (cfg.get('vol_orig', 0) > 0 or (cfg.get('vol_hook_orig') or 0) > 0):
            self.log(f'[{self.task_name}] 提示：当前声音均关闭，将生成无声视频。')

        protected = [
            name for name, enabled in (
                ('BGM', cfg.get('apply_bgm_to_hook', True)),
                ('配音', cfg.get('apply_voice_to_hook', True)),
                ('字幕', cfg.get('apply_srt_to_hook', True)),
                ('水印', cfg.get('apply_watermark_to_hook', True)),
            ) if not enabled
        ]
        if protected:
            offset_label = '实际 Hook 结束' if cfg.get('hook_full_duration') or t_hook_max > t_hook else f'{t_hook:g} 秒'
            self.log(f"[{self.task_name}] 成品 Hook 保护：{', '.join(protected)} 从 {offset_label}后开始生效。")

        if cfg.get('selection_mode') == 'random' and (
            (not cfg.get('hook_full_duration') and t_hook_max > t_hook) or
            (not cfg.get('body_full_duration') and not grouped_body and t_body_max > t_body_min)
        ):
            self.log(
                f'[{self.task_name}] 随机时长：Hook {t_hook:g}-{t_hook_max:g} 秒，'
                f'Body {t_body_min:g}-{t_body_max:g} 秒；每条成片按选中素材剩余长度取值。'
            )

        self.rng.shuffle(self.hook_pool)
        return True, "预检通过"

    def render_single_video(self, task_idx: int, return_result: bool = False):
        if not self.is_running:
            return (False, None, None) if return_result else False

        now_str_start = datetime.now().strftime("%H:%M:%S")
        self.log(f"  [{now_str_start}] [{self.task_name}] 正在拼装 视频 {task_idx:03d} ...")

        start_time = time.time()
        cfg = dict(self.config)
        body_specs = body_segment_specs(cfg)
        additional_hook_clips: List[dict] = []

        with self.core_lock:
            if cfg.get('selection_mode') == 'speech_logic':
                if task_idx < 1 or task_idx > len(self.semantic_plans):
                    self.log(f'[{self.task_name}] 口播逻辑方案不足，无法生成第 {task_idx} 条。')
                    return (False, None, None) if return_result else False
                semantic_plan = self.semantic_plans[task_idx - 1]
                chosen = semantic_plan['segments']
                hook_clip_count = int(semantic_plan['hook_segment_count'])
                hook_clip = {key: chosen[0][key] for key in ('file', 'start', 'duration', 'has_audio')}
                additional_hook_clips = [
                    {key: item[key] for key in ('file', 'start', 'duration', 'has_audio')}
                    for item in chosen[1:hook_clip_count]
                ]
                body_clips = [
                    {key: item[key] for key in ('file', 'start', 'duration', 'has_audio')}
                    for item in chosen[hook_clip_count:]
                ]
                cfg['t_hook'] = semantic_plan['hook_duration_s']
                cfg['total_clips'] = len(chosen)
                cfg['_hook_clip_count'] = hook_clip_count
                cfg['_semantic_plan'] = [
                    {'file': item['file'], 'start': item['start'],
                     'duration': item['duration'], 'text': item['text'], 'role': item['role'],
                     'pain_id': item.get('pain_id', ''),
                     'mechanism_id': item.get('mechanism_id', '')}
                    for item in chosen
                ]
                bgm_clip = self.rng.choice(self.bgm_pool) if self.bgm_pool else None
                voice_clip = None
                if semantic_plan.get('fallback'):
                    self.log(f'[{self.task_name}] 口播保底方案 {task_idx}：完整原片拼接，'
                             f'产品 {semantic_plan["product_id"] or "未知"}，'
                             f'成片约 {semantic_plan["duration_s"]:.1f}s；'
                             '有风险但可渲染，需人工复核。')
                    for warning in semantic_plan.get('warnings', []):
                        self.log(f'[{self.task_name}] 保底风险：{warning}。')
                else:
                    self.log(f'[{self.task_name}] 口播逻辑方案 {task_idx}：产品 {semantic_plan["product_id"]}，原声 {semantic_plan["duration_s"]:.1f}s。')
                for index, item in enumerate(chosen, 1):
                    self.log(f'    {index}. {item["role"]} {os.path.basename(item["file"])} [{item["in_s"]:.1f}-{item["out_s"]:.1f}s] {item["text"][:72]}')
            elif not self.hook_pool:
                return (False, None, None) if return_result else False
            elif cfg.get('hook_full_duration'):
                if not self.hook_cycle:
                    self.hook_cycle = list(self.hook_pool)
                    self.rng.shuffle(self.hook_cycle)
                hook_clip = self.hook_cycle.pop()
                cfg['t_hook'] = hook_clip['duration']
            elif cfg['hook_r'] >= 0.99:
                hook_clip = self._sample_ranged_clip(self.rng.choice(self.hook_pool))
                cfg['t_hook'] = hook_clip['duration']
            else:
                hook_clip = self._sample_ranged_clip(self.hook_pool.pop())
                cfg['t_hook'] = hook_clip['duration']

            if cfg.get('selection_mode') != 'speech_logic':
                bgm_clip = self.rng.choice(self.bgm_pool) if self.bgm_pool else None
                if cfg.get('duration_mode') == 'bgm':
                    from .audio_timeline import fit_body_to_audio
                    pools = self.body_group_pools if cfg.get('body_mode') == 'grouped' else [self.body_pool]
                    try:
                        body_clips = fit_body_to_audio(
                            cfg, [[self._sample_ranged_clip(clip) for clip in pool] for pool in pools],
                            cfg['t_hook'], bgm_clip['duration'], self.rng,
                        )
                    except ValueError as exc:
                        # A short random draw may not fill a long music track.
                        # Retry at each candidate's safe upper bound; the final
                        # clip may still be trimmed to hit the exact BGM end.
                        try:
                            body_clips = fit_body_to_audio(
                                cfg, [[self._max_duration_clip(clip) for clip in pool] for pool in pools],
                                cfg['t_hook'], bgm_clip['duration'], self.rng,
                            )
                            self.log(f'[{self.task_name}] Body 随机时长不足以铺满 BGM，已改用素材允许的范围上限。')
                        except ValueError:
                            self.log(f'[{self.task_name}] {exc}')
                            return (False, None, None) if return_result else False
                elif cfg.get('body_mode', 'normal') == 'grouped':
                    body_clips = []
                    for spec, pool in zip(body_specs, self.body_group_pools):
                        body_clips.extend(
                            self._sample_ranged_clip(clip)
                            for clip in self.rng.sample(pool, spec['clip_count'])
                        )
                elif len(self.body_pool) >= (cfg['total_clips'] - 1):
                    body_clips = [
                        self._sample_ranged_clip(clip)
                        for clip in self.rng.sample(self.body_pool, cfg['total_clips'] - 1)
                    ]
                else:
                    body_clips = [
                        self._sample_ranged_clip(clip)
                        for clip in self.rng.choices(self.body_pool, k=cfg['total_clips'] - 1)
                    ]
                voice_clip = self.rng.choice(self.voice_pool) if self.voice_pool else None

        apply_timeline_totals(cfg)
        planned_body_duration = cfg['body_duration']
        cfg['body_duration'] = sum(c['duration'] for c in body_clips)
        cfg['_body_clip_durations'] = [c['duration'] for c in body_clips]
        cfg['total_duration'] = cfg['t_hook'] + cfg['body_duration']
        if cfg.get('duration_mode') != 'bgm' and any(spec.get('full_duration') for spec in body_specs):
            self.log(f"[{self.task_name}] Body 使用原素材时长：{len(body_clips)} 段，共 {cfg['body_duration']:.3f}s；成片 {cfg['total_duration']:.3f}s。")
        if cfg.get('duration_mode') == 'bgm':
            old_body_duration = planned_body_duration
            cfg['body_duration'] = sum(c['duration'] for c in body_clips)
            cfg['_body_clip_durations'] = [c['duration'] for c in body_clips]
            cfg['total_duration'] = cfg['t_hook'] + cfg['body_duration']
            cfg['total_clips'] = 1 + len(body_clips)
            action = '裁掉尾部多余素材' if cfg['body_duration'] < old_body_duration else '自动补满素材' if cfg['body_duration'] > old_body_duration else '时长刚好匹配'
            self.log(f"[{self.task_name}] BGM {os.path.basename(bgm_clip['file'])} 完整 {bgm_clip['duration']:.3f}s；{action}，共 {cfg['total_clips']} 段 / {cfg['total_duration']:.3f}s。")
            if cfg.get('apply_bgm_to_hook', True) and cfg['t_hook'] >= bgm_clip['duration']:
                self.log(f"[{self.task_name}] 提示：保留完整 Hook，不追加 Body。" + ('BGM 未覆盖完整 Hook，剩余部分按原声设置输出。' if cfg['t_hook'] > bgm_clip['duration'] else '无剩余时长添加 Body。'))
        t_hook = cfg['t_hook']
        body_duration = cfg['body_duration']
        t_total = cfg['total_duration']

        temp_srt_path_safe = None
        with self.core_lock:
            self.output_configs[task_idx] = cfg
        if cfg.get('enable_srt') and (cfg.get('apply_srt_to_hook', True) or body_duration > 0):
            srt_on_hook = cfg.get('apply_srt_to_hook', True)
            temp_srt_path_safe = self.process_srt(
                cfg['srt_dir'],
                t_total if srt_on_hook else body_duration,
                0.0 if srt_on_hook else t_hook,
            )
            if not temp_srt_path_safe:
                self.log(f"    [{self.task_name}] 字幕处理失败：SRT 无有效时间轴或编码无法读取。")
                return (False, None, None) if return_result else False

        vol_orig = cfg['vol_orig'] / 100.0
        hook_volume_value = cfg.get('vol_hook_orig')
        vol_hook_orig = (cfg['vol_orig'] if hook_volume_value is None else hook_volume_value) / 100.0
        vol_bgm = cfg['vol_bgm'] / 100.0 if bgm_clip else 0.0
        vol_voice = cfg['vol_voice'] / 100.0
        fps_val = str(cfg['fps'])

        has_voice = bool(voice_clip and vol_voice > 0 and (cfg.get('apply_voice_to_hook', True) or body_duration > 0))
        has_watermark = bool(cfg.get('watermark_path') and os.path.exists(cfg['watermark_path']) and (cfg.get('apply_watermark_to_hook', True) or body_duration > 0))
        has_original_audio = vol_hook_orig > 0 or vol_orig > 0

        clips = [hook_clip] + additional_hook_clips + list(body_clips)
        inputs = [c['file'] for c in clips] + ([bgm_clip['file']] if bgm_clip else [])

        voice_idx = -1
        if has_voice:
            inputs.append(voice_clip['file'])
            voice_idx = len(inputs) - 1

        watermark_idx = -1
        if has_watermark:
            inputs.append(cfg['watermark_path'])
            watermark_idx = len(inputs) - 1

        # Accurate input seeking skips long unused prefixes. Keep one second of
        # preroll, then trim residual timestamps for frame/audio boundary accuracy.
        seek_offsets = [max(0.0, float(c['start']) - 1.0) for c in clips]
        bgm_seek = max(0.0, float(bgm_clip['start']) - 1.0) if bgm_clip else 0.0
        cmd = [FFMPEG, '-y']
        for idx, inp in enumerate(inputs):
            if idx == watermark_idx:
                ext = inp.lower().split('.')[-1]
                if ext == 'gif':
                    cmd.extend(['-ignore_loop', '0', '-i', inp])
                elif ext in ['png', 'jpg', 'jpeg']:
                    cmd.extend(['-loop', '1', '-i', inp])
                else:
                    cmd.extend(['-i', inp])
            else:
                offset = seek_offsets[idx] if idx < len(clips) else bgm_seek if idx == len(clips) else 0.0
                if offset > 0:
                    cmd.extend(['-ss', f'{offset:.9f}'])
                cmd.extend(['-threads', '2', '-i', inp])

        n_clips = len(clips)
        res_str = cfg['resolution'].lower().replace('*', 'x')
        w, h = map(int, res_str.split('x'))
        
        filter_complex = ""
        for i, clip in enumerate(clips):
            start, dur, clip_has_audio = clip['start'] - seek_offsets[i], clip['duration'], clip.get('has_audio', False)
            filter_complex += (
                f"[{i}:v]trim=start={start}:duration={dur},setpts=PTS-STARTPTS,"
                f"fps={fps_val},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                f"setsar=1,format=yuv420p[v{i}]; "
            )
            if has_original_audio:
                clip_volume = vol_hook_orig if i < int(cfg.get('_hook_clip_count', 1)) else vol_orig
                if clip_has_audio:
                    filter_complex += (
                        f"[{i}:a]atrim=start={start}:duration={dur},asetpts=PTS-STARTPTS,"
                        f"aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                        f"apad=pad_dur={dur},atrim=duration={dur},volume={clip_volume}[a{i}]; "
                    )
                else:
                    filter_complex += (
                        f"anullsrc=channel_layout=stereo:sample_rate=44100:d={dur},"
                        f"volume={clip_volume}[a{i}]; "
                    )

        if has_original_audio:
            concat_inputs = "".join([f"[v{i}][a{i}]" for i in range(n_clips)])
            filter_complex += f"{concat_inputs}concat=n={n_clips}:v=1:a=1[vout_base][aout_orig]; "
        else:
            concat_inputs = "".join([f"[v{i}]" for i in range(n_clips)])
            filter_complex += f"{concat_inputs}concat=n={n_clips}:v=1:a=0[vout_base]; "

        current_v = "[vout_base]"
        if temp_srt_path_safe:
            subtitle_filter = build_subtitle_filter(
                temp_srt_path_safe,
                cfg.get('subtitle_y_percent', 92.0),
                cfg.get('subtitle_font_size_percent', 5.6),
                bundled_font_dir_safe(),
            )
            filter_complex += f"{current_v}{subtitle_filter}[v_sub]; "
            current_v = "[v_sub]"
        if has_watermark:
            watermark_offset = 0.0 if cfg.get('apply_watermark_to_hook', True) else t_hook
            watermark_input = f"[{watermark_idx}:v]"
            enable_filter = ""
            if watermark_offset > 0:
                filter_complex += (
                    f"[{watermark_idx}:v]setpts=PTS-STARTPTS+{watermark_offset}/TB[wm_timed]; "
                )
                watermark_input = "[wm_timed]"
                enable_filter = f":enable='gte(t,{watermark_offset})'"
            filter_complex += (
                f"{current_v}{watermark_input}overlay=(W-w)/2:(H-h)/2:"
                f"shortest=1:eof_action=pass{enable_filter}[v_wm]; "
            )
            current_v = "[v_wm]"

        bgm_idx = n_clips
        if vol_bgm > 0:
            bgm_offset = 0.0 if cfg.get('apply_bgm_to_hook', True) else t_hook
            bgm_delay = f",adelay={int(round(bgm_offset * 1000))}|{int(round(bgm_offset * 1000))}" if bgm_offset > 0 else ""
            filter_complex += (
                f"[{bgm_idx}:a]atrim=start={bgm_clip['start'] - bgm_seek}:duration={bgm_clip['duration']},"
                f"asetpts=PTS-STARTPTS,aresample=44100,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={vol_bgm}"
                f"{bgm_delay}[aout_bgm_v]; "
            )
        if has_voice:
            voice_on_hook = cfg.get('apply_voice_to_hook', True)
            voice_offset = 0.0 if voice_on_hook else t_hook
            voice_duration = t_total if voice_on_hook else body_duration
            voice_delay_ms = int(round(voice_offset * 1000))
            voice_delay = f",adelay={voice_delay_ms}|{voice_delay_ms}" if voice_delay_ms > 0 else ""
            filter_complex += (
                f"[{voice_idx}:a]atrim=start=0:duration={voice_duration},asetpts=PTS-STARTPTS,"
                f"aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"apad=pad_dur={voice_duration},atrim=duration={voice_duration},volume={vol_voice}"
                f"{voice_delay}[aout_voice_v]; "
            )

        mix_tracks = []
        if has_original_audio:
            mix_tracks.append("[aout_orig]")
        if vol_bgm > 0:
            mix_tracks.append("[aout_bgm_v]")
        if has_voice:
            mix_tracks.append("[aout_voice_v]")

        audio_map = None
        if len(mix_tracks) > 1:
            inputs_str = "".join(mix_tracks)
            bgm_on_hook = cfg.get('apply_bgm_to_hook', True)
            voice_on_hook = cfg.get('apply_voice_to_hook', True)
            hook_mix_count = sum((
                int(vol_hook_orig > 0),
                int(vol_bgm > 0 and bgm_on_hook),
                int(has_voice and voice_on_hook),
            ))
            body_mix_count = sum((
                int(vol_orig > 0),
                int(vol_bgm > 0),
                int(has_voice),
            ))
            hook_mix_gain = 1.0 / max(1, hook_mix_count)
            body_mix_gain = 1.0 / max(1, body_mix_count)
            gain_expression = (
                f"if(lt(t,{t_hook}),{hook_mix_gain:.8f},{body_mix_gain:.8f})"
            )
            filter_complex += (
                f"{inputs_str}amix=inputs={len(mix_tracks)}:duration=longest:"
                f"dropout_transition=0:normalize=0[aout_sum]; "
                f"[aout_sum]volume='{gain_expression}':eval=frame[aout_mix]; "
            )
            audio_source = "[aout_mix]"
        elif len(mix_tracks) == 1:
            audio_source = mix_tracks[0]
        else:
            audio_source = None

        if audio_source:
            filter_complex += (
                f"{audio_source}atrim=start=0:duration={t_total},"
                f"asetpts=PTS-STARTPTS[aout_final]"
            )
            audio_map = "[aout_final]"

        filter_complex = filter_complex.strip('; ')
        if cfg.get('enable_variants') and cfg.get('_fuse_variants', True):
            from .video_variant import VideoVariantProcessor, derive_variant_seed
            seed = derive_variant_seed(int(cfg.get('variant_seed') or 0), self.task_name, task_idx)
            extra, current_v, audio_map, summary = VideoVariantProcessor.inline_filters(cfg, seed, current_v, audio_map)
            if extra:
                filter_complex += '; ' + extra
            cfg['_variant_applied'] = summary
        out_name = f"{self.task_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{task_idx:03d}.mp4"
        out_path = os.path.join(cfg['out_dir'], out_name)

        cmd.extend(['-filter_complex', filter_complex, '-map', current_v])
        if audio_map:
            cmd.extend(['-map', audio_map, '-c:a', 'aac'])
        cmd.extend(['-r', fps_val, '-b:v', cfg['bitrate'], '-t', f"{t_total:.3f}"])

        success, error = render_video(
            cmd, cfg.get('enable_gpu', True), out_path, self.temp_dir_path,
            config=cfg, log=self.log, is_cancelled=lambda: not self.is_running,
            on_process=self._track_process,
        )
        if not success and self.is_running:
            from .hardware import short_error
            self.log(f"    [{self.task_name}] 视频 {task_idx:03d} 失败：{short_error(error)}")

        if success and self.is_running:
            if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
                now_str = datetime.now().strftime("%H:%M:%S")
                self.log(f"    [{now_str}] [{self.task_name}] 视频 {task_idx:03d} 输出异常（文件过小或不存在），可能编码失败")
                return (False, None, None) if return_result else False
            if 'id' in hook_clip:
                with self.shared.lock:
                    self.shared.usage_history.add(hook_clip['id'])
                self.shared.save_state()
            elapsed_time = time.time() - start_time
            self.last_output_path = out_path
            self.last_elapsed = round(elapsed_time, 1)
            now_str = datetime.now().strftime("%H:%M:%S")
            self.log(f"    [{now_str}] [{self.task_name}] 视频 {task_idx:03d} 完成，耗时 {elapsed_time:.1f} 秒 -> {out_name}")
            return (True, out_path, self.last_elapsed) if return_result else True
        return (False, None, None) if return_result else False

    def _track_process(self, process):
        with self.core_lock:
            if process is not None:
                self.processes.add(process)
            else:
                self.processes = {p for p in self.processes if p.poll() is None}

    def stop(self):
        self.is_running = False
        with self.core_lock:
            processes = list(self.processes)
        for process in processes:
            try:
                process.terminate()
            except Exception:
                pass
