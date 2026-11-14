"""Run the actual dashboard modules through controlled DOM and transport races."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('module',['presence','phone'])
def test_checked_ui_delivery_has_no_polling_or_history_fetch_delay(module):
    node=shutil.which('node')
    if not node:
        pytest.skip('Node is required for actual dashboard module contracts')
    options={'capture_output':True,'text':True,'timeout':20}
    if os.name=='nt':
        options['creationflags']=subprocess.CREATE_NO_WINDOW
    result=subprocess.run([node,str(Path(__file__).with_name('test_ui_latency_contract.cjs')),module],**options)
    assert result.returncode==0,result.stdout+result.stderr
    assert '"passed":true' in result.stdout
