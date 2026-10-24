"""Trusted activation of verified source through existing service controls."""
import json
import os
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler


def activate_source(paths):
    targets = set()
    for path in paths:
        if path.startswith(('src/gamma/dashboard/', 'src/gamma/coding/')):
            targets.add('dashboard')
        else:
            targets.add('shana')
        # Discord has an independent long-lived worker. A source change needs
        # a voice-preserving activation adapter, not a claim based on API health.
        if path.startswith('src/gamma/integrations/'):
            return {'passed':False,'reason':'Integration worker activation requires its specific runtime acceptance adapter'}
    evidence = {}
    for target in sorted(targets):
        hidden = {}
        if os.name == 'nt':
            hidden['creationflags'] = subprocess.CREATE_NO_WINDOW
            info = subprocess.STARTUPINFO(); info.dwFlags |= subprocess.STARTF_USESHOWWINDOW; info.wShowWindow = subprocess.SW_HIDE
            hidden['startupinfo'] = info
        result = subprocess.run([sys.executable, '-m', 'gamma.supervisor.cli', 'restart', target],
                                capture_output=True, text=True, timeout=40, check=False, **hidden)
        try:
            report = json.loads(result.stdout)
        except ValueError:
            report = {}
        if result.returncode != 0 or report.get(target,{}).get('ok') is not True:
            return {'passed':False,'reason':'Managed service restart failed','target':target}
        url = 'http://127.0.0.1:8001/dashboard' if target == 'dashboard' else 'http://127.0.0.1:8000/health'
        opener = build_opener(ProxyHandler({}))
        healthy = False
        for _ in range(20):
            try:
                with opener.open(url, timeout=2) as response:
                    healthy = response.status == 200
                if healthy:
                    break
            except OSError:
                pass
            time.sleep(0.5)
        evidence[target] = {'restart_verified':True,'http_healthy':healthy}
        if not healthy:
            return {'passed':False,'reason':'Service did not become healthy','evidence':evidence}
    return {'passed':True,'evidence':evidence}
