"""Benchmark progress is tied to completed work and never a fake 95% timer."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes
from app.models.schemas import VideoConfig
from app.services.task_service import TaskService
from tests.test_speech_logic_engine import config as media_config, source


class FakeCore:
    capacity = 4
    instances = []

    def __init__(self, config, _log, _cache):
        self.config = config
        self.n_total = self.capacity
        self.is_running = True
        self.temp_dir = self
        self.cleaned = False
        self.instances.append(self)

    def pre_flight_check(self):
        self.config['target_count'] = min(self.config['target_count'], self.capacity)
        return True, '预检通过'

    def cleanup(self):
        self.cleaned = True

    def stop(self):
        self.is_running = False


class BenchmarkProgressTests(unittest.TestCase):
    def setUp(self):
        FakeCore.capacity = 4
        FakeCore.instances = []

    def request(self):
        return VideoConfig(hook_dir='test-hook', body_dirs=['test-body'])

    def test_real_work_progress_and_temporary_core_cleanup(self):
        updates = []
        service = TaskService()
        with patch('app.services.task_service.VideoMatrixCore', FakeCore), \
                patch.object(service, '_render_job', return_value=True) as render:
            result = service.get_benchmark(
                self.request(), progress_callback=lambda percent, message: updates.append((percent, message)),
            )

        self.assertEqual(render.call_count, 10)  # 1 + 2 + 3 + 4 real renders
        self.assertEqual(set(result['results']), {1, 2, 3, 4})
        self.assertEqual(updates[0][0], 0)
        self.assertEqual(updates[-1][0], 100)
        self.assertEqual([percent for percent, _ in updates], sorted(percent for percent, _ in updates))
        self.assertTrue(any('已完成 3/3 条' in message for _, message in updates))
        self.assertTrue(all(core.cleaned for core in FakeCore.instances))

    def test_insufficient_capacity_skips_unmeasurable_levels(self):
        FakeCore.capacity = 2
        service = TaskService()
        with patch('app.services.task_service.VideoMatrixCore', FakeCore), \
                patch.object(service, '_render_job', return_value=True) as render:
            result = service.get_benchmark(self.request())

        self.assertEqual(render.call_count, 3)
        self.assertEqual(set(result['results']), {1, 2})
        self.assertTrue(any('3 路及更高并发未测试' in warning for warning in result['warnings']))
        self.assertEqual(len(FakeCore.instances), 3)

    def test_timeout_stops_current_render_instead_of_waiting_forever(self):
        service = TaskService()
        service.BENCHMARK_ROUND_TIMEOUT_SECONDS = 0.02

        def slow_render(core, _index, _status):
            while core.is_running:
                time.sleep(0.005)
            return False

        started = time.monotonic()
        with patch('app.services.task_service.VideoMatrixCore', FakeCore), \
                patch.object(service, '_render_job', side_effect=slow_render):
            result = service.get_benchmark(self.request())

        self.assertLess(time.monotonic() - started, 2)
        self.assertIn('error', result)
        self.assertTrue(any('超' in warning for warning in result['warnings']))
        self.assertEqual(len(FakeCore.instances), 1)
        self.assertFalse(FakeCore.instances[0].is_running)

    def test_streaming_route_and_legacy_json(self):
        app = FastAPI()
        app.include_router(routes.router, prefix='/api')
        client = TestClient(app)

        def fake_benchmark(_config, progress_callback=None):
            if progress_callback:
                progress_callback(0, '准备')
                progress_callback(42, '2 路并发：已完成 1/2 条')
                progress_callback(100, '完成')
            return {'results': {1: {'concurrent': 1}}, 'best_concurrent': 1}

        try:
            with patch.object(routes.task_service, 'get_benchmark', side_effect=fake_benchmark):
                plain = client.post('/api/benchmark', json={'hook_dir': 'test'})
                self.assertEqual(plain.json()['best_concurrent'], 1)
                with client.stream('POST', '/api/benchmark', json={'hook_dir': 'test'},
                                   headers={'Accept': 'text/event-stream'}) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('text/event-stream', response.headers['content-type'])
                    events = [json.loads(line[6:]) for line in response.iter_lines()
                              if line.startswith('data: ')]
                self.assertEqual([event['type'] for event in events],
                                 ['progress', 'progress', 'progress', 'result'])
                self.assertEqual(events[1]['percent'], 42)

            def failed_benchmark(_config, progress_callback=None):
                progress_callback(20, '压测中')
                raise RuntimeError('测试故障')

            with patch.object(routes.task_service, 'get_benchmark', side_effect=failed_benchmark):
                with client.stream('POST', '/api/benchmark', json={'hook_dir': 'test'},
                                   headers={'Accept': 'text/event-stream'}) as response:
                    events = [json.loads(line[6:]) for line in response.iter_lines()
                              if line.startswith('data: ')]
                self.assertEqual([event['type'] for event in events], ['progress', 'error'])
                self.assertIn('测试故障', events[-1]['message'])
        finally:
            client.close()

    def test_small_real_media_benchmark_reaches_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source(root / 'hook', 'test-hook', 'red', 440, '这是一段开场。')
            source(root / 'body', 'test-body', 'blue', 550, '这是一段后段。')
            request = VideoConfig(**media_config(
                root, selection_mode='random', body_dir=root / 'body',
                base_out_dir=str(root / 'out'), total_clips=2, hook_r=1,
                t_hook=0.5, t_body=0.5, target_count=4,
            ))
            updates = []
            result = TaskService().get_benchmark(
                request, progress_callback=lambda percent, message: updates.append((percent, message)),
            )
            self.assertNotIn('error', result, result)
            self.assertEqual(set(result['results']), {1, 2, 3, 4})
            self.assertEqual(updates[-1][0], 100)
            self.assertFalse(any(percent == 95 for percent, _ in updates))


if __name__ == '__main__':
    unittest.main()
