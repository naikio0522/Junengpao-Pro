"""End-to-end checks for original-audio, transcript-guided video selection."""

from array import array
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.core.ffmpeg import FFMPEG, probe_media
from app.core.video_matrix import SharedMediaCache, VideoMatrixCore
from app.models.schemas import VideoConfig
from app.services.task_service import TaskService


def run(*args):
    subprocess.run([FFMPEG, "-y", *args], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def frame_bytes(path: str, index: int) -> bytes:
    result = subprocess.run(
        [FFMPEG, "-v", "error", "-i", path, "-vf", f"select=eq(n\\,{index})",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout


def dominant_channel(frame: bytes) -> int:
    totals = [sum(frame[index::3]) for index in range(3)]
    return totals.index(max(totals))


def audio_tones(path: str, centers: tuple[float, ...]) -> list[float]:
    result = subprocess.run(
        [FFMPEG, "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "1",
         "-ar", "44100", "-f", "s16le", "-"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    samples = array("h")
    samples.frombytes(result.stdout)
    rates = []
    for center in centers:
        first = int((center - 0.1) * 44100)
        last = int((center + 0.1) * 44100)
        window = samples[first:last]
        crossings = sum((left < 0) != (right < 0)
                        for left, right in zip(window, window[1:]))
        rates.append(crossings / 0.4)
    return rates


def audio_levels(path: str, centers: tuple[float, ...]) -> list[float]:
    result = subprocess.run(
        [FFMPEG, "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "1",
         "-ar", "44100", "-f", "s16le", "-"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    samples = array("h")
    samples.frombytes(result.stdout)
    return [
        sum(abs(value) for value in samples[int((center - 0.1) * 44100):
                                               int((center + 0.1) * 44100)]) / 8820
        for center in centers
    ]


def source(folder: Path, name: str, color: str, frequency: int, speech: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / f"{name}.mp4"
    run(
        "-f", "lavfi", "-i", f"color=c={color}:s=64x64:r=24:d=2",
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=44100:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(video),
    )
    video.with_suffix(".srt").write_text(
        f"1\n00:00:00,200 --> 00:00:01,200\n{speech}\n\n",
        encoding="utf-8",
    )
    return video


def config(root: Path, *, selection_mode: str, body_dir: Path, **changes) -> dict:
    value = dict(
        task_name="speech-integration", selection_mode=selection_mode,
        semantic_sku="JXB-99", semantic_topic="牙齿敏感",
        hook_dir=str(root / "hook"), body_dirs=[str(body_dir)],
        body_mode="normal", duration_mode="clips",
        t_hook=0.5, t_body=0.5, total_clips=3, target_count=1,
        hook_r=1, body_r=0, bgm_r=0,
        bgm_dir="", voice_dir=None, watermark_path=None,
        resolution="64*64", fps=24, bitrate="300k", enable_gpu=False,
        vol_orig=100, vol_hook_orig=100, vol_bgm=0, vol_voice=0,
        enable_srt=False, enable_variants=False, enable_random_cover=False,
        apply_bgm_to_hook=True, apply_voice_to_hook=True,
        apply_srt_to_hook=True, apply_watermark_to_hook=True,
    )
    value.update(changes)
    value["out_dir"] = str(root / "out")
    return value


class SpeechLogicEngineTests(unittest.TestCase):
    def test_preflight_exposes_source_transcripts_and_first_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source(root / "hook", "JXB-99_hook", "red", 440, "牙齿敏感吗？")
            source(root / "body", "JXB-99_demo", "green", 550,
                   "刷牙要用对方法。")
            source(root / "body", "JXB-99_product", "blue", 660,
                   "这是俊小白修护牙膏。")
            request = VideoConfig(**config(root, selection_mode="speech_logic",
                                           body_dir=root / "body",
                                           base_out_dir=str(root / "out"),
                                           no_fallback_mix=True,
                                           semantic_topic="旧预设里的任意主题"))
            response = TaskService(SharedMediaCache(str(root / "state"))).preflight(request)
            self.assertTrue(response["ok"], response)
            preview = response["report"][0]["speech_logic_preview"]
            self.assertFalse(preview["fallback"])
            self.assertEqual(preview["product_id"], "JXB-99")
            self.assertEqual(len(preview["transcripts"]), 3)
            self.assertTrue(all(item["product_id"] == "JXB-99"
                                for item in preview["transcripts"]))
            self.assertEqual({item["source_type"] for item in preview["transcripts"]},
                             {"hook", "body"})
            self.assertTrue(all(item["cues"] for item in preview["transcripts"]))
            self.assertEqual([item["role"] for item in preview["segments"]],
                             ["hook", "body", "body"])
            self.assertEqual(preview["hook_segment_count"], 1)
            self.assertIn("牙齿敏感吗", preview["transcript"])

    def test_multiple_whole_hooks_precede_body_and_share_hook_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "out").mkdir()
            first = source(root / "hook", "JXB-99_hook_a", "red", 440,
                           "牙齿敏感吗？")
            second = source(root / "hook", "JXB-99_hook_b", "yellow", 500,
                            "冷热酸甜刺激，牙齿酸痛怎么办？")
            source(root / "body", "JXB-99_demo", "green", 550,
                   "刷牙要用对方法。")
            source(root / "body", "JXB-99_product", "blue", 660,
                   "这是俊小白修护牙膏。")
            logs = []
            core = VideoMatrixCore(
                config(root, selection_mode="speech_logic", body_dir=root / "body",
                       total_clips=4, vol_hook_orig=20, vol_orig=100),
                logs.append, SharedMediaCache(str(root / "state")),
            )
            try:
                ok, message = core.pre_flight_check()
                self.assertTrue(ok, message)
                plan = core.semantic_plans[0]
                self.assertEqual(plan["hook_segment_count"], 2)
                self.assertEqual(plan["hook_duration_s"], 4.0)
                self.assertEqual({Path(x["file"]) for x in plan["segments"][:2]},
                                 {first, second})
                self.assertEqual([x["role"] for x in plan["segments"]],
                                 ["hook", "hook", "demonstrate", "product"])
                success, output, _ = core.render_single_video(1, return_result=True)
                self.assertTrue(success, "\n".join(logs))
                self.assertAlmostEqual(float(probe_media(output)["format"]["duration"]),
                                       6.0, delta=0.15)
                tones = audio_tones(output, (0.5, 2.5, 4.5, 5.5))
                for measured, expected in zip(sorted(tones[:2]), (440, 500)):
                    self.assertAlmostEqual(measured, expected, delta=25)
                self.assertAlmostEqual(tones[2], 550, delta=25)
                self.assertAlmostEqual(tones[3], 660, delta=25)
                levels = audio_levels(output, (0.5, 2.5, 4.5))
                self.assertLess(levels[0], levels[2] * 0.35)
                self.assertLess(levels[1], levels[2] * 0.35)
                self.assertEqual(core.output_configs[1]["_hook_clip_count"], 2)
            finally:
                core.temp_dir.cleanup()

    def test_preflight_and_render_follow_spoken_order_with_original_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "out").mkdir()
            hook = source(root / "hook", "JXB-99_hook", "red", 440, "牙齿敏感吗？")
            demo = source(root / "body", "JXB-99_demo", "green", 550, "刷牙要用对方法。")
            product = source(root / "body", "JXB-99_product", "blue", 660,
                             "这是俊小白修护牙膏。")
            logs = []
            core = VideoMatrixCore(
                config(root, selection_mode="speech_logic", body_dir=root / "body"),
                logs.append, SharedMediaCache(str(root / "state")),
            )
            try:
                ok, message = core.pre_flight_check()
                self.assertTrue(ok, message)
                self.assertEqual(len(core.semantic_plans), 1)
                plan = core.semantic_plans[0]
                self.assertEqual([item["role"] for item in plan["segments"]],
                                 ["hook", "demonstrate", "product"])
                self.assertEqual([Path(item["file"]) for item in plan["segments"]],
                                 [hook, demo, product])
                self.assertEqual([item["start"] for item in plan["segments"]],
                                 [0.0, 0.2, 0.2])
                self.assertEqual([item["duration"] for item in plan["segments"]],
                                 [2.0, 1.0, 1.0])
                self.assertEqual(plan["hook_segment_count"], 1)
                self.assertAlmostEqual(plan["hook_duration_s"], 2.0, delta=0.1)

                success, output, _ = core.render_single_video(1, return_result=True)
                self.assertTrue(success, "\n".join(logs))
                self.assertTrue(Path(output).is_file())
                details = probe_media(output)
                self.assertTrue(any(item["codec_type"] == "audio" for item in details["streams"]))
                self.assertAlmostEqual(float(details["format"]["duration"]), 4.0, delta=0.15)
                self.assertEqual(
                    [dominant_channel(frame_bytes(output, frame)) for frame in (12, 60, 84)],
                    [0, 1, 2],
                )
                for measured, expected in zip(audio_tones(output, (0.5, 2.5, 3.5)),
                                              (440, 550, 660)):
                    self.assertAlmostEqual(measured, expected, delta=25)
                self.assertEqual(
                    [item["role"] for item in core.output_configs[1]["_semantic_plan"]],
                    ["hook", "demonstrate", "product"],
                )
            finally:
                core.temp_dir.cleanup()

    def test_mixed_product_and_missing_middle_create_review_only_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "out").mkdir()
            source(root / "hook", "JXB-99_hook", "red", 440, "牙齿敏感吗？")
            source(root / "mixed", "JXB-COLOR_demo", "green", 550, "刷牙要用对方法。")
            source(root / "mixed", "JXB-99_product", "blue", 660,
                   "这是俊小白修护牙膏。")
            mixed = VideoMatrixCore(
                config(root, selection_mode="speech_logic", body_dir=root / "mixed"),
                lambda _: None, SharedMediaCache(str(root / "state")),
            )
            try:
                ok, message = mixed.pre_flight_check()
                self.assertTrue(ok, message)
                self.assertIn("降级", message)
                self.assertTrue(mixed.semantic_plans[0]["fallback"])
                self.assertTrue(all(item["product_id"] == "JXB-99" for item in
                                    mixed.semantic_plans[0]["segments"]))
                self.assertFalse(mixed.hook_pool)
                self.assertFalse(list((root / "out").glob("*.mp4")))
            finally:
                mixed.temp_dir.cleanup()

            source(root / "incomplete", "JXB-99_fragment", "green", 550,
                   "因为这个")
            source(root / "incomplete", "JXB-99_product", "blue", 660,
                   "这是俊小白修护牙膏。")
            incomplete = VideoMatrixCore(
                config(root, selection_mode="speech_logic", body_dir=root / "incomplete"),
                lambda _: None, SharedMediaCache(str(root / "state2")),
            )
            try:
                ok, message = incomplete.pre_flight_check()
                self.assertTrue(ok, message)
                self.assertIn("降级", message)
                self.assertTrue(incomplete.semantic_plans[0]["fallback"])
                self.assertTrue(incomplete.semantic_plans[0]["warnings"])
                self.assertFalse(list((root / "out").glob("*.mp4")))
            finally:
                incomplete.temp_dir.cleanup()

    def test_existing_random_mode_still_renders_without_semantic_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "out").mkdir()
            source(root / "hook", "JXB-99_hook", "red", 440, "牙齿敏感吗？")
            source(root / "mixed", "JXB-COLOR_only", "green", 550,
                   "因为这个")
            core = VideoMatrixCore(
                config(root, selection_mode="random", body_dir=root / "mixed"),
                lambda _: None, SharedMediaCache(str(root / "state")),
            )
            try:
                ok, message = core.pre_flight_check()
                self.assertTrue(ok, message)
                self.assertEqual(core.semantic_plans, [])
                success, output, _ = core.render_single_video(1, return_result=True)
                self.assertTrue(success)
                self.assertTrue(Path(output).is_file())
                self.assertNotIn("_semantic_plan", core.output_configs[1])
            finally:
                core.temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
