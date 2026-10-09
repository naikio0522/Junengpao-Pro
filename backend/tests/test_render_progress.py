"""Progress comes from FFmpeg encoded timestamps, not a UI timer."""

import unittest

from app.core.ffmpeg import FFMPEG, run_process


class RenderProgressTests(unittest.TestCase):
    def test_ffmpeg_reports_monotonic_real_progress(self):
        samples = []
        success, error = run_process(
            [FFMPEG, '-y', '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=15',
             '-t', '1.5', '-an', '-f', 'null', '-'],
            progress_callback=samples.append, progress_duration=1.5,
            timeout=30,
        )
        self.assertTrue(success, error)
        self.assertTrue(samples, 'FFmpeg emitted no encoded-timestamp progress')
        self.assertEqual(samples, sorted(samples))
        self.assertTrue(all(0 < value <= 0.98 for value in samples))


if __name__ == '__main__':
    unittest.main()
