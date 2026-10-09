import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.api.subtitles import export_srt, render_srt
from app.core.speech_transcript import SpeechTranscriptService, parse_srt


class SubtitleExportTests(unittest.TestCase):
    def test_transcribed_video_creates_editable_srt_next_to_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "成片.mp4"
            video.write_bytes(b"video")
            service = SpeechTranscriptService(cache_dir=root / "cache", reference_dir=root / "none")
            spoken = [
                {"start": 0.125, "end": 1.375, "text": "第一句。"},
                {"start": 1.5, "end": 3.0, "text": "第二句。"},
            ]
            with patch.object(service, "_transcribe_audio", return_value=spoken) as transcribe:
                result = export_srt(video, transcripts=service)
            transcribe.assert_called_once_with(video.resolve())
            subtitle = video.with_suffix(".srt")
            self.assertEqual(result.status, "created")
            self.assertEqual(result.srt_path, str(subtitle.resolve()))
            self.assertEqual(result.folder, str(root.resolve()))
            self.assertEqual(result.cue_count, 2)
            self.assertEqual(parse_srt(subtitle.read_text(encoding="utf-8-sig")), spoken)
            self.assertIn("00:00:00,125 --> 00:00:01,375", subtitle.read_text(encoding="utf-8-sig"))

    def test_existing_hand_edited_srt_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "clip.mp4"
            video.write_bytes(b"video")
            subtitle = video.with_suffix(".srt")
            subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\n人工修订。\n", encoding="utf-8-sig")
            before = subtitle.read_bytes()
            service = SpeechTranscriptService(cache_dir=root / "cache", reference_dir=root / "none")
            with patch.object(service, "_transcribe_audio") as transcribe:
                result = export_srt(video, transcripts=service)
            transcribe.assert_not_called()
            self.assertEqual(result.status, "existing")
            self.assertEqual(result.cue_count, 1)
            self.assertEqual(subtitle.read_bytes(), before)

    def test_silent_video_never_creates_blank_srt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "silent.mp4"
            video.write_bytes(b"video")
            service = SpeechTranscriptService(cache_dir=root / "cache", reference_dir=root / "none")
            with patch.object(service, "_transcribe_audio", return_value=[]):
                with self.assertRaisesRegex(ValueError, "未识别到"):
                    export_srt(video, transcripts=service)
            self.assertFalse(video.with_suffix(".srt").exists())

    def test_invalid_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "notes.txt"
            source.write_text("not a video", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "视频文件"):
                export_srt(source)
            with self.assertRaises(FileNotFoundError):
                export_srt(root / "missing.mp4")

    def test_srt_time_rolls_over_to_next_hour(self):
        self.assertEqual(
            render_srt([{"start": 3599.999, "end": 3600.001, "text": "跨小时。"}]),
            "1\n00:59:59,999 --> 01:00:00,001\n跨小时。\n",
        )


if __name__ == "__main__":
    unittest.main()
