"""Preflight reports an actionable reason for each uploaded speech source."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.video_matrix import SharedMediaCache, VideoMatrixCore


class SpeechPreflightDiagnosticsTests(unittest.TestCase):
    def test_complete_opening_before_promo_can_join_same_product_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hook = str(root / "色修牙膏_hook.mp4")
            body = str(root / "色修牙膏_body.mp4")
            cues = {
                hook: [
                    {"start": 0.0, "end": 1.2, "text": "俊小白色修牙膏要用完了"},
                    {"start": 1.2, "end": 1.8, "text": "你买了吗"},
                    {"start": 1.8, "end": 2.8, "text": "不然我嘴里又有味了"},
                    {"start": 2.8, "end": 3.7, "text": "买了跟之前一样"},
                    {"start": 3.7, "end": 5.2, "text": "国庆39块9，拍一发六"},
                ],
                body: [
                    {"start": 0.0, "end": 2.0, "text": "关键是它加入了香氛口气清新技术"},
                    {"start": 2.0, "end": 3.0, "text": "刷完牙齿之后"},
                    {"start": 3.0, "end": 4.7, "text": "打个嗝都是香香的"},
                    {"start": 4.7, "end": 6.0, "text": "现在下单三支送两支"},
                ],
            }
            config = {
                "task_name": "opening-before-promo", "body_mode": "normal",
                "duration_mode": "clips", "voice_dir": "", "enable_srt": False,
                "vol_orig": 100, "vol_hook_orig": 100, "total_clips": 2,
                "target_count": 1, "semantic_sku": "色修牙膏",
                "semantic_topic": "旧版中不相关的主题",
            }
            core = VideoMatrixCore(config, lambda _: None,
                                   SharedMediaCache(str(root / "state")))
            try:
                with patch("app.core.speech_transcript.SpeechTranscriptService.get_cues",
                           side_effect=lambda path, **_: cues[path]):
                    ok, message = core._preflight_speech_logic(
                        config, [hook], [body], [],
                        {hook: (5.0, True, 5.2), body: (6.0, True, 6.0)},
                    )
                self.assertTrue(ok, message)
                plan = core.semantic_plans[0]
                self.assertEqual(plan["product_id"], "JXB-COLOR")
                self.assertEqual([(part["in_s"], part["out_s"])
                                  for part in plan["segments"]],
                                 [(0.0, 2.8), (0.0, 4.7)])
                self.assertNotIn("国庆", plan["transcript"])
                self.assertNotIn("下单", plan["transcript"])
                self.assertEqual(core.semantic_transcripts[0]["selected_out_s"], 2.8)
                self.assertTrue(any("仅选完整开场对白" in reason for reason in
                                    core.semantic_transcripts[0]["reasons"]))
            finally:
                core.temp_dir.cleanup()

    def test_unverified_promotion_is_reported_on_its_hook_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hook = str(root / "JXB-COLOR_hook.mp4")
            body = str(root / "JXB-COLOR_body.mp4")
            cues = {
                hook: [
                    {"start": 0.1, "end": 1.1, "text": "黄牙困扰吗？"},
                    {"start": 1.2, "end": 2.5, "text": "国庆拍一发六还送三支牙刷"},
                    {"start": 2.6, "end": 3.5, "text": "真的啊 那我抓你去抢"},
                ],
                body: [
                    {"start": 0.1, "end": 1.0, "text": "刷牙要用对方法。"},
                    {"start": 1.1, "end": 2.3, "text": "这是俊小白色修牙膏。"},
                ],
            }
            config = {
                "task_name": "diagnostic", "body_mode": "normal",
                "duration_mode": "clips", "voice_dir": "", "enable_srt": False,
                "vol_orig": 100, "vol_hook_orig": 100, "total_clips": 3,
                "target_count": 1, "semantic_sku": "色修", "semantic_topic": "美白",
            }
            core = VideoMatrixCore(config, lambda _: None,
                                   SharedMediaCache(str(root / "state")))
            try:
                with patch("app.core.speech_transcript.SpeechTranscriptService.get_cues",
                           side_effect=lambda path, **_: cues[path]):
                    ok, message = core._preflight_speech_logic(
                        config, [hook], [body], [],
                        {hook: (3.8, True, 3.8), body: (2.8, True, 2.8)},
                    )
                self.assertTrue(ok, message)
                self.assertIn("降级", message)
                self.assertEqual(len(core.semantic_transcripts), 2)
                hook_report, body_report = core.semantic_transcripts
                self.assertEqual(hook_report["source_file"], hook)
                self.assertEqual(hook_report["status"], "blocked")
                self.assertTrue(any("促单引导「一发六」" in reason
                                    for reason in hook_report["reasons"]))
                self.assertFalse(any("末句未说完" in reason
                                     for reason in hook_report["reasons"]))
                self.assertIn("活动、价格或赠品话术未核对当期有效", hook_report["reasons"])
                self.assertEqual(body_report["usable_unit_count"], 2)
                self.assertEqual(body_report["status"], "review")
                self.assertTrue(core.semantic_plans[0]["fallback"])
                self.assertTrue(core.semantic_plans[0]["warnings"])
            finally:
                core.temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
