import sqlite3
import subprocess
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from app.core.brand_watermark import apply_brand_watermark, brand_asset_path
from app.core.ffmpeg import FFMPEG, probe_media
from app.services.account_service import AccountService, SQLiteAccountRepository


class MembershipTests(unittest.TestCase):
    def test_verified_order_is_idempotent_and_membership_is_server_side(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteAccountRepository(Path(directory) / 'accounts.sqlite3')
            accounts = AccountService(repository)
            session = accounts.register('13800138010', 'test-password-123')
            user_id = session['user']['id']
            self.assertFalse(accounts.is_member(session['token']))
            paid_at = int(time.time())
            expires = repository.apply_verified_payment(user_id, 'paid-order-1', 30, paid_at)
            self.assertEqual(repository.apply_verified_payment(user_id, 'paid-order-1', 30, paid_at), expires)
            self.assertTrue(accounts.is_member(session['token']))
            self.assertEqual(repository.apply_verified_payment(user_id, 'paid-order-2', 30, paid_at), expires + 30 * 86400)
            with closing(sqlite3.connect(repository.path)) as connection:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM account_paid_orders').fetchone()[0], 2)


class BrandWatermarkTests(unittest.TestCase):
    def test_brand_mark_preserves_audio_and_dimensions(self):
        self.assertTrue(brand_asset_path().is_file())
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'video.mp4'
            result = subprocess.run([
                FFMPEG, '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'lavfi', '-i', 'color=c=gray:s=320x240:r=15:d=1.2',
                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.2',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(source),
            ], capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
            ok, error = apply_brand_watermark(str(source), {
                'enable_gpu': False, 'resolution': '320*240', 'fps': '15',
                'bitrate': '500k', 'concurrent_tasks': 1,
            })
            self.assertTrue(ok, error)
            media = probe_media(str(source))
            self.assertEqual([s['codec_type'] for s in media['streams']], ['video', 'audio'])
            self.assertEqual((media['streams'][0]['width'], media['streams'][0]['height']), (320, 240))
            self.assertFalse(list(source.parent.glob('.*.brand-*.mp4')))


if __name__ == '__main__':
    unittest.main()
