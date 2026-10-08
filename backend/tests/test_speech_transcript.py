import csv
import errno
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from app.core.speech_transcript import SpeechTranscriptService, parse_srt, parse_timed_text


class SpeechTranscriptTests(unittest.TestCase):
    def test_srt_sidecar_has_priority_and_groups_adjacent_phrases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "clip.mp4"
            video.write_bytes(b"video")
            video.with_suffix(".txt").write_text("0.0–5.0 文本版本。", encoding="utf-8")
            subtitle = video.with_suffix(".srt")
            subtitle.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n牙齿敏感\n\n"
                "2\n00:00:01,100 --> 00:00:02,000\n怎么办？\n\n"
                "3\n00:00:03,000 --> 00:00:04,000\n看看用法。\n",
                encoding="utf-8-sig",
            )
            service = SpeechTranscriptService(cache_dir=root / "cache", reference_dir=root / "none")
            self.assertEqual(service.get_segments(video), [
                {"start": 0.0, "end": 2.0, "text": "牙齿敏感 怎么办？"},
                {"start": 3.0, "end": 4.0, "text": "看看用法。"},
            ])
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\n已修改。\n", encoding="utf-8")
            self.assertEqual(service.get_segments(video)[0]["text"], "已修改。")

    def test_timed_txt_and_untimed_sidecar(self):
        self.assertEqual(parse_timed_text("0.0–1.0 你好\n1.0–2.0 世界。"), [
            {"start": 0.0, "end": 1.0, "text": "你好"},
            {"start": 1.0, "end": 2.0, "text": "世界。"},
        ])
        self.assertEqual(parse_srt("1\n00:00:00.100 --> 00:00:01.200\n字幕\n"), [
            {"start": 0.1, "end": 1.2, "text": "字幕"},
        ])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "clip.mp4"
            video.write_bytes(b"video")
            sidecar = video.with_suffix(".txt")
            sidecar.write_text("0.0–1.0 你好\n1.0–2.0 世界。", encoding="utf-8")
            service = SpeechTranscriptService(cache_dir=root / "cache", reference_dir=root / "none")
            self.assertEqual(service.get_segments(video), [
                {"start": 0.0, "end": 2.0, "text": "你好 世界。"},
            ])
            sidecar.write_text("只有文字，没有时间码", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "缺少有效时间码"):
                service.get_segments(video)

    def test_old_library_requires_exact_name_id_and_source_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            videos = root / "source"
            videos.mkdir()
            reference = root / "reference-study" / "transcripts-raw-library"
            reference.mkdir(parents=True)
            video = videos / "原片123_真实口播.mp4"
            video.write_bytes(b"original")
            similar = videos / "原片123_另一个文件.mp4"
            similar.write_bytes(b"different")
            (reference / "123.txt").write_text("0.0–2.0 正确的旧转录。", encoding="utf-8")
            with (reference.parent / "raw-library-metadata.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["ID", "Name"])
                writer.writeheader()
                writer.writerow({"ID": "123", "Name": video.name})
            manifest = root / "source-inventory.json"
            stat = video.stat()
            manifest.write_text(json.dumps({"records": [{
                "source_group": "raw", "path": str(video.resolve()),
                "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            }]}), encoding="utf-8")
            service = SpeechTranscriptService(
                cache_dir=root / "cache", reference_dir=reference,
                reference_video_dir=videos, reference_manifest=manifest,
            )
            self.assertEqual(service.get_segments(video)[0]["text"], "正确的旧转录。")
            self.assertEqual(service.get_segments(similar), [])
            outside = root / video.name
            outside.write_bytes(b"original")
            self.assertEqual(service.get_segments(outside), [])
            video.write_bytes(b"changed content")
            self.assertEqual(service.get_segments(video), [])

    def test_bundled_index_is_path_free_and_survives_relocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "speech_transcripts"
            reference.mkdir()
            video = root / "moved" / "原片123_真实口播.mp4"
            video.parent.mkdir()
            video.write_bytes(b"original")
            stat = video.stat()
            (reference / "123.txt").write_text("0.0–2.0 打包的口播。", encoding="utf-8")
            (reference / "index.json").write_text(json.dumps({
                "version": 1,
                "records": [{
                    "id": "123", "name": video.name,
                    "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                }],
            }, ensure_ascii=False), encoding="utf-8")
            service = SpeechTranscriptService(cache_dir=root / "cache", reference_dir=reference)
            self.assertEqual(service.get_segments(video)[0]["text"], "打包的口播。")
            self.assertNotIn(str(root), (reference / "index.json").read_text(encoding="utf-8"))
            stat = video.stat()
            os.utime(video, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
            self.assertEqual(service.get_segments(video), [])

    def test_offline_asr_cache_invalidates_on_mtime_and_size(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "clip.mp4"
            video.write_bytes(b"first")
            options = {"cache_dir": root / "cache", "reference_dir": root / "none"}
            service = SpeechTranscriptService(**options)
            self.assertEqual(service.get_segments(video), [])
            spoken = [{"start": 0.0, "end": 2.0, "text": "第一句话。"}]
            with patch.object(service, "_transcribe_audio", return_value=spoken) as transcribe:
                self.assertEqual(service.get_segments(video, allow_asr=True), spoken)
                transcribe.assert_called_once()
            second_service = SpeechTranscriptService(**options)
            with patch.object(second_service, "_transcribe_audio") as transcribe:
                self.assertEqual(second_service.get_segments(video), spoken)
                transcribe.assert_not_called()

            stat = video.stat()
            os.utime(video, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
            self.assertEqual(second_service.get_segments(video), [])
            with patch.object(second_service, "_transcribe_audio", return_value=spoken) as transcribe:
                self.assertEqual(second_service.get_segments(video, allow_asr=True), spoken)
                transcribe.assert_called_once()
            video.write_bytes(b"longer content")
            self.assertEqual(second_service.get_segments(video), [])

    def test_cache_write_falls_back_when_windows_rename_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "clip.mp4"
            video.write_bytes(b"video")
            service = SpeechTranscriptService(cache_dir=root / "cache", reference_dir=root / "none")
            spoken = [{"start": 0.0, "end": 1.0, "text": "离线口播。"}]
            with patch.object(service, "_transcribe_audio", return_value=spoken):
                with patch("app.core.speech_transcript.os.replace", side_effect=OSError(errno.EXDEV, "cross-device")):
                    self.assertEqual(service.get_cues(video, allow_asr=True), spoken)
            self.assertEqual(service.get_cues(video), spoken)

    @unittest.skipUnless(
        os.environ.get("VIDEOMATRIX_TEST_SPEECH_SOURCE") and (
            os.environ.get("VIDEOMATRIX_TEST_BINARY") or os.environ.get("VIDEOMATRIX_TEST_SOURCE_BACKEND")
        ),
        "requires a backend and a local speech sample",
    )
    def test_bundled_backend_reuses_old_transcript_and_transcribes_new_media(self):
        source = Path(os.environ["VIDEOMATRIX_TEST_SPEECH_SOURCE"])
        source_backend = bool(os.environ.get("VIDEOMATRIX_TEST_SOURCE_BACKEND"))
        no_instance = bool(os.environ.get("VIDEOMATRIX_TEST_NO_INSTANCE"))
        binary = Path(os.environ["VIDEOMATRIX_TEST_BINARY"]) if not source_backend else None
        with tempfile.TemporaryDirectory(prefix="vm-speech-smoke-") as directory:
            root = Path(directory)
            old_hook_dir, old_body_dir, new_dir = root / "old_hook", root / "old_body", root / "new"
            old_hook_dir.mkdir()
            old_body_dir.mkdir()
            new_dir.mkdir()
            # Hook and Body must be different physical videos. Preserve the
            # reference-library name, size and mtime in both copies so neither
            # needs ASR during the old-transcript phase.
            shutil.copy2(source, old_hook_dir / source.name)
            shutil.copy2(source, old_body_dir / source.name)
            shutil.copy2(source, new_dir / "新素材修护口播.mp4")
            env = {
                **os.environ, "LOCALAPPDATA": str(root / "localappdata"),
                "APPDATA": str(root / "appdata"),
                "HF_HOME": str(root / "hf_home"), "HF_HUB_OFFLINE": "1",
                "VIDEOMATRIX_INSTANCE": "speech-smoke", "PYTHONIOENCODING": "utf-8",
            }
            if no_instance:
                env.pop("VIDEOMATRIX_INSTANCE", None)
                with socket.socket() as port_picker:
                    port_picker.bind(("127.0.0.1", 0))
                    requested_port = port_picker.getsockname()[1]
            else:
                requested_port = 0
            process = subprocess.Popen(
                ([sys.executable, "run_backend.py"] if source_backend else [str(binary)])
                + ["--host", "127.0.0.1", "--port", str(requested_port)],
                cwd=Path(__file__).resolve().parents[1], env=env,
                stdin=subprocess.DEVNULL if no_instance else subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8",
            )
            lines = queue.Queue()
            errors = queue.Queue()
            threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True).start()
            threading.Thread(target=lambda: [errors.put(line) for line in process.stderr], daemon=True).start()
            try:
                if no_instance:
                    port = requested_port
                else:
                    ready = lines.get(timeout=90)
                    self.assertTrue(ready.startswith("VIDEOMATRIX_READY "), ready)
                    port = json.loads(ready.split(" ", 1)[1])["port"]
                base = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 15
                while True:
                    try:
                        with urlopen(f"{base}/api/health", timeout=1) as response:
                            self.assertEqual(json.load(response)["status"], "ok")
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(0.1)

                def preflight(hook_folder: Path, body_folder: Path) -> dict:
                    config = {
                        "hook_dir": str(hook_folder), "body_dirs": [str(body_folder)],
                        "selection_mode": "speech_logic", "semantic_sku": "99",
                        "semantic_topic": "牙齿敏感", "total_clips": 3,
                        "target_count": 1, "enable_gpu": False,
                    }
                    request = Request(
                        f"{base}/api/preflight",
                        data=json.dumps(config, ensure_ascii=False).encode("utf-8"),
                        headers={"Content-Type": "application/json; charset=utf-8"},
                        method="POST",
                    )
                    with urlopen(request, timeout=120) as response:
                        return json.load(response)

                cache_dir = root / "localappdata" / "VideoMatrix" / "speech_transcripts"
                if not source_backend:
                    old_report = preflight(old_hook_dir, old_body_dir)
                    self.assertEqual(list(cache_dir.glob("*.json")), [])
                    self.assertNotIn("转录或时间轴不可用", old_report["report"][0]["message"])
                    self.assertNotIn("没有可用的完整口播句段", old_report["report"][0]["message"])
                    if os.environ.get("VIDEOMATRIX_TEST_SPEECH_SKIP_ASR"):
                        return

                try:
                    new_report = preflight(old_hook_dir, new_dir)
                except (TimeoutError, HTTPError) as exc:
                    time.sleep(0.3)
                    recent_errors = list(errors.queue)[-30:]
                    redacted_errors = [line.replace(str(root), "[test-temp]").strip() for line in recent_errors]
                    recent_output = [line.replace(str(root), "[test-temp]").strip() for line in list(lines.queue)[-10:]]
                    self.fail(f"ASR request failed ({type(exc).__name__}); backend logs: {redacted_errors}; stdout: {recent_output}; running={process.poll() is None}")
                caches = list(cache_dir.glob("*.json"))
                self.assertEqual(len(caches), 1, new_report)
                record = json.loads(caches[0].read_text(encoding="utf-8"))
                self.assertEqual(record["model"], "base")
                self.assertGreater(len(record["segments"]), 0)
                if not no_instance:
                    process.stdin.close()
                    self.assertEqual(process.wait(timeout=15), 0)
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


if __name__ == "__main__":
    unittest.main()
