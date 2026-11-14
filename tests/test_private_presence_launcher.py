"""The Windows presentation launcher must never start a runtime or a shell."""
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import sys

import pytest


def launcher():
    source = Path(__file__).resolve().parents[1] / 'scripts' / 'private_presence_client.pyw'
    loader = SourceFileLoader('private_presence_client_test', str(source))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


@pytest.mark.parametrize('url', ['http://127.0.0.1:18701', 'http://[::1]:18701/dashboard/presence'])
def test_isolated_local_url(url):
    assert launcher().validate_url(url).endswith('/dashboard/presence')


@pytest.mark.parametrize('url', [
    'http://127.0.0.1:8001', 'http://127.0.0.1:8000', 'http://127.0.0.1:8080',
    'http://127.0.0.1:11434', 'http://gamma.neety.me:18701', 'https://example.com',
    'http://127.0.0.1:18701/dashboard/monitor', 'http://x:y@127.0.0.1:18701',
    'http://127.0.0.1:18701?token=secret', 'http://127.0.0.1:18701/#secret',
])
def test_other_routes_and_destinations_fail(url):
    with pytest.raises(ValueError):
        launcher().validate_url(url)


def test_launcher_only_passes_private_url_to_supported_browser(tmp_path):
    module = launcher()
    browser = tmp_path / 'brave.exe'
    browser.touch()
    argv = module.build_command('http://127.0.0.1:18701', browser, (12, 34), (400, 600))
    assert argv[0] == str(browser)
    assert '--app=http://127.0.0.1:18701/dashboard/presence' in argv
    assert '--window-position=12,34' in argv
    assert '--window-size=400,600' in argv
    assert not any('allow' in part or 'ignore-certificate' in part for part in argv)


def test_launcher_logs_hidden_browser_child_without_shell(tmp_path, monkeypatch):
    module = launcher()
    module.PROJECT = tmp_path
    browser = tmp_path / 'msedge.exe'
    browser.touch()
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(module.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    calls = []
    class Process:
        pid = 123
    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return Process()
    result = module.launch('http://127.0.0.1:18701', browser, popen=popen)
    assert result['browser_pid'] == 123 and result['microphone_auto_enabled'] is False
    assert calls[0][1]['creationflags'] & 0x08000000
    assert 'shell' not in calls[0][1]
    assert calls[0][1]['stderr'] is calls[0][1]['stdout']
    assert not result['activation'] and not result['starts_services']
    assert len(list((tmp_path / 'data/coordination/p5/client').glob('*/launch.json'))) == 1


def test_launcher_logs_cannot_escape_isolated_data(tmp_path, monkeypatch):
    module = launcher()
    module.PROJECT = tmp_path
    browser = tmp_path / 'brave.exe'
    browser.touch()
    monkeypatch.setattr(sys, 'platform', 'win32')
    with pytest.raises(ValueError):
        module.launch('http://127.0.0.1:18701', browser, log_dir=tmp_path / 'outside')
