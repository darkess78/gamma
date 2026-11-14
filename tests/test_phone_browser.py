"""Execute the actual thin client with controlled network and privacy races."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_phone_browser_privacy_replay_and_notification_contract():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the actual phone client contract')
    result = subprocess.run([node, str(Path(__file__).with_name('test_phone_browser_contract.cjs'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"passed":true' in result.stdout


def test_phone_notification_worker_has_fixed_destination_and_no_background_handlers():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the actual phone worker contract')
    result = subprocess.run([node, str(Path(__file__).with_name('test_phone_worker_contract.cjs'))],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"passed":true' in result.stdout
