"""Update installation must account for every kind of background job."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.api import routes, subtitles


class UpdateActiveJobTests(unittest.TestCase):
    def test_active_count_includes_all_job_services(self):
        tasks = [SimpleNamespace(status="pending"), SimpleNamespace(status="running"),
                 SimpleNamespace(status="completed")]
        with patch.object(routes.task_service, "get_all_tasks", return_value=tasks), \
             patch.object(routes.standalone_variant_service, "active_count", return_value=1), \
             patch.object(routes.watermark_removal_service, "active_count", return_value=2), \
             patch.object(routes.link_watermark_service, "active_count", return_value=3), \
             patch.object(routes, "active_export_count", return_value=4):
            self.assertEqual(routes.active_task_count(), {"count": 12})

    def test_benchmark_and_preflight_leases_remain_active_until_work_finishes(self):
        with patch.object(routes.task_service, "get_all_tasks", return_value=[]), \
             patch.object(routes.standalone_variant_service, "active_count", return_value=0), \
             patch.object(routes.watermark_removal_service, "active_count", return_value=0), \
             patch.object(routes.link_watermark_service, "active_count", return_value=0), \
             patch.object(routes, "active_export_count", return_value=0):
            release_benchmark = routes._begin_auxiliary_work()
            release_preflight = routes._begin_auxiliary_work()
            try:
                self.assertEqual(routes.active_task_count(), {"count": 2})
                release_benchmark()
                self.assertEqual(routes.active_task_count(), {"count": 1})
            finally:
                release_benchmark()
                release_preflight()
            self.assertEqual(routes.active_task_count(), {"count": 0})

    def test_srt_export_is_active_until_the_request_finishes(self):
        self.assertEqual(subtitles.active_export_count(), 0)

        def check_while_exporting(_path):
            self.assertEqual(subtitles.active_export_count(), 1)
            return subtitles.ExportSrtResponse(
                video_path="sample.mp4", srt_path="sample.srt", folder=".",
                cue_count=1, status="created",
            )

        with patch.object(subtitles, "export_srt", side_effect=check_while_exporting):
            subtitles.export_srt_route(subtitles.ExportSrtRequest(video_path="sample.mp4"))
        self.assertEqual(subtitles.active_export_count(), 0)


if __name__ == "__main__":
    unittest.main()
