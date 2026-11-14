"""Controlled Node DOM checks, not visual Brave or game acceptance."""
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("case", [
    "renewal", "oneRefresh", "noUninterpretedReview", "privacyPendingReview",
    "pagehide", "timeout", "goalCancels",
    "historicalMoment", "historicalPrivacy", "watchLifecycle", "watchBusy",
    "watchDisconnect", "watchPendingStartPrivacy", "watchWindowEnded",
    "watchStopDuringStart", "receiptRetryDoesNotResendText", "watchPagehide",
    "finalWatchReceipt", "momentListening", "uncertainMomentReceiptNoSpeech", "explicitSingleMomentGrant",
])
def test_live_moment_browser_contract(case):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([
        "node", str(root / "tests/shared_activity_browser_contract.cjs"),
        str(root / "src/gamma/dashboard/static/shared_activities.js"), case,
    ], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
