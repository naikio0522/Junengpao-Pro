"""Resolve public short-video pages and save a selected direct video stream.

This is deliberately not a generic URL fetcher.  Only known public page
extractors and their media CDN hosts are accepted; it does not import browser
cookies, bypass access controls, or promise that a platform's stream is clean.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from yt_dlp import YoutubeDL
from yt_dlp.networking._urllib import RedirectHandler as YdlRedirectHandler
from yt_dlp.networking._urllib import UrllibRH
from yt_dlp.utils import DownloadError

from .ffmpeg import FFMPEG, FFPROBE, run_process


MAX_MEDIA_BYTES = 512 * 1024 * 1024
MAX_MEDIA_SECONDS = 15 * 60
MAX_DOWNLOAD_SECONDS = 180
_LINK_RE = re.compile(r'https://[^\s<>"\']+', re.I)
_PAGE_HOSTS = {
    'douyin': frozenset({'v.douyin.com', 'www.douyin.com', 'douyin.com',
                         'www.iesdouyin.com', 'iesdouyin.com'}),
    'xiaohongshu': frozenset({'xhslink.com', 'www.xhslink.com',
                             'www.xiaohongshu.com', 'xiaohongshu.com'}),
}
_SHORT_HOSTS = frozenset({'v.douyin.com', 'xhslink.com', 'www.xhslink.com'})
_MEDIA_SUFFIXES = {
    'douyin': ('douyinvod.com', 'douyin.com', 'snssdk.com',
               'byteimg.com', 'douyinpic.com', 'pstatp.com',
               'ibytedtos.com', 'bytedance.com'),
    'xiaohongshu': ('xhscdn.com', 'xiaohongshu.com'),
}


class _RestrictedUrllibRH(UrllibRH):
    """Check extractor redirects *before* urllib follows them."""

    def __init__(self, *, platform: str, **kwargs):
        self._allowed_platform = platform
        super().__init__(**kwargs)

    def _create_instance(self, proxies, cookiejar, legacy_ssl_support=None):
        opener = super()._create_instance(proxies, cookiejar, legacy_ssl_support)
        permitted = (*_MEDIA_SUFFIXES[self._allowed_platform],
                     *_PAGE_HOSTS[self._allowed_platform])
        for handler in opener.handlers:
            if not isinstance(handler, YdlRedirectHandler):
                continue
            original = handler.redirect_request

            def guarded_redirect(req, fp, code, msg, headers, newurl):
                _public_https_url(newurl, suffixes=permitted)
                return original(req, fp, code, msg, headers, newurl)

            handler.redirect_request = guarded_redirect
        return opener


class _RestrictedYoutubeDL(YoutubeDL):
    def __init__(self, platform: str, params: dict):
        self._allowed_platform = platform
        super().__init__(params)

    def build_request_director(self, handlers, preferences=None):
        # Force the audited urllib handler; an optional curl/requests backend
        # must not silently bypass the redirect guard.
        return super().build_request_director(
            [lambda **kwargs: _RestrictedUrllibRH(
                platform=self._allowed_platform, **kwargs)], preferences)

    def urlopen(self, req):
        url = req if isinstance(req, str) else req.url
        permitted = (*_MEDIA_SUFFIXES[self._allowed_platform],
                     *_PAGE_HOSTS[self._allowed_platform])
        _public_https_url(url, suffixes=permitted)
        response = super().urlopen(req)
        try:
            _public_https_url(response.url, suffixes=permitted, resolve_dns=False)
        except LinkResolutionError:
            response.close()
            raise
        return response


class LinkResolutionError(ValueError):
    pass


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == suffix or host.endswith('.' + suffix) for suffix in suffixes)


def _public_https_url(url: str, *, hosts: frozenset[str] | None = None,
                      suffixes: tuple[str, ...] | None = None,
                      resolve_dns: bool = True) -> str:
    try:
        parts = urllib.parse.urlsplit(url)
        hostname = (parts.hostname or '').lower().rstrip('.')
        port = parts.port
    except ValueError as exc:
        raise LinkResolutionError('链接格式无效') from exc
    if (parts.scheme != 'https' or not hostname or parts.username or parts.password
            or port not in (None, 443) or parts.fragment):
        raise LinkResolutionError('只支持公开 HTTPS 视频链接')
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise LinkResolutionError('不支持 IP 地址或局域网链接')
    if hosts is not None and hostname not in hosts:
        raise LinkResolutionError('目前只支持抖音和小红书的公开分享链接')
    if suffixes is not None and not _host_matches(hostname, suffixes):
        raise LinkResolutionError('视频流地址不属于受支持的平台')
    if resolve_dns:
        try:
            addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise LinkResolutionError('链接域名无法解析') from exc
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global
                                for item in addresses):
            raise LinkResolutionError('链接指向非公网地址，已拒绝访问')
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path,
                                    parts.query, ''))


def parse_public_link(text: str) -> tuple[str, str]:
    match = _LINK_RE.search(text or '')
    if not match:
        raise LinkResolutionError('请粘贴公开的抖音或小红书 HTTPS 视频链接')
    url = match.group(0).rstrip('.,;!?)）。，；！')
    host = (urllib.parse.urlsplit(url).hostname or '').lower().rstrip('.')
    platform = next((name for name, hosts in _PAGE_HOSTS.items() if host in hosts), None)
    if platform is None:
        raise LinkResolutionError('目前只支持抖音和小红书的公开分享链接')
    return _public_https_url(url, hosts=_PAGE_HOSTS[platform]), platform


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener():
    # Never inherit system proxies: proxy redirects can defeat local DNS/host
    # checks and turn this desktop feature into an intranet fetcher.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _open_bounded(url: str, platform: str, *, headers: dict | None = None,
                  method: str = 'GET', media: bool = False):
    allowed = _MEDIA_SUFFIXES[platform] if media else None
    current = url
    opener = _opener()
    for _ in range(5):
        if allowed is None:
            _public_https_url(current, hosts=_PAGE_HOSTS[platform])
        else:
            _public_https_url(current, suffixes=allowed)
        request = urllib.request.Request(current, headers=headers or {
            'User-Agent': 'Mozilla/5.0',
        }, method=method)
        try:
            response = opener.open(request, timeout=15)
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                exc.close()
                raise LinkResolutionError(f'平台返回 HTTP {exc.code}') from exc
            location = exc.headers.get('Location')
            exc.close()
            if not location:
                raise LinkResolutionError('平台跳转缺少地址')
            current = urllib.parse.urljoin(current, location)
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LinkResolutionError('平台连接失败，请稍后重试') from exc
        return response, current
    raise LinkResolutionError('链接跳转次数过多')


def canonical_page_url(url: str, platform: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or '').lower()
    if host in _SHORT_HOSTS:
        url = _resolve_short_page(url, platform)
    parts = urllib.parse.urlsplit(url)
    if platform == 'douyin':
        share_id = re.fullmatch(r'/share/video/(\d+)/?', parts.path)
        if parts.hostname in ('www.iesdouyin.com', 'iesdouyin.com') and share_id:
            url = f'https://www.douyin.com/video/{share_id.group(1)}'
            parts = urllib.parse.urlsplit(url)
        if parts.hostname == 'douyin.com':
            url = urllib.parse.urlunsplit((parts.scheme, 'www.douyin.com', parts.path,
                                           parts.query, ''))
        if not re.fullmatch(r'/video/\d+/?', urllib.parse.urlsplit(url).path):
            raise LinkResolutionError('该抖音链接无法解析为公开单条视频')
    else:
        if parts.hostname == 'xiaohongshu.com':
            url = urllib.parse.urlunsplit((parts.scheme, 'www.xiaohongshu.com', parts.path,
                                           parts.query, ''))
        if not re.fullmatch(r'/(?:explore|discovery/item)/[0-9a-fA-F]+/?',
                            urllib.parse.urlsplit(url).path):
            raise LinkResolutionError('该小红书链接无法解析为公开单条视频')
    return _public_https_url(url, hosts=_PAGE_HOSTS[platform])


def _resolve_short_page(url: str, platform: str) -> str:
    """Follow only official short-link redirects, without fetching the video page."""
    opener = _opener()
    current = url
    for _ in range(5):
        _public_https_url(current, hosts=_PAGE_HOSTS[platform])
        path = urllib.parse.urlsplit(current).path
        if platform == 'douyin' and re.fullmatch(r'/(?:share/)?video/\d+/?', path):
            return current
        if platform == 'xiaohongshu' and re.fullmatch(
                r'/(?:explore|discovery/item)/[0-9a-fA-F]+/?', path):
            return current
        for method in ('HEAD', 'GET'):
            request = urllib.request.Request(
                current, headers={'User-Agent': 'Mozilla/5.0'}, method=method)
            try:
                response = opener.open(request, timeout=15)
            except urllib.error.HTTPError as exc:
                code = exc.code
                location = exc.headers.get('Location')
                exc.close()
                if code in (403, 405) and method == 'HEAD':
                    continue
                if code not in (301, 302, 303, 307, 308) or not location:
                    raise LinkResolutionError(f'平台短链返回 HTTP {code}') from exc
                current = urllib.parse.urljoin(current, location)
                break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise LinkResolutionError('平台连接失败，请稍后重试') from exc
            else:
                response.close()
                raise LinkResolutionError('短链未跳转到公开单条视频')
        else:
            raise LinkResolutionError('短链未跳转到公开单条视频')
    raise LinkResolutionError('短链跳转次数过多')


def _extract_info(url: str, platform: str) -> dict:
    # Only the two audited upstream extractors are enabled. yt-dlp's Generic
    # extractor would otherwise accept arbitrary redirects and file types.
    extractor = '^Douyin$' if platform == 'douyin' else '^XiaoHongShu$'
    settings = {
        'quiet': True, 'no_warnings': True, 'skip_download': True,
        'noplaylist': True, 'allowed_extractors': [extractor],
        'socket_timeout': 8, 'retries': 0, 'extractor_retries': 0,
        'fragment_retries': 0, 'proxy': '', 'cachedir': False,
        'geo_bypass': False, 'ignoreerrors': False,
    }
    try:
        with _RestrictedYoutubeDL(platform, settings) as downloader:
            info = downloader.extract_info(url, download=False)
    except DownloadError as exc:
        if 'fresh cookies' in str(exc).lower():
            raise LinkResolutionError(
                '抖音当前公开接口要求新的访客 Cookie；本软件不会读取浏览器凭据。'
                '可改用本地视频去水印功能。') from exc
        raise LinkResolutionError('平台解析失败：视频可能需要登录、已下架，或平台接口发生变化') from exc
    if not isinstance(info, dict) or info.get('_type') in ('playlist', 'url'):
        raise LinkResolutionError('只支持公开的单条视频')
    return info


def resolve_public_video(text: str) -> dict:
    page_url, platform = parse_public_link(text)
    page_url = canonical_page_url(page_url, platform)
    info = _extract_info(page_url, platform)
    formats = []
    for candidate in info.get('formats') or [info]:
        media_url = candidate.get('url')
        if not isinstance(media_url, str):
            continue
        try:
            _public_https_url(media_url, suffixes=_MEDIA_SUFFIXES[platform], resolve_dns=False)
        except LinkResolutionError:
            continue
        if ((candidate.get('ext') or 'mp4').lower() != 'mp4'
                or candidate.get('vcodec') == 'none'
                or (candidate.get('protocol') or 'https') not in ('https', 'http')):
            continue
        note = str(candidate.get('format_note') or '').lower()
        if 'watermarked' in note or '水印' in note or 'unplayable' in note:
            continue
        size = candidate.get('filesize') or candidate.get('filesize_approx')
        if isinstance(size, (int, float)) and size > MAX_MEDIA_BYTES:
            continue
        status = 'original' if platform == 'xiaohongshu' and candidate.get('format_id') == 'direct' else 'unverified'
        formats.append({
            'id': f'f{len(formats)}',
            'label': ('原始视频' if status == 'original' else '视频流')
                     + (f" · {candidate['height']}p" if candidate.get('height') else ''),
            'width': candidate.get('width'), 'height': candidate.get('height'),
            'ext': 'mp4', 'filesize': size if isinstance(size, (int, float)) else None,
            'watermark_status': status, 'url': media_url,
            'http_headers': candidate.get('http_headers') or info.get('http_headers') or {},
            'acodec': candidate.get('acodec'),
        })
    if not formats:
        raise LinkResolutionError('没有找到可直接下载的视频流；可能仅有带水印、受限或分段视频')
    # Prefer a platform's original stream; otherwise choose the highest usable
    # progressive format without asserting that pixels contain no watermark.
    formats.sort(key=lambda item: (item['watermark_status'] == 'original',
                                   item['acodec'] != 'none', item['height'] or 0,
                                   item['filesize'] or 0), reverse=True)
    for index, item in enumerate(formats):
        item['id'] = f'f{index}'
    thumbnail = info.get('thumbnail')
    if thumbnail:
        try:
            _public_https_url(thumbnail, suffixes=_MEDIA_SUFFIXES[platform], resolve_dns=False)
        except LinkResolutionError:
            thumbnail = None
    duration = info.get('duration')
    if isinstance(duration, (int, float)) and duration > MAX_MEDIA_SECONDS:
        raise LinkResolutionError('视频超过 15 分钟，请使用本地视频处理功能')
    return {
        'platform': '抖音' if platform == 'douyin' else '小红书',
        'platform_key': platform,
        'title': str(info.get('title') or '未命名视频')[:120],
        'thumbnail_url': thumbnail,
        'webpage_url': page_url,
        'video_url': formats[0]['url'],
        'watermark_status': formats[0]['watermark_status'],
        'warning': ('平台提供的原始视频流；仍请预览核对画面是否有水印。'
                    if formats[0]['watermark_status'] == 'original' else
                    '无法保证视频流不含画面水印；请先预览核对。链接可能随时失效。'),
        'formats': formats,
        'source_id': re.sub(r'[^0-9A-Za-z_-]', '', str(info.get('id') or 'video'))[:64],
    }


def _safe_headers(raw: dict) -> dict:
    allowed = {'user-agent', 'referer', 'origin', 'accept'}
    return {str(key): str(value) for key, value in raw.items()
            if str(key).lower() in allowed and isinstance(value, str)
            and '\r' not in value and '\n' not in value}


def download_direct_video(format_info: dict, platform: str, temp_path: Path,
                          is_cancelled: Callable[[], bool],
                          on_progress: Callable[[float], None]) -> None:
    url = format_info['url']
    headers = _safe_headers(format_info.get('http_headers') or {})
    headers.setdefault('User-Agent', 'Mozilla/5.0')
    response = None
    started = time.monotonic()
    total_read = 0
    try:
        response, _ = _open_bounded(url, platform, headers=headers, media=True)
        content_type = (response.headers.get('Content-Type') or '').lower()
        if content_type and not (content_type.startswith('video/') or
                                 'application/octet-stream' in content_type):
            raise LinkResolutionError('平台返回的不是视频文件')
        expected = int(response.headers.get('Content-Length') or 0)
        if expected > MAX_MEDIA_BYTES:
            raise LinkResolutionError('视频超过 512 MB 限制')
        with temp_path.open('xb') as stream:
            while True:
                if is_cancelled():
                    raise InterruptedError('已停止')
                if time.monotonic() - started > MAX_DOWNLOAD_SECONDS:
                    raise LinkResolutionError('下载超时，请稍后重试')
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                total_read += len(chunk)
                if total_read > MAX_MEDIA_BYTES:
                    raise LinkResolutionError('视频超过 512 MB 限制')
                stream.write(chunk)
                if expected:
                    on_progress(min(0.98, total_read / expected))
                else:
                    on_progress(min(0.90, total_read / (64 * 1024 * 1024)))
        if total_read < 1024:
            raise LinkResolutionError('视频文件为空或内容不完整')
        if expected and total_read != expected:
            raise LinkResolutionError('视频文件未下载完整，请重试')
        on_progress(1.0)
    finally:
        if response is not None:
            response.close()


def download_cover_image(url: str, platform: str, destination_stem: Path,
                         is_cancelled: Callable[[], bool]) -> Path:
    """Save a public platform thumbnail only after its image signature agrees."""
    response = None
    temporary = destination_stem.with_name(f'.{destination_stem.name}.cover-download')
    try:
        response, _ = _open_bounded(url, platform,
                                    headers={'User-Agent': 'Mozilla/5.0'}, media=True)
        content_type = (response.headers.get('Content-Type') or '').lower()
        if content_type and not (content_type.startswith('image/') or
                                 'application/octet-stream' in content_type):
            raise LinkResolutionError('封面地址未返回图片')
        maximum = 10 * 1024 * 1024
        expected = int(response.headers.get('Content-Length') or 0)
        if expected > maximum:
            raise LinkResolutionError('封面图片超过 10 MB')
        total = 0
        with temporary.open('xb') as stream:
            while True:
                if is_cancelled():
                    raise InterruptedError('已停止')
                chunk = response.read(128 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise LinkResolutionError('封面图片超过 10 MB')
                stream.write(chunk)
        if expected and total != expected:
            raise LinkResolutionError('封面图片未下载完整')
        with temporary.open('rb') as stream:
            magic = stream.read(12)
        if magic.startswith(b'\xff\xd8\xff'):
            suffix = '.jpg'
        elif magic.startswith(b'\x89PNG\r\n\x1a\n'):
            suffix = '.png'
        elif magic.startswith(b'RIFF') and magic[8:12] == b'WEBP':
            suffix = '.webp'
        else:
            raise LinkResolutionError('封面图片格式不受支持')
        destination = destination_stem.with_suffix(suffix)
        temporary.replace(destination)
        return destination
    finally:
        if response is not None:
            response.close()
        temporary.unlink(missing_ok=True)


def remux_to_mp4(source: Path, destination: Path,
                 is_cancelled: Callable[[], bool],
                 on_progress: Callable[[float], None]) -> None:
    import subprocess

    try:
        probe = subprocess.run(
            [FFPROBE, '-v', 'error', '-print_format', 'json',
             '-show_format', '-show_streams', str(source)],
            capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if __import__('os').name == 'nt' else 0,
        )
        media = json.loads(probe.stdout) if probe.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        media = {}
    if not any(stream.get('codec_type') == 'video' for stream in media.get('streams', [])):
        raise LinkResolutionError('下载内容不是可识别的视频')
    try:
        duration = float(media.get('format', {}).get('duration') or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0 or duration > MAX_MEDIA_SECONDS:
        raise LinkResolutionError('视频时长无效或超过 15 分钟')
    command = [FFMPEG, '-hide_banner', '-loglevel', 'error', '-y',
               '-i', str(source), '-map', '0:v:0', '-map', '0:a:0?',
               '-c', 'copy', '-movflags', '+faststart', '-f', 'mp4', str(destination)]
    okay, error = run_process(command, is_cancelled, timeout=60,
                              progress_callback=on_progress,
                              progress_duration=duration)
    if not okay:
        raise InterruptedError('已停止') if is_cancelled() else LinkResolutionError(
            '视频封装失败；该平台流可能使用了不兼容的编码')
    if not destination.is_file() or destination.stat().st_size < 1024:
        raise LinkResolutionError('视频输出校验失败')
    on_progress(1.0)
