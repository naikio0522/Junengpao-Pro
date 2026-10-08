"""End-to-end checks for the independent existing-video variant flow."""
import hashlib
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from app.api.standalone_variants import StandaloneVariantRequest, StandaloneVariantService
from app.core.ffmpeg import FFMPEG, FFPROBE, probe_media


@unittest.skipUnless(shutil.which(FFMPEG) and shutil.which(FFPROBE), "FFmpeg is unavailable")
class StandaloneVariantTests(unittest.TestCase):
    def test_folder_input_keeps_source_and_ignores_previous_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "素材"
            source_dir.mkdir()
            source = source_dir / "原视频.mp4"
            subprocess.run([
                FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=12:duration=1.2",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1.2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-shortest", str(source),
            ], check=True, timeout=20)
            original_hash = hashlib.sha256(source.read_bytes()).digest()
            output_dir = source_dir / "去重变换输出"
            output_dir.mkdir()
            shutil.copy2(source, output_dir / "上次生成.mp4")

            service = StandaloneVariantService()
            job = service.create(StandaloneVariantRequest(
                input_paths=[str(source_dir)], output_dir=str(output_dir),
                strength="mild", copies_per_video=2, use_gpu=False,
            ))
            self.assertEqual(job["input_count"], 1)
            deadline = time.monotonic() + 45
            while job["status"] in ("pending", "running") and time.monotonic() < deadline:
                time.sleep(0.2)
                job = service.get(job["task_id"])
            self.assertEqual(job["status"], "completed", job)
            self.assertEqual(len(job["output_files"]), 2)
            self.assertNotEqual(
                hashlib.sha256(Path(job["output_files"][0]).read_bytes()).digest(),
                hashlib.sha256(Path(job["output_files"][1]).read_bytes()).digest(),
            )
            output = Path(job["output_files"][0])
            self.assertNotEqual(source, output)
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), original_hash)
            info = probe_media(str(output))
            self.assertTrue(info)
            self.assertGreater(float(info["format"]["duration"]), 1.0)
            self.assertTrue(any(s["codec_type"] == "audio" for s in info["streams"]))
            self.assertEqual(job["progress"], 100)
            self.assertEqual(job["file_progress"], 100)
            detail = "\n".join(job["log_lines"])
            for stage in ("读取源视频", "复制原视频", "画面·整段视频", "色彩·整段视频",
                          "纹理·整段视频", "原声·整段视频", "正在校验", "完成"):
                self.assertIn(stage, detail)
            self.assertIn("完成", job["phase_detail"])


if __name__ == "__main__":
    unittest.main()
