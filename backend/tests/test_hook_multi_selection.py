import tempfile
import unittest
from pathlib import Path

from app.api.routes import scan_directory
from app.core.video_matrix import SharedMediaCache, VideoMatrixCore
from app.models.schemas import ScanRequest, VideoConfig
from app.services.task_service import TaskService
from tests.test_grouped_body_and_cover import run


class HookMultiSelectionTests(unittest.TestCase):
    def test_selected_files_are_the_only_hooks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_dir = root / 'first'
            second_dir = root / 'second'
            first_dir.mkdir()
            second_dir.mkdir()
            first = first_dir / 'a.mp4'
            second = second_dir / 'b.mov'
            unselected = first_dir / 'unselected.mp4'
            for path in (first, second, unselected):
                path.touch()
            chosen = f'{first}; {second}; {first}'

            core = VideoMatrixCore.__new__(VideoMatrixCore)
            self.assertEqual(core._scan_files(chosen, ('.mp4', '.mov')), [str(first), str(second)])
            scanned = scan_directory(ScanRequest(dir_path=chosen, extensions=['.mp4', '.mov']))
            self.assertEqual(scanned.files, [str(first), str(second)])
            self.assertEqual(scanned.count, 2)

            service = TaskService.__new__(TaskService)
            config = VideoConfig(hook_dir=chosen, body_dirs=[str(root / 'body')])
            normalized = service._normalize_config(config.model_dump())
            self.assertEqual(Path(normalized['base_out_dir']).parent, first_dir)
            tasks = service._get_tasks_from_config(normalized)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]['hook_dir'], chosen)
            self.assertEqual(tasks[0]['name'], 'a_Multi_Hook')

    def test_random_preflight_uses_only_explicitly_selected_videos(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hook_dir = root / 'hooks'
            body_dir = root / 'body'
            hook_dir.mkdir()
            body_dir.mkdir()
            first, second, unselected = (hook_dir / f'{name}.mp4' for name in ('first', 'second', 'unselected'))
            for path, color in ((first, 'red'), (second, 'green'), (unselected, 'yellow')):
                run('-f', 'lavfi', '-i', f'color={color}:s=64x64:r=24:d=1', str(path))
            run('-f', 'lavfi', '-i', 'color=blue:s=64x64:r=24:d=1', str(body_dir / 'body.mp4'))
            config = VideoConfig(
                hook_dir=f'{first}; {second}', body_dirs=[str(body_dir)],
                base_out_dir=str(root / 'out'), t_hook_min=0.5, t_hook_max=0.5,
                t_body_min=0.5, t_body_max=0.5, total_clips=2,
                target_count=1, resolution='64*64', fps=24, bitrate='300k',
                enable_gpu=False, vol_orig=0, vol_hook_orig=0,
            ).model_dump()
            config['out_dir'] = str(root / 'out')
            core = VideoMatrixCore(config, lambda _message: None, SharedMediaCache(str(root / 'state')))
            try:
                ok, message = core.pre_flight_check()
                self.assertTrue(ok, message)
                self.assertEqual({item['file'] for item in core.hook_pool}, {str(first), str(second)})
                (root / 'out').mkdir(exist_ok=True)
                rendered, output, _ = core.render_single_video(1, return_result=True)
                self.assertTrue(rendered)
                self.assertTrue(Path(output).is_file())
            finally:
                core.temp_dir.cleanup()


if __name__ == '__main__':
    unittest.main()
