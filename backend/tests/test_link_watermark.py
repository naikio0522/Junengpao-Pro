import io
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from yt_dlp.utils import DownloadError

from app.api.link_watermark import (
    LinkDownloadRequest, LinkWatermarkService, ResolveRequest,
    link_watermark_service,
)
from app.core.link_watermark import (
    LinkResolutionError, _RestrictedYoutubeDL, _extract_info, _open_bounded,
    canonical_page_url, download_cover_image, download_direct_video,
    parse_public_link, resolve_public_video,
)
from app.main import app


def _public_dns(*args, **kwargs):
    return [(2, 1, 6, '', ('8.8.8.8', 443))]


def _fixture_result():
    return {
        'platform': '抖音', 'platform_key': 'douyin', 'title': '演示视频',
        'thumbnail_url': None,
        'webpage_url': 'https://www.douyin.com/video/123456789',
        'video_url': 'https://v26.douyinvod.com/clip.mp4',
        'watermark_status': 'unverified',
        'warning': '无法保证视频流不含画面水印',
        'source_id': '123456789',
        'formats': [{
            'id': 'f0', 'label': '视频流', 'width': 320, 'height': 240,
            'ext': 'mp4', 'filesize': 2048, 'watermark_status': 'unverified',
            'url': 'https://v26.douyinvod.com/clip.mp4',
            'http_headers': {}, 'acodec': 'aac',
        }],
    }


class LinkSafetyTests(unittest.TestCase):
    def test_only_explicit_supported_https_public_page_hosts(self):
        with patch('app.core.link_watermark.socket.getaddrinfo', side_effect=_public_dns):
            self.assertEqual(parse_public_link('3.28 复制 https://v.douyin.com/ABC123/ 打开'),
                             ('https://v.douyin.com/ABC123/', 'douyin'))
            for url in (
                'http://v.douyin.com/ABC/', 'https://v.douyin.com.evil.test/ABC/',
                'https://127.0.0.1/file', 'https://192.168.0.2/video',
                'https://v.douyin.com:8443/ABC/', 'https://v.kuaishou.com/ABC/',
                'https://name:secret@v.douyin.com/ABC/',
            ):
                with self.subTest(url=url), self.assertRaises(LinkResolutionError):
                    parse_public_link(url)

    def test_dns_private_address_rejected(self):
        with patch('app.core.link_watermark.socket.getaddrinfo',
                   return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaisesRegex(LinkResolutionError, '非公网'):
                parse_public_link('https://v.douyin.com/ABC/')

    def test_redirect_to_private_host_rejected_before_following(self):
        class Opener:
            calls = 0

            def open(self, request, timeout):
                self.calls += 1
                raise urllib.error.HTTPError(
                    request.full_url, 302, 'Moved',
                    {'Location': 'https://127.0.0.1/admin'}, io.BytesIO(),
                )

        opener = Opener()
        with patch('app.core.link_watermark._opener', return_value=opener), patch(
            'app.core.link_watermark.socket.getaddrinfo', side_effect=_public_dns,
        ):
            with self.assertRaises(LinkResolutionError):
                _open_bounded('https://v.douyin.com/ABC/', 'douyin', method='HEAD')
        self.assertEqual(opener.calls, 1)

    def test_empty_media_headers_still_use_cdn_policy(self):
        class Response(io.BytesIO):
            headers = {'Content-Type': 'video/mp4'}

        class Opener:
            calls = 0

            def open(self, request, timeout):
                self.calls += 1
                return Response(b'video')

        opener = Opener()
        with patch('app.core.link_watermark._opener', return_value=opener), patch(
            'app.core.link_watermark.socket.getaddrinfo', side_effect=_public_dns,
        ):
            response, _ = _open_bounded('https://v26.douyinvod.com/clip.mp4',
                                        'douyin', headers={}, media=True)
            response.close()
            with self.assertRaises(LinkResolutionError):
                _open_bounded('https://v26.douyinvod.com/clip.mp4', 'douyin')
        self.assertEqual(opener.calls, 1)

    def test_yt_dlp_extractor_redirect_is_checked_before_request(self):
        from yt_dlp.networking._urllib import RedirectHandler

        downloader = _RestrictedYoutubeDL('douyin', {
            'allowed_extractors': ['^Douyin$'], 'quiet': True, 'proxy': '',
        })
        try:
            handler = next(item for item in downloader._request_director.handlers.values()
                           if item.RH_KEY == '_RestrictedUrllib')
            opener = handler._create_instance({}, downloader.cookiejar)
            redirect = next(item for item in opener.handlers
                            if isinstance(item, RedirectHandler))
            with self.assertRaises(LinkResolutionError):
                redirect.redirect_request(None, None, 302, 'moved', {},
                                          'https://127.0.0.1/internal')
        finally:
            downloader.close()

    def test_short_link_network_failure_is_user_facing_error(self):
        class Opener:
            def open(self, request, timeout):
                raise urllib.error.URLError('connection timeout')

        with patch('app.core.link_watermark._opener', return_value=Opener()), patch(
            'app.core.link_watermark.socket.getaddrinfo', side_effect=_public_dns,
        ):
            with self.assertRaisesRegex(LinkResolutionError, '平台连接失败'):
                canonical_page_url('https://v.douyin.com/ABC/', 'douyin')

    def test_known_watermarked_format_is_not_offered(self):
        info = {
            'id': '123456789', 'title': '演示', 'duration': 12,
            'formats': [
                {'format_id': 'download_addr', 'url': 'https://v26.douyinvod.com/mark.mp4',
                 'ext': 'mp4', 'format_note': 'Download video, watermarked'},
                {'format_id': 'play_addr', 'url': 'https://v26.douyinvod.com/play.mp4',
                 'ext': 'mp4', 'height': 720, 'acodec': 'aac'},
            ],
        }
        with patch('app.core.link_watermark.socket.getaddrinfo', side_effect=_public_dns), patch(
            'app.core.link_watermark._extract_info', return_value=info,
        ):
            result = resolve_public_video('https://www.douyin.com/video/123456789')
        self.assertEqual(len(result['formats']), 1)
        self.assertEqual(result['formats'][0]['url'], 'https://v26.douyinvod.com/play.mp4')
        self.assertEqual(result['watermark_status'], 'unverified')
        self.assertIn('无法保证', result['warning'])

    def test_official_iesdouyin_short_redirect_is_normalized_without_opening_page(self):
        with patch('app.core.link_watermark._resolve_short_page', return_value=
                   'https://www.iesdouyin.com/share/video/7679408277695810856/'), patch(
            'app.core.link_watermark.socket.getaddrinfo', side_effect=_public_dns,
        ):
            canonical = canonical_page_url('https://v.douyin.com/ABC/', 'douyin')
        self.assertEqual(canonical,
                         'https://www.douyin.com/video/7679408277695810856')

    def test_cover_download_requires_real_image_signature(self):
        class Response(io.BytesIO):
            headers = {'Content-Type': 'image/jpeg'}

        with tempfile.TemporaryDirectory() as directory:
            stem = Path(directory) / 'cover'
            with patch('app.core.link_watermark._open_bounded', return_value=(
                Response(b'\xff\xd8\xff' + b'x' * 2048), '',
            )) as opened:
                path = download_cover_image('https://p1.douyinpic.com/pic',
                                            'douyin', stem, lambda: False)
            self.assertEqual(path.suffix, '.jpg')
            self.assertTrue(path.is_file())
            self.assertTrue(opened.call_args.kwargs['media'])
            with patch('app.core.link_watermark._open_bounded', return_value=(
                Response(b'<html>not image</html>'), '',
            )):
                with self.assertRaisesRegex(LinkResolutionError, '格式不受支持'):
                    download_cover_image('https://p1.douyinpic.com/pic',
                                         'douyin', Path(directory) / 'invalid', lambda: False)
            self.assertFalse((Path(directory) / '.invalid.cover-download').exists())

    def test_platform_cookie_requirement_has_actionable_local_fallback_message(self):
        with patch('app.core.link_watermark._RestrictedYoutubeDL.extract_info',
                   side_effect=DownloadError('Fresh cookies are needed')):
            with self.assertRaisesRegex(LinkResolutionError, '本地视频去水印'):
                _extract_info('https://www.douyin.com/video/123456789', 'douyin')

    def test_download_stream_has_hard_size_limit(self):
        class Response(io.BytesIO):
            headers = {'Content-Type': 'video/mp4', 'Content-Length': str(600 * 1024 * 1024)}

        with tempfile.TemporaryDirectory() as directory, patch(
            'app.core.link_watermark._open_bounded', return_value=(Response(b'x' * 2048), ''),
        ):
            path = Path(directory) / 'video.download'
            with self.assertRaisesRegex(LinkResolutionError, '512 MB'):
                download_direct_video(_fixture_result()['formats'][0], 'douyin', path,
                                      lambda: False, lambda _: None)
            self.assertFalse(path.exists())


class LinkServiceTests(unittest.TestCase):
    def _wait(self, service, task_id):
        until = time.monotonic() + 8
        while time.monotonic() < until:
            result = service.get(task_id)
            if result['status'] in ('completed', 'failed', 'stopped'):
                return result
            time.sleep(.02)
        self.fail('link download job did not finish')

    def test_resolve_requires_rights_and_hides_internal_headers(self):
        service = LinkWatermarkService()
        with self.assertRaisesRegex(LinkResolutionError, '授权'):
            service.resolve(ResolveRequest(url='https://v.douyin.com/12345/'))
        with patch('app.api.link_watermark.resolve_public_video', return_value=_fixture_result()):
            response = service.resolve(ResolveRequest(
                url='https://v.douyin.com/12345/', authorized=True))
        self.assertTrue(response['resolve_id'])
        self.assertNotIn('http_headers', response['formats'][0])
        self.assertNotIn('platform_key', response)

    def test_free_export_brand_failure_deletes_unbranded_video(self):
        service = LinkWatermarkService()
        with tempfile.TemporaryDirectory() as directory, patch(
            'app.api.link_watermark.resolve_public_video', return_value=_fixture_result(),
        ):
            resolved = service.resolve(ResolveRequest(url='https://v.douyin.com/12345/',
                                                      authorized=True))

            def fake_download(selected, platform, path, cancelled, progress):
                path.write_bytes(b'0' * 2048)
                progress(1)

            def fake_remux(source, destination, cancelled, progress):
                destination.write_bytes(b'1' * 2048)
                progress(1)

            with patch('app.api.link_watermark.download_direct_video', side_effect=fake_download), patch(
                'app.api.link_watermark.remux_to_mp4', side_effect=fake_remux,
            ), patch('app.api.link_watermark.apply_brand_watermark',
                     return_value=(False, '品牌水印失败')):
                task = service.create(LinkDownloadRequest(
                    resolve_id=resolved['resolve_id'], output_dir=directory,
                    authorized=True))['task_id']
                result = self._wait(service, task)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['output_files'], [])
            self.assertEqual(list(Path(directory).iterdir()), [])

            with patch('app.api.link_watermark.download_direct_video', side_effect=fake_download), patch(
                'app.api.link_watermark.remux_to_mp4', side_effect=fake_remux,
            ), patch('app.api.link_watermark.apply_brand_watermark') as brand:
                task = service.create(LinkDownloadRequest(
                    resolve_id=resolved['resolve_id'], output_dir=directory,
                    authorized=True), is_member=True)['task_id']
                result = self._wait(service, task)
            self.assertEqual(result['status'], 'completed', result['errors'])
            self.assertEqual(result['progress'], 100)
            self.assertTrue(Path(result['output_files'][0]).is_file())
            brand.assert_not_called()

    def test_api_does_not_accept_client_member_flag(self):
        with patch.object(link_watermark_service, 'resolve', return_value={
            'resolve_id': 'a' * 32, 'formats': [],
        }) as resolve:
            with TestClient(app) as client:
                response = client.post('/api/link-watermark/resolve', json={
                    'url': 'https://v.douyin.com/12345/', 'authorized': True,
                })
        self.assertEqual(response.status_code, 200)
        resolve.assert_called_once()
        with TestClient(app) as client:
            response = client.post('/api/link-watermark/jobs', json={
                'resolve_id': 'a' * 32, 'output_dir': 'C:/tmp',
                'authorized': False, 'is_member': True,
            })
        self.assertEqual(response.status_code, 422)
        self.assertIn('授权', response.json()['detail'])

    def test_cover_failure_does_not_discard_successful_video(self):
        service = LinkWatermarkService()
        source = _fixture_result()
        source['thumbnail_url'] = 'https://p1.douyinpic.com/pic'

        def fake_download(selected, platform, path, cancelled, progress):
            path.write_bytes(b'0' * 2048)

        def fake_remux(source, destination, cancelled, progress):
            destination.write_bytes(b'1' * 2048)

        with tempfile.TemporaryDirectory() as directory, patch(
            'app.api.link_watermark.resolve_public_video', return_value=source,
        ):
            resolved = service.resolve(ResolveRequest(url='https://v.douyin.com/12345/',
                                                      authorized=True))
            with patch('app.api.link_watermark.download_direct_video', side_effect=fake_download), patch(
                'app.api.link_watermark.remux_to_mp4', side_effect=fake_remux,
            ), patch('app.api.link_watermark.download_cover_image',
                     side_effect=OSError('封面网络失败')):
                task = service.create(LinkDownloadRequest(
                    resolve_id=resolved['resolve_id'], output_dir=directory,
                    authorized=True, save_cover=True), is_member=True)['task_id']
                job = self._wait(service, task)
            self.assertEqual(job['status'], 'completed', job['errors'])
            self.assertEqual(len(job['output_files']), 1)
            self.assertTrue(Path(job['output_files'][0]).is_file())
            self.assertTrue(any('封面保存失败' in line for line in job['log_lines']))

    def test_finished_job_history_is_bounded_without_pruning_active_jobs(self):
        service = LinkWatermarkService()
        now = time.monotonic()
        with service.lock:
            for index in range(45):
                task_id = f'done-{index}'
                service.jobs[task_id] = {'status': 'completed', 'output_files': [],
                                         'errors': [], 'log_lines': []}
                service.events[task_id] = threading.Event()
                service.finished_at[task_id] = now + index
            service.jobs['active'] = {'status': 'running', 'output_files': [],
                                      'errors': [], 'log_lines': []}
            service.events['active'] = threading.Event()
        self.assertIsNotNone(service.get('active'))
        self.assertLessEqual(len(service.finished_at), 40)
        self.assertIsNone(service.get('done-0'))
        self.assertIsNotNone(service.get('done-44'))


if __name__ == '__main__':
    unittest.main()
