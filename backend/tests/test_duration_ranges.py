import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.core.ffmpeg import probe_media
from app.core.video_matrix import SharedMediaCache, VideoMatrixCore
from app.models.schemas import VideoConfig
from app.services.task_service import TaskService
from tests.test_grouped_body_and_cover import run


class DurationRangeTests(unittest.TestCase):
    def test_legacy_fixed_fields_and_explicit_ranges(self):
        legacy = VideoConfig(hook_dir='hook', t_hook=1.25, t_body=0.75)
        self.assertEqual((legacy.t_hook_min, legacy.t_hook_max), (1.25, 1.25))
        self.assertEqual((legacy.t_body_min, legacy.t_body_max), (0.75, 0.75))

        ranged = VideoConfig(
            hook_dir='hook', t_hook_min=0.6, t_hook_max=1.2,
            t_body_min=0.5, t_body_max=1.5,
        )
        self.assertEqual((ranged.t_hook_min, ranged.t_hook_max), (0.6, 1.2))
        self.assertEqual((ranged.t_body_min, ranged.t_body_max), (0.5, 1.5))
        self.assertEqual(VideoConfig(hook_dir='hook', t_hook_min=0.8).t_hook_max, 0.8)
        with self.assertRaises(ValidationError):
            VideoConfig(hook_dir='hook', t_hook_min=2, t_hook_max=1)
        with self.assertRaises(ValidationError):
            VideoConfig(hook_dir='hook', t_body_min=1, t_body_max=0.5)

    def test_single_hook_file_makes_one_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hook = root / 'single.mp4'
            hook.touch()
            (root / 'body').mkdir()
            service = TaskService(SharedMediaCache(str(root / 'state')))
            normalized = service._normalize_config({
                'hook_dir': str(hook), 'body_dirs': [str(root / 'body')],
            })
            self.assertEqual(normalized['base_out_dir'], str(root / 'single_VideoMatrix_Output'))
            tasks = service._get_tasks_from_config(normalized)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]['name'], 'single')
            self.assertEqual(tasks[0]['hook_dir'], str(hook))
            self.assertEqual(tasks[0]['body_dirs'], [str(root / 'body')])

    def test_ranged_hook_and_body_render_without_exceeding_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('body', 'out'):
                (root / name).mkdir()
            hook = root / 'single.mp4'
            body = root / 'body' / 'b.mp4'
            run('-f', 'lavfi', '-i', 'color=red:s=64x64:r=24:d=0.75', str(hook))
            run('-f', 'lavfi', '-i', 'color=blue:s=64x64:r=24:d=1.25', str(body))
            config = VideoConfig(
                hook_dir=str(hook), body_dirs=[str(root / 'body')],
                base_out_dir=str(root / 'out'), t_hook_min=0.5, t_hook_max=1.0,
                t_body_min=0.5, t_body_max=1.0, total_clips=3,
                target_count=2, hook_r=1, body_r=0, resolution='64*64',
                fps=24, bitrate='300k', enable_gpu=False, vol_orig=0,
                vol_hook_orig=0, vol_bgm=0,
            ).model_dump()
            config.update(out_dir=str(root / 'out'), _selection_seed=17)
            core = VideoMatrixCore(config, lambda _message: None, SharedMediaCache(str(root / 'state')))
            ok, message = core.pre_flight_check()
            self.assertTrue(ok, message)
            self.assertEqual(len(core.hook_pool), 1)
            self.assertLessEqual(core.hook_pool[0]['max_duration'], 0.75 + 0.001)
            for clip in core.body_pool:
                self.assertGreaterEqual(clip['max_duration'], 0.5)
                self.assertLessEqual(clip['max_duration'], 1.25 - clip['start'] + 0.001)

            durations = []
            for index in (1, 2):
                success, output, _ = core.render_single_video(index, return_result=True)
                self.assertTrue(success)
                timeline = core.output_configs[index]
                hook_duration = timeline['t_hook']
                body_durations = timeline['_body_clip_durations']
                self.assertGreaterEqual(hook_duration, 0.5)
                self.assertLessEqual(hook_duration, 0.75 + 0.001)
                self.assertEqual(len(body_durations), 2)
                self.assertTrue(all(0.5 <= value <= 1.0 for value in body_durations))
                video = next(s for s in probe_media(output)['streams'] if s['codec_type'] == 'video')
                self.assertAlmostEqual(
                    float(video['duration']), hook_duration + sum(body_durations), delta=1 / 24 + 0.001,
                )
                durations.append((hook_duration, *body_durations))
            self.assertNotEqual(durations[0], durations[1])
            core.temp_dir.cleanup()


if __name__ == '__main__':
    unittest.main()
