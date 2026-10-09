"""Regression checks for a diagnosed, renderable speech-logic fallback.

These cases intentionally do not assert a word-perfect transcript: when the
source is unrecognised, the user needs a rendered draft plus explicit review
diagnostics, not a silent random-order edit or an empty output directory.
"""

import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.ffmpeg import FFMPEG, probe_media
from app.core.video_matrix import SharedMediaCache, VideoMatrixCore
from app.models.schemas import VideoConfig
from app.services.task_service import TaskService
from app.core.speech_transcript import SpeechTranscriptService
from app.core.semantic_selection import plan_clip_level_fallback


def make_video(folder: Path, name: str, color: str = 'blue') -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{name}.mp4'
    subprocess.run([
        FFMPEG, '-y', '-loglevel', 'error',
        '-f', 'lavfi', '-i', f'color=c={color}:s=64x64:r=24:d=2',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100:d=2',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest',
        str(path),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return path


def make_silent_video(folder: Path, name: str, color: str = 'blue') -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{name}.mp4'
    subprocess.run([
        FFMPEG, '-y', '-loglevel', 'error',
        '-f', 'lavfi', '-i', f'color=c={color}:s=64x64:r=24:d=2',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-an', str(path),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return path


def make_audio_only_mp4(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{name}.mp4'
    subprocess.run([
        FFMPEG, '-y', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100:d=2',
        '-c:a', 'aac', '-vn', str(path),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return path


def cue(text: str, *, visual: str = 'unknown') -> dict:
    return {'start': 0.2, 'end': 1.2, 'text': text,
            'visual_offer_status': visual, 'offer_status': 'none'}


def config(root: Path, hook: Path, body: Path, **changes) -> dict:
    value = {
        'task_name': 'fallback-regression', 'selection_mode': 'speech_logic',
        'semantic_sku': '', 'semantic_topic': '', 'hook_dir': str(hook),
        'body_dirs': [str(body)], 'body_mode': 'normal', 'duration_mode': 'clips',
        't_hook': 0.5, 't_body': 0.5, 'total_clips': 2, 'target_count': 1,
        'hook_r': 1, 'body_r': 0, 'bgm_r': 0, 'bgm_dir': '', 'voice_dir': None,
        'watermark_path': None, 'resolution': '64*64', 'fps': 24,
        'bitrate': '300k', 'enable_gpu': False,
        'vol_orig': 100, 'vol_hook_orig': 100, 'vol_bgm': 0, 'vol_voice': 0,
        'enable_srt': False, 'enable_variants': False,
        'enable_random_cover': False, 'apply_bgm_to_hook': True,
        'apply_voice_to_hook': True, 'apply_srt_to_hook': True,
        'apply_watermark_to_hook': True, 'base_out_dir': str(root / 'out'),
        'out_dir': str(root / 'out'),
    }
    value.update(changes)
    return value


class SpeechFallbackTests(unittest.TestCase):
    def test_no_fallback_mix_keeps_risk_clips_without_status_ranking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_video(root / 'hook', 'unlabelled-hook', 'red')
            make_video(root / 'body', 'unlabelled-body', 'green')
            settings = config(root, root / 'hook', root / 'body', no_fallback_mix=True)

            with self._with_cues({}):
                report = TaskService(SharedMediaCache(str(root / 'service-state'))).preflight(
                    VideoConfig(**settings))
            self.assertTrue(report['ok'])
            self.assertEqual(report['capacity'], 1)
            item = report['report'][0]
            self.assertIn('非分级混剪', item['message'])
            self.assertTrue(item['speech_logic_preview']['fallback'])
            self.assertEqual(item['speech_logic_preview']['fallback_mode'],
                             'unranked_clip_level')
            self.assertEqual({source['status'] for source in
                              item['speech_logic_preview']['transcripts']}, {'blocked'})

            core, logs, ok, message = self._preflight_core(
                root, root / 'hook', root / 'body', {}, no_fallback_mix=True)
            try:
                self.assertTrue(ok, message)
                self.assertEqual(core.semantic_plans[0]['fallback_mode'],
                                 'unranked_clip_level')
                (root / 'out').mkdir(exist_ok=True)
                success, output, _ = core.render_single_video(1, return_result=True)
                self.assertTrue(success, '\n'.join(logs))
                self.assertTrue(Path(output).is_file())
            finally:
                core.temp_dir.cleanup()

    def test_unranked_mix_ignores_status_order_but_excludes_known_cross_product(self):
        hook = [{'file': 'JXB-99-hook.mp4', 'duration_s': 2, 'status': 'usable',
                 'product_id': 'JXB-99', 'has_audio': True}]
        bodies = [
            {'file': f'JXB-99-body-{index}.mp4', 'duration_s': 2, 'status': status,
             'product_id': 'JXB-99', 'has_audio': True}
            for index, status in enumerate(('usable', 'review', 'blocked'))
        ]
        bodies.append({'file': 'JXB-COLOR-body.mp4', 'duration_s': 2,
                       'status': 'usable', 'product_id': 'JXB-COLOR', 'has_audio': True})
        first = plan_clip_level_fallback(hook, bodies, target_product='JXB-99',
                                         target_clips=2, max_variants=4, unranked=True)
        changed_statuses = [{**item, 'status': 'blocked' if item['status'] == 'usable' else 'usable'}
                            for item in bodies]
        second = plan_clip_level_fallback(hook, changed_statuses, target_product='JXB-99',
                                          target_clips=2, max_variants=4, unranked=True)
        first_order = [plan['segments'][1]['file'] for plan in first]
        second_order = [plan['segments'][1]['file'] for plan in second]
        self.assertEqual(first_order, second_order)
        self.assertEqual(len(first_order), 3)
        self.assertNotIn('JXB-COLOR-body.mp4', first_order)
        self.assertTrue(all(plan['fallback_mode'] == 'unranked_clip_level' for plan in first))

        unknown_hook = [{'file': 'unknown-hook.mp4', 'duration_s': 2,
                         'status': 'blocked', 'product_id': '', 'has_audio': True}]
        no_target = plan_clip_level_fallback(unknown_hook, bodies,
                                             target_clips=3, max_variants=4,
                                             unranked=True)
        self.assertTrue(no_target)
        for plan in no_target:
            known_products = {segment['product_id'] for segment in plan['segments']
                              if segment['product_id']}
            self.assertLessEqual(len(known_products), 1)

    def test_later_risk_tiers_fill_extra_drafts_after_same_product(self):
        hook = [{'file': 'JXB-99-hook.mp4', 'duration_s': 2, 'status': 'usable',
                 'product_id': 'JXB-99', 'has_audio': True}]
        bodies = [
            {'file': 'JXB-99-body.mp4', 'duration_s': 2, 'status': 'usable',
             'product_id': 'JXB-99', 'has_audio': True},
            {'file': 'unknown-body.mp4', 'duration_s': 2, 'status': 'review',
             'product_id': '', 'has_audio': True},
            {'file': 'JXB-COLOR-body.mp4', 'duration_s': 2, 'status': 'blocked',
             'product_id': 'JXB-COLOR', 'has_audio': True},
        ]
        plans = plan_clip_level_fallback(hook, bodies, target_product='JXB-99',
                                         target_clips=2, max_variants=3)
        self.assertEqual([Path(plan['segments'][1]['file']).name for plan in plans],
                         ['JXB-99-body.mp4', 'unknown-body.mp4', 'JXB-COLOR-body.mp4'])
        self.assertNotIn('跨产品', ' '.join(plans[0]['warnings']))
        self.assertIn('跨产品', ' '.join(plans[2]['warnings']))

    def _with_cues(self, cues_by_name: dict[str, list[dict]]):
        def fake_get_cues(_self, file_path, allow_asr=True):
            return [dict(item) for item in cues_by_name.get(Path(file_path).name, [])]
        return patch.object(SpeechTranscriptService, 'get_cues', new=fake_get_cues)

    def _preflight_core(self, root: Path, hook: Path, body: Path,
                        cues_by_name: dict[str, list[dict]], **changes):
        logs = []
        core = VideoMatrixCore(
            config(root, hook, body, **changes), logs.append,
            SharedMediaCache(str(root / 'state')),
        )
        with self._with_cues(cues_by_name):
            ok, message = core.pre_flight_check()
        return core, logs, ok, message

    def test_unknown_product_and_no_transcript_have_diagnostics_and_render(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_video(root / 'hook', 'unlabelled-hook', 'red')
            make_video(root / 'body', 'unlabelled-body', 'green')
            (root / 'out').mkdir()
            cfg = config(root, root / 'hook', root / 'body')

            with self._with_cues({}):
                report = TaskService(SharedMediaCache(str(root / 'service-state'))).preflight(VideoConfig(**cfg))
            self.assertTrue(report['ok'], report)
            preview = report['report'][0]['speech_logic_preview']
            self.assertTrue(preview['fallback'])
            self.assertEqual({item['status'] for item in preview['transcripts']}, {'blocked'})
            self.assertTrue(all(item['reasons'] for item in preview['transcripts']))
            self.assertTrue(preview['warnings'])

            core, logs, ok, message = self._preflight_core(root, root / 'hook', root / 'body', {})
            try:
                self.assertTrue(ok, message)
                self.assertTrue(core.semantic_plans[0]['fallback'])
                self.assertIn('降级', message)
                self.assertEqual([item['role'] for item in core.semantic_plans[0]['segments']], ['hook', 'body'])
                success, output, _ = core.render_single_video(1, return_result=True)
                self.assertTrue(success, '\n'.join(logs))
                self.assertTrue(Path(output).is_file())
                self.assertGreater(float(probe_media(output)['format']['duration']), 2.5)
            finally:
                core.temp_dir.cleanup()

    def test_task_service_exports_two_drafts_after_warning_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_video(root / 'hook', 'unlabelled-hook-a', 'red')
            make_video(root / 'hook', 'unlabelled-hook-b', 'blue')
            make_video(root / 'body', 'unlabelled-body-a', 'green')
            make_video(root / 'body', 'unlabelled-body-b', 'yellow')
            settings = config(root, root / 'hook', root / 'body', target_count=2)
            service = TaskService(SharedMediaCache(str(root / 'task-state')))
            with self._with_cues({}):
                report = service.preflight(VideoConfig(**settings))
                self.assertTrue(report['ok'], report)
                self.assertGreaterEqual(report['capacity'], 2)
                task_id = service.create_task(VideoConfig(**settings))
                deadline = time.monotonic() + 20
                while service.get_task(task_id).status in {'pending', 'running'} and time.monotonic() < deadline:
                    time.sleep(0.05)
            status = service.get_task(task_id)
            self.assertEqual(status.status, 'completed', '\n'.join(service.get_logs(task_id)))
            self.assertEqual(len(status.output_files), 2, '\n'.join(service.get_logs(task_id)))
            self.assertTrue(all(Path(path).is_file() for path in status.output_files))

    def test_unknown_product_with_transcripts_is_not_relabelled_to_a_guess(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_video(root / 'hook', 'unlabelled-hook', 'red')
            make_video(root / 'body', 'unlabelled-body', 'green')
            (root / 'out').mkdir()
            cues = {
                'unlabelled-hook.mp4': [cue('为什么刷完牙还是不清爽？')],
                'unlabelled-body.mp4': [cue('刷牙的时候要仔细清洁。')],
            }
            core, logs, ok, message = self._preflight_core(
                root, root / 'hook', root / 'body', cues)
            try:
                self.assertTrue(ok, message)
                self.assertTrue(core.semantic_plans[0]['fallback'])
                self.assertEqual(core.semantic_plans[0]['product_id'], '')
                self.assertTrue(core.semantic_plans[0]['warnings'])
                success, output, _ = core.render_single_video(1, return_result=True)
                self.assertTrue(success, '\n'.join(logs))
                self.assertTrue(Path(output).is_file())
            finally:
                core.temp_dir.cleanup()

    def test_usable_body_beats_review_body_for_same_product(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_video(root / 'hook', 'JXB-99-hook', 'red')
            usable = make_video(root / 'body', 'JXB-99-usable', 'green')
            make_video(root / 'body', 'JXB-99-review', 'blue')
            cues = {
                'JXB-99-hook.mp4': [cue('牙齿敏感吗？', visual='none')],
                'JXB-99-usable.mp4': [cue('刷牙时轻轻刷。', visual='none')],
                'JXB-99-review.mp4': [cue('刷牙时轻轻刷。', visual='unknown')],
            }
            core, _, ok, message = self._preflight_core(
                root, root / 'hook', root / 'body', cues, semantic_sku='99')
            try:
                self.assertTrue(ok, message)
                self.assertTrue(core.semantic_plans[0]['fallback'])
                by_name = {Path(item['source_file']).name: item['status'] for item in core.semantic_transcripts}
                self.assertEqual(by_name['JXB-99-usable.mp4'], 'usable')
                self.assertEqual(by_name['JXB-99-review.mp4'], 'review')
                self.assertEqual(Path(core.semantic_plans[0]['segments'][1]['file']), usable)
            finally:
                core.temp_dir.cleanup()

    def test_review_body_beats_blocked_body_for_same_product(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_video(root / 'hook', 'JXB-99-hook', 'red')
            review = make_video(root / 'body', 'JXB-99-review', 'green')
            make_video(root / 'body', 'JXB-99-no-transcript', 'blue')
            cues = {
                'JXB-99-hook.mp4': [cue('牙齿敏感吗？')],
                'JXB-99-review.mp4': [cue('刷牙时轻轻刷。')],
            }
            core, _, ok, message = self._preflight_core(
                root, root / 'hook', root / 'body', cues, semantic_sku='99')
            try:
                self.assertTrue(ok, message)
                self.assertTrue(core.semantic_plans[0]['fallback'])
                self.assertEqual(Path(core.semantic_plans[0]['segments'][1]['file']), review)
            finally:
                core.temp_dir.cleanup()

    def test_known_same_product_blocked_beats_cross_product_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_video(root / 'hook', 'JXB-99-hook', 'red')
            same = make_video(root / 'body', 'JXB-99-no-transcript', 'green')
            make_video(root / 'body', 'JXB-COLOR-review', 'blue')
            cues = {
                'JXB-99-hook.mp4': [cue('牙齿敏感吗？', visual='none')],
                'JXB-COLOR-review.mp4': [cue('这支色修牙膏适合黄牙。')],
            }
            core, _, ok, message = self._preflight_core(
                root, root / 'hook', root / 'body', cues, semantic_sku='99')
            try:
                self.assertTrue(ok, message)
                self.assertTrue(core.semantic_plans[0]['fallback'])
                self.assertEqual(Path(core.semantic_plans[0]['segments'][1]['file']), same)
                self.assertNotIn('跨产品', ' '.join(core.semantic_plans[0]['warnings']))
            finally:
                core.temp_dir.cleanup()

    def test_unavoidable_cross_product_is_loudly_warned_and_renderable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_video(root / 'hook', 'JXB-99-hook', 'red')
            make_video(root / 'body', 'JXB-COLOR-body', 'green')
            (root / 'out').mkdir()
            cues = {
                'JXB-99-hook.mp4': [cue('牙齿敏感吗？')],
                'JXB-COLOR-body.mp4': [cue('这支色修牙膏适合黄牙。')],
            }
            core, logs, ok, message = self._preflight_core(
                root, root / 'hook', root / 'body', cues, semantic_sku='99')
            try:
                self.assertTrue(ok, message)
                self.assertTrue(core.semantic_plans[0]['fallback'])
                warnings = ' '.join(core.semantic_plans[0]['warnings'])
                self.assertTrue('跨产品' in warnings or '产品不一致' in warnings, warnings)
                success, output, _ = core.render_single_video(1, return_result=True)
                self.assertTrue(success, '\n'.join(logs))
                self.assertTrue(Path(output).is_file())
            finally:
                core.temp_dir.cleanup()

    def test_promotion_only_talk_is_review_risk_not_empty_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_video(root / 'hook', 'JXB-99-hook', 'red')
            make_video(root / 'body', 'JXB-99-body', 'green')
            (root / 'out').mkdir()
            cues = {
                'JXB-99-hook.mp4': [cue('今天最后一天39元，买一送一！')],
                'JXB-99-body.mp4': [cue('快拍这支99牙膏，优惠马上结束！')],
            }
            core, logs, ok, message = self._preflight_core(
                root, root / 'hook', root / 'body', cues, semantic_sku='99')
            try:
                self.assertTrue(ok, message)
                self.assertTrue(core.semantic_plans[0]['fallback'])
                self.assertTrue(all(item['reasons'] for item in core.semantic_transcripts))
                self.assertTrue(core.semantic_plans[0]['warnings'])
                success, output, _ = core.render_single_video(1, return_result=True)
                self.assertTrue(success, '\n'.join(logs))
                self.assertTrue(Path(output).is_file())
            finally:
                core.temp_dir.cleanup()

    def test_silent_hook_and_body_render_a_review_only_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_silent_video(root / 'hook', 'JXB-99-silent-hook', 'red')
            make_silent_video(root / 'body', 'JXB-99-silent-body', 'green')
            (root / 'out').mkdir()
            core, logs, ok, message = self._preflight_core(
                root, root / 'hook', root / 'body', {}, semantic_sku='99')
            try:
                self.assertTrue(ok, message)
                plan = core.semantic_plans[0]
                self.assertTrue(plan['fallback'])
                self.assertTrue(all(not segment['has_audio'] for segment in plan['segments']))
                self.assertIn('无有效原声', ' '.join(plan['warnings']))
                success, output, _ = core.render_single_video(1, return_result=True)
                self.assertTrue(success, '\n'.join(logs))
                self.assertTrue(Path(output).is_file())
                self.assertTrue(any(stream['codec_type'] == 'video' for stream in probe_media(output)['streams']))
            finally:
                core.temp_dir.cleanup()

    def test_corrupt_video_coexists_with_good_video_without_poisoning_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good_hook = make_video(root / 'hook', 'JXB-99-good-hook', 'red')
            corrupt = root / 'hook' / 'JXB-99-corrupt.mp4'
            corrupt.write_bytes(b'not an mp4 file')
            good_body = make_video(root / 'body', 'JXB-99-good-body', 'green')
            (root / 'out').mkdir()
            core, logs, ok, message = self._preflight_core(
                root, root / 'hook', root / 'body', {}, semantic_sku='99')
            try:
                self.assertTrue(ok, message)
                chosen = [Path(segment['file']) for segment in core.semantic_plans[0]['segments']]
                self.assertEqual(chosen, [good_hook, good_body])
                self.assertTrue(any('JXB-99-corrupt.mp4' in line for line in logs))
                success, output, _ = core.render_single_video(1, return_result=True)
                self.assertTrue(success, '\n'.join(logs))
                self.assertTrue(Path(output).is_file())
            finally:
                core.temp_dir.cleanup()

    def test_only_corrupt_or_audio_only_input_fails_before_render(self):
        for unusable_side, kind in (
            ('hook', 'corrupt'), ('body', 'corrupt'),
            ('hook', 'audio_only'), ('body', 'audio_only'),
        ):
            with self.subTest(unusable_side=unusable_side, kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                good_side = 'body' if unusable_side == 'hook' else 'hook'
                make_video(root / good_side, f'JXB-99-good-{good_side}')
                if kind == 'corrupt':
                    folder = root / unusable_side
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / 'JXB-99-broken.mp4').write_bytes(b'broken container')
                else:
                    make_audio_only_mp4(root / unusable_side, 'JXB-99-audio-only')
                core, _, ok, message = self._preflight_core(
                    root, root / 'hook', root / 'body', {}, semantic_sku='99')
                try:
                    self.assertFalse(ok, message)
                    self.assertFalse(core.semantic_plans)
                    self.assertFalse((root / 'out').exists() and list((root / 'out').glob('*.mp4')))
                finally:
                    core.temp_dir.cleanup()

    def test_requested_six_segments_with_two_sources_reports_actual_two(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_video(root / 'hook', 'JXB-99-hook', 'red')
            make_video(root / 'body', 'JXB-99-body', 'green')
            core, _, ok, message = self._preflight_core(
                root, root / 'hook', root / 'body', {},
                semantic_sku='99', total_clips=6)
            try:
                self.assertTrue(ok, message)
                plan = core.semantic_plans[0]
                self.assertEqual(len(plan['segments']), 2)
                self.assertIn('实际仅 2 段，低于设置 6 段', ' '.join(plan['warnings']))
            finally:
                core.temp_dir.cleanup()


if __name__ == '__main__':
    unittest.main()
