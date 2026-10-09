import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.watermark_removal import (
    CleanupRequest, RegionInput, WatermarkRemovalService,
)
from app.api.accounts import get_account_service
from app.api.watermark_removal import watermark_removal_service
from app.core.ffmpeg import FFMPEG, probe_media
from app.core.watermark_removal import (
    Region, detect_region, inspect_video, locate_static_overlay,
    render_delogo, scale_region, validate_region,
)
from app.main import app


def _make_video(path: Path) -> None:
    result = subprocess.run([
        FFMPEG, '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i',
        'testsrc2=s=320x240:r=15:d=1.3,drawbox=x=12:y=12:w=54:h=26:color=white:t=fill,'
        'drawbox=x=20:y=17:w=5:h=16:color=black:t=fill,'
        'drawbox=x=33:y=17:w=5:h=16:color=black:t=fill,'
        'drawbox=x=46:y=17:w=5:h=16:color=black:t=fill',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.3',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
        '-shortest', str(path),
    ], capture_output=True, timeout=30)
    if result.returncode:
        raise AssertionError(result.stderr.decode('utf-8', 'replace'))


class WatermarkRemovalCoreTests(unittest.TestCase):
    def test_manual_region_validation_and_real_audio_preserving_render(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.mp4'
            output = Path(directory) / 'cleaned.mp4'
            _make_video(source)
            info = inspect_video(source)
            self.assertEqual((info.width, info.height, info.has_audio), (320, 240, True))
            with self.assertRaises(ValueError):
                validate_region(Region(300, 200, 30, 50), info)
            self.assertEqual(scale_region(Region(6, 6, 32, 16), 160, 120, info),
                             Region(12, 12, 64, 32))
            fractions = []
            render_delogo(source, output, Region(12, 12, 54, 26), info,
                          threading.Event(), on_progress=fractions.append)
            media = probe_media(str(output))
            self.assertTrue(output.stat().st_size > 1024)
            self.assertEqual([stream['codec_type'] for stream in media['streams']],
                             ['video', 'audio'])
            self.assertEqual((media['streams'][0]['width'], media['streams'][0]['height']),
                             (320, 240))
            self.assertEqual(fractions[-1], 1.0)

    def test_non_mp4_source_audio_is_transcoded_for_mp4_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.mkv'
            output = Path(directory) / 'cleaned.mp4'
            result = subprocess.run([
                FFMPEG, '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'lavfi', '-i', 'testsrc2=s=320x240:r=10:d=1',
                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
                '-c:v', 'ffv1', '-c:a', 'pcm_s16le', '-shortest', str(source),
            ], capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
            render_delogo(source, output, Region(10, 10, 30, 20), inspect_video(source),
                          threading.Event())
            media = probe_media(str(output))
            audio = next(stream for stream in media['streams'] if stream['codec_type'] == 'audio')
            self.assertEqual(audio['codec_name'], 'aac')

    def test_region_touching_frame_boundary_still_renders(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.mp4'
            _make_video(source)
            info = inspect_video(source)
            for name, region in (
                ('top-left', Region(0, 0, 30, 20)),
                ('bottom-right', Region(290, 220, 30, 20)),
            ):
                output = Path(directory) / f'{name}.mp4'
                render_delogo(source, output, region, info, threading.Event())
                media = probe_media(str(output))
                self.assertEqual((media['streams'][0]['width'], media['streams'][0]['height']),
                                 (320, 240))

    def test_no_automatic_region_when_scene_is_still(self):
        frame = bytes([128]) * (256 * 192)
        region, confidence = locate_static_overlay([frame] * 7, 256, 192)
        self.assertIsNone(region)
        self.assertEqual(confidence, 0)

    def test_automatic_detection_is_conservative_on_real_video(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.mp4'
            _make_video(source)
            info = inspect_video(source)
            region, confidence = detect_region(source, info)
            # A false positive away from the corner would damage the footage;
            # no result is acceptable and leads to manual selection.
            if region is not None:
                self.assertGreaterEqual(confidence, .70)
                self.assertLess(region.x, 90)
                self.assertLess(region.y, 65)
                validate_region(region, info)


class WatermarkRemovalServiceTests(unittest.TestCase):
    def _wait(self, service: WatermarkRemovalService, task_id: str) -> dict:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            job = service.get(task_id)
            if job['status'] in ('completed', 'failed', 'stopped'):
                return job
            time.sleep(.05)
        self.fail('watermark-removal job did not complete')

    def test_authorization_required_and_free_export_is_branded(self):
        service = WatermarkRemovalService()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.mp4'
            _make_video(source)
            common = {'input_paths': [str(source)], 'output_dir': str(Path(directory) / 'out'),
                      'mode': 'manual', 'region': RegionInput(x=12, y=12, width=54, height=26)}
            with self.assertRaisesRegex(ValueError, '版权'):
                service.create(CleanupRequest(**common))
            job_id = service.create(CleanupRequest(**common, authorized=True))['task_id']
            job = self._wait(service, job_id)
            self.assertEqual(job['status'], 'completed', job['errors'])
            self.assertEqual(job['progress'], 100)
            self.assertEqual(len(job['output_files']), 1)
            self.assertTrue(Path(job['output_files'][0]).is_file())
            self.assertTrue(any('俊小白品牌水印' in line for line in job['log_lines']))

    def test_branding_failure_fails_closed_and_paid_export_skips_branding(self):
        service = WatermarkRemovalService()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.mp4'
            _make_video(source)
            request = CleanupRequest(
                input_paths=[str(source)], output_dir=str(Path(directory) / 'out'),
                mode='manual', region=RegionInput(x=12, y=12, width=54, height=26),
                authorized=True,
            )
            with patch('app.api.watermark_removal.apply_brand_watermark',
                       return_value=(False, '模拟品牌水印失败')):
                job = self._wait(service, service.create(request)['task_id'])
            self.assertEqual(job['status'], 'failed')
            self.assertEqual(job['output_files'], [])
            self.assertEqual(list((Path(directory) / 'out').glob('*.mp4')), [])
            self.assertIn('模拟品牌水印失败', job['errors'][0])

            with patch('app.api.watermark_removal.apply_brand_watermark') as brand:
                paid_job = self._wait(service, service.create(request, is_member=True)['task_id'])
            self.assertEqual(paid_job['status'], 'completed', paid_job['errors'])
            brand.assert_not_called()

    def test_api_job_requires_explicit_rights_confirmation(self):
        with TestClient(app) as client:
            response = client.post('/api/watermark-removal/jobs', json={
                'input_paths': ['C:/does-not-exist.mp4'], 'authorized': False,
            })
        self.assertEqual(response.status_code, 422)
        self.assertIn('版权', response.json()['detail'])

    def test_api_detect_returns_video_dimensions_and_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.mp4'
            _make_video(source)
            with TestClient(app) as client:
                response = client.post('/api/watermark-removal/detect',
                                       json={'input_path': str(source)})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual((response.json()['width'], response.json()['height']), (320, 240))
            self.assertTrue(response.json()['preview'].startswith('data:image/jpeg;base64,'))

    def test_api_uses_server_side_membership_instead_of_client_field(self):
        class Accounts:
            def is_member(self, token):
                return token == 'valid-session-token'

        app.dependency_overrides[get_account_service] = lambda: Accounts()
        try:
            with patch.object(watermark_removal_service, 'create', return_value={'task_id': 'test'}) as create:
                with TestClient(app) as client:
                    response = client.post('/api/watermark-removal/jobs',
                        headers={'Authorization': 'Bearer valid-session-token'},
                        json={'input_paths': ['C:/fixture.mp4'], 'authorized': True})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'task_id': 'test'})
            self.assertTrue(create.call_args.kwargs['is_member'])
        finally:
            app.dependency_overrides.pop(get_account_service, None)


if __name__ == '__main__':
    unittest.main()
