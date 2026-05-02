from __future__ import annotations

import unittest

from novel_pipeline_stable.monitor_dashboard import DASHBOARD_HTML
from novel_pipeline_stable.monitor_server import MODERN_DASHBOARD_HTML


class MonitorDashboardTest(unittest.TestCase):
    def test_dashboard_contains_operational_controls(self) -> None:
        self.assertIn("Novel Pipeline Monitor", DASHBOARD_HTML)
        self.assertIn("searchInput", DASHBOARD_HTML)
        self.assertIn("statusSegments", DASHBOARD_HTML)
        self.assertIn("refreshSelect", DASHBOARD_HTML)
        self.assertIn("Failures", DASHBOARD_HTML)
        self.assertIn("Raw status", DASHBOARD_HTML)

    def test_monitor_server_uses_modern_dashboard(self) -> None:
        self.assertIs(MODERN_DASHBOARD_HTML, DASHBOARD_HTML)


if __name__ == "__main__":
    unittest.main()
