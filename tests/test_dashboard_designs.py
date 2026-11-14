"""Structural and actual-module contracts; visual acceptance remains the owner's."""
from html.parser import HTMLParser
import importlib.util
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
import pytest


PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dashboard_design_check', PROJECT / 'scripts/verify_dashboard_designs.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def test_original_ten_and_combined_preview_have_structural_and_role_contrast_checks():
    if not shutil.which('node'):
        pytest.skip('Node is required to read actual preview metadata')
    styles = check.verify(PROJECT)
    assert len(styles) == 11
    assert len({item['layout'] for item in styles}) == 11
    assert len(check.manifest(PROJECT)) == 10
    assert styles[-1]['id'] == 'night-signal'
    assert not any(item['failing_roles'] for item in styles)


def test_contrast_helper_rejects_the_worker_uncovered_trailing_newline_and_boundary_rounding():
    assert check.contrast('#000000', '#ffffff') == 21
    assert check.contrast('#777777', '#ffffff') < 4.5
    assert check.contrast('#767676', '#ffffff') > 4.5
    with pytest.raises(ValueError):
        check.luminance('#ffffff\n')
    with pytest.raises(ValueError):
        check.luminance('rgb(0,0,0)')
    with pytest.raises(ValueError):
        check.audit_palette({'bg':'#ffffff'})


def test_css_delimiter_check_understands_strings_and_comments_without_claiming_layout():
    assert check.balanced_css('a{content:"}";background:var(--x)} /* [ */')
    with pytest.raises(ValueError):
        check.balanced_css('a{background:var(--x))}')


def test_actual_preview_module_keeps_private_gates_and_default_unchanged():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the actual preview module contract')
    result = subprocess.run([node, str(Path(__file__).with_name('test_dashboard_designs_contract.cjs'))],
                            capture_output=True, text=True, timeout=20, **check.hidden_options())
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"passed":true' in result.stdout


def test_standalone_gallery_has_no_external_asset_dependency_or_private_content(tmp_path):
    target = tmp_path / 'gallery.html'
    digest = check.standalone(PROJECT, target)
    assert len(digest) == 64
    class References(HTMLParser):
        external = []
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == 'script' and 'src' in values or tag == 'link' and 'href' in values:
                self.external.append(values)
    parser = References(); parser.feed(target.read_text(encoding='utf-8'))
    assert parser.external == []
    assert 'Saved static gallery. Fictional content only' in target.read_text(encoding='utf-8')
    with pytest.raises(ValueError):
        check.standalone(PROJECT, target)


def test_gallery_is_on_the_authenticated_existing_dashboard_without_backend_actions():
    from gamma.config import settings
    from gamma.coordination.dashboard import build_app
    from gamma.dashboard.auth import session_cookie_value
    from gamma.dashboard.shana_client import ShanaApiClient
    calls = []
    client = ShanaApiClient(base_url='http://isolated', transport=httpx.MockTransport(
        lambda request:calls.append(request.url.path) or httpx.Response(200, json={'ok':True})))
    with (patch.object(settings, 'dashboard_auth_enabled', True),
          patch.object(settings, 'dashboard_auth_username', 'owner'),
          patch.object(settings, 'dashboard_auth_password', 'test-password'),
          patch.object(settings, 'dashboard_session_secret', 'test-session'),
          TestClient(build_app(client)) as dashboard):
        denied = dashboard.get('/dashboard/designs', follow_redirects=False)
        assert denied.status_code in {303, 307, 401}
        dashboard.cookies.set('gamma_dashboard_session', session_cookie_value('owner'))
        page = dashboard.get('/dashboard/designs')
        assert page.status_code == 200
        assert 'Ten dashboard directions' in page.text and 'fictional sample content' in page.text
        assert page.text.count('/static/coordination_designs.js') == 1
        assert page.text.count('/static/coordination_designs.css') == 1
        assert dashboard.get('/static/coordination_designs.js').status_code == 200
        assert dashboard.get('/static/coordination_designs.css').status_code == 200
        assert 'id="combinedDesign"' in page.text
        assert page.text.count('data-shana-default-style="night-signal"') == 1
        for route, protected in [('home','homeSleep'),('activities',''),('rehearsal',''),('phone','phonePrivate')]:
            plain = dashboard.get('/dashboard/' + route)
            preview = dashboard.get('/dashboard/' + route + '?style=night-signal')
            assert preview.status_code == plain.status_code == 200
            assert preview.text == plain.text
            assert plain.text.count('data-shana-default-style="night-signal"') == 1
            if protected:
                assert protected in preview.text
        assert calls == []
