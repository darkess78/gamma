import threading
from unittest.mock import Mock

from gamma.dashboard.service import DashboardService


def test_scheduled_stop_really_stops_the_named_service():
    # Regression: _schedule_stop referenced threading without importing it, so
    # the dashboard's stop control raised NameError instead of stopping anything.
    service = DashboardService()
    service._process_manager = Mock()
    stopped = threading.Event()
    service._process_manager.stop.side_effect = lambda name: stopped.set()

    service._schedule_stop("shana", delay_seconds=0)

    assert stopped.wait(2)
    service._process_manager.stop.assert_called_once_with("shana")
