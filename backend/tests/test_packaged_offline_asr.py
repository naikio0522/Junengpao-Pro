"""Exercise bundled ASR through the packaged HTTP backend, without a model cache.

The generated audio is a sine wave, so the test verifies model loading and
cache writes, not transcription quality or non-empty speech recognition.
"""

import json
import os
import queue
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import Request, urlopen


BACKEND_DIR = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("VIDEOMATRIX_TEST_BINARY"), "requires packaged backend")
class PackagedOfflineAsrTests(unittest.TestCase):
    def test_preflight_transcribes_two_uncached_videos_without_network(self):
        binary = Path(os.environ["VIDEOMATRIX_TEST_BINARY"]).resolve(strict=True)
        ffmpeg = BACKEND_DIR / "ffmpeg" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        self.assertTrue(ffmpeg.is_file(), f"test media generator missing: {ffmpeg}")

        with tempfile.TemporaryDirectory(prefix="vm-packaged-offline-asr-") as temporary:
            root = Path(temporary)
            hook_dir, body_dir = root / "hook", root / "body"
            hook_dir.mkdir()
            body_dir.mkdir()
            videos = [hook_dir / "hook.mp4", body_dir / "body.mp4"]
            for video, color, tone in zip(videos, ("blue", "red"), (440, 660)):
                result = subprocess.run(
                    [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
                     "-f", "lavfi", "-i", f"color=c={color}:s=64x64:r=12",
                     "-f", "lavfi", "-i", f"sine=frequency={tone}:sample_rate=16000",
                     "-t", "2", "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac",
                     "-pix_fmt", "yuv420p", "-shortest", "-y", str(video)],
                    cwd=BACKEND_DIR, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertGreater(video.stat().st_size, 0)
                self.assertFalse(video.with_suffix(".srt").exists())

            localappdata, hf_home = root / "localappdata", root / "hf_home"
            localappdata.mkdir()
            hf_home.mkdir()
            environment = os.environ.copy()
            for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE", "XDG_CACHE_HOME"):
                environment.pop(name, None)
            environment.update({
                "LOCALAPPDATA": str(localappdata), "APPDATA": str(root / "appdata"),
                "HF_HOME": str(hf_home), "HF_HUB_OFFLINE": "1",
                "VIDEOMATRIX_INSTANCE": "packaged-offline-asr-smoke",
                "PYTHONIOENCODING": "utf-8",
            })
            process = subprocess.Popen(
                [str(binary), "--host", "127.0.0.1", "--port", "0"],
                cwd=BACKEND_DIR, env=environment, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
            )
            output_lines, error_lines = [], []
            ready_lines = queue.Queue()

            def drain(stream, collected, ready_queue=None):
                for line in stream:
                    collected.append(line.rstrip())
                    if ready_queue is not None:
                        ready_queue.put(line)

            stdout_thread = threading.Thread(
                target=drain, args=(process.stdout, output_lines, ready_lines), daemon=True)
            stderr_thread = threading.Thread(
                target=drain, args=(process.stderr, error_lines), daemon=True)
            stdout_thread.start()
            stderr_thread.start()
            try:
                deadline = time.monotonic() + 120
                while True:
                    remaining = deadline - time.monotonic()
                    self.assertGreater(remaining, 0, f"backend did not start: {output_lines[-15:]} {error_lines[-15:]}")
                    try:
                        line = ready_lines.get(timeout=min(1, remaining))
                    except queue.Empty:
                        self.assertIsNone(process.poll(), f"backend exited: {output_lines[-15:]} {error_lines[-15:]}")
                        continue
                    if line.startswith("VIDEOMATRIX_READY "):
                        port = json.loads(line.split(" ", 1)[1])["port"]
                        break

                request = Request(
                    f"http://127.0.0.1:{port}/api/preflight",
                    data=json.dumps({
                        "hook_dir": str(hook_dir), "body_dirs": [str(body_dir)],
                        "selection_mode": "speech_logic", "duration_mode": "clips",
                        "total_clips": 2, "target_count": 1, "enable_gpu": False,
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    method="POST",
                )
                with urlopen(request, timeout=180) as response:
                    result = json.load(response)
                cache_dir = localappdata / "VideoMatrix" / "speech_transcripts"
                records = [json.loads(path.read_text(encoding="utf-8"))
                           for path in cache_dir.glob("*.json")]
                self.assertEqual(len(records), 2, f"preflight={result}; stderr={error_lines[-20:]}")
                self.assertEqual({item["video"]["path"] for item in records},
                                 {str(video.resolve()) for video in videos})
                self.assertTrue(all(item["model"] == "base" for item in records))
                # Silence or sine waves can legitimately yield no recognized text.
                self.assertTrue(all(isinstance(item["segments"], list) for item in records))
                combined = json.dumps(result, ensure_ascii=False) + "\n" + "\n".join(output_lines + error_lines)
                self.assertNotIn("未找到本地 faster-whisper base 模型", combined)
                self.assertNotIn("离线转写需要安装 faster-whisper", combined)
                self.assertEqual(list(hf_home.rglob("*.bin")), [], "test unexpectedly populated the online model cache")
            finally:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()
                stdout_thread.join(timeout=2)
                stderr_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
