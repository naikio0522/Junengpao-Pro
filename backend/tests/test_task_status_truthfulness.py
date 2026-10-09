"""The queue status must describe actual exported videos, not just completed jobs."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.models.schemas import TaskStatus
from app.services.task_service import TaskService


class _ReadyCore:
    def __init__(self, config, _log, _cache):
        self.config = config
        self.task_name = config['task_name']
        self.temp_dir = tempfile.TemporaryDirectory()

    def pre_flight_check(self, progress_callback=None):
        if progress_callback:
            progress_callback('probe', 1, 1)
        return True, '预检通过'


class TaskStatusTruthfulnessTests(unittest.TestCase):
    def _run_with_results(self, results):
        with tempfile.TemporaryDirectory() as directory:
            service = TaskService()
            task_id = 'truthfulness-test'
            service.tasks[task_id] = TaskStatus(
                task_id=task_id, task_name='sample', status='pending', created_at=datetime.now(),
            )
            service.log_buffers[task_id] = []
            output = str(Path(directory) / 'output')
            task_specs = [{'name': 'sample', 'hook_dir': str(Path(directory) / 'hook'),
                           'body_dirs': [str(Path(directory) / 'body')], 'out': output}]
            with patch.object(service, '_get_tasks_from_config', return_value=task_specs), \
                    patch('app.services.task_service.VideoMatrixCore', _ReadyCore), \
                    patch('app.services.task_service.HardwareSession', return_value=object()), \
                    patch.object(service, '_render_job', side_effect=lambda _core, idx, _status, _progress: results[idx - 1]):
                service._run_pipeline(task_id, {'task_name': 'sample', 'target_count': len(results),
                                                'concurrent_tasks': 1})
            return service.tasks[task_id], service.log_buffers[task_id]

    def test_zero_outputs_is_failure(self):
        status, logs = self._run_with_results([False, False])
        self.assertEqual(status.status, 'failed')
        self.assertEqual(status.current, 2)
        self.assertIn('均未输出视频', status.message)
        self.assertTrue(any('[失败]' in line for line in logs))

    def test_partial_outputs_are_marked_as_partial(self):
        status, logs = self._run_with_results([True, False])
        self.assertEqual(status.status, 'completed')
        self.assertIn('部分完成：成功 1/2 条', status.message)
        self.assertTrue(any('[警告]' in line for line in logs))


if __name__ == '__main__':
    unittest.main()
