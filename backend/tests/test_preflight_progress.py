"""Preflight progress reflects completed media work, not elapsed time."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes
from app.core.video_matrix import SharedMediaCache
from app.models.schemas import VideoConfig
from app.services.task_service import TaskService
from tests.test_speech_logic_engine import config, source


class PreflightProgressTests(unittest.TestCase):
    def test_speech_preflight_reports_completed_probe_and_transcription_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source(root / 'hook', 'JXB-99_hook', 'red', 440, '牙齿敏感吗？')
            source(root / 'body', 'JXB-99_demo', 'green', 550, '刷牙要用对方法。')
            source(root / 'body', 'JXB-99_product', 'blue', 660, '这是俊小白修护牙膏。')
            request = VideoConfig(**config(
                root, selection_mode='speech_logic', body_dir=root / 'body',
                base_out_dir=str(root / 'out'),
            ))
            updates = []
            result = TaskService(SharedMediaCache(str(root / 'state'))).preflight(
                request, progress_callback=lambda percent, message: updates.append((percent, message)),
            )

            self.assertEqual(len(result['report']), 1)
            self.assertEqual(updates[0][0], 0)
            self.assertEqual(updates[-1][0], 100)
            self.assertEqual([value for value, _ in updates],
                             sorted(value for value, _ in updates))
            self.assertTrue(any('探测媒体 3/3' in message for _, message in updates), updates)
            self.assertTrue(any('转录口播 1/3' in message for _, message in updates), updates)
            self.assertTrue(any('转录口播 3/3' in message for _, message in updates), updates)

    def test_streaming_route_keeps_json_compatibility_and_reports_errors(self):
        app = FastAPI()
        app.include_router(routes.router, prefix='/api')
        client = TestClient(app)
        request = {'hook_dir': 'sample'}

        def fake_preflight(_config, progress_callback=None):
            if progress_callback:
                progress_callback(0, '准备')
                progress_callback(50, '探测媒体 1/2')
                progress_callback(100, '完成')
            return {'ok': True, 'capacity': 1, 'report': []}

        try:
            with patch.object(routes.task_service, 'preflight', side_effect=fake_preflight):
                plain = client.post('/api/preflight', json=request)
                self.assertEqual(plain.json()['capacity'], 1)

                with client.stream('POST', '/api/preflight', json=request,
                                   headers={'Accept': 'text/event-stream'}) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('text/event-stream', response.headers['content-type'])
                    events = [json.loads(line[6:]) for line in response.iter_lines()
                              if line.startswith('data: ')]
                self.assertEqual([event['type'] for event in events],
                                 ['progress', 'progress', 'progress', 'result'])
                self.assertEqual(events[-1]['result']['capacity'], 1)

            def failed_preflight(_config, progress_callback=None):
                progress_callback(25, '探测媒体 1/4')
                raise RuntimeError('测试预检异常')

            with patch.object(routes.task_service, 'preflight', side_effect=failed_preflight):
                with client.stream('POST', '/api/preflight', json=request,
                                   headers={'Accept': 'text/event-stream'}) as response:
                    events = [json.loads(line[6:]) for line in response.iter_lines()
                              if line.startswith('data: ')]
                self.assertEqual([event['type'] for event in events], ['progress', 'error'])
                self.assertIn('测试预检异常', events[-1]['message'])
        finally:
            client.close()


if __name__ == '__main__':
    unittest.main()
