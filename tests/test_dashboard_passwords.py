"""Real password derivation, fail-closed cookies and persistent dashboard login."""
import hashlib
import hmac
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from gamma.config import settings
from gamma.dashboard import auth
from gamma.dashboard import passwords

@pytest.fixture(scope='module')
def credentials():
    password='a private Shana phrase with 你好'
    return password,{'version':1,'username':'darkess78','password_hash':passwords.hash_password(password),
                     'session_secret':'s'*64,'credential_version':uuid.uuid4().hex}

def save(path,value):
    path.write_text(json.dumps(value));path.chmod(0o600);return path

def configured(path,**extra):
    return patch.multiple(settings,dashboard_auth_enabled=True,dashboard_auth_username='legacy',
        dashboard_auth_password='legacy-plaintext',dashboard_session_secret='legacy-secret',
        dashboard_credentials_file=str(path),**extra)

def test_real_scrypt_salts_and_unicode_verification(credentials):
    password,record=credentials
    other=passwords.hash_password(password)
    assert other!=record['password_hash']
    assert passwords.verify_password(password,record['password_hash'])
    assert not passwords.verify_password(password+'wrong',record['password_hash'])
    assert not passwords.verify_password('x'*1025,record['password_hash'])

@pytest.mark.parametrize('encoded',[None,'','scrypt-v2$a$b','scrypt-v1$@@$@@','x'*121,'scrypt-v1$AA==$AA=='])
def test_malformed_verifiers_never_start_expensive_derivation(encoded):
    with patch.object(passwords.hashlib,'scrypt',side_effect=AssertionError('unexpected KDF')):
        assert not passwords.verify_password('any-password',encoded)

@pytest.mark.parametrize('failure',['missing','malformed','permissions','symlink'])
def test_configured_file_failure_never_uses_legacy_plaintext(tmp_path,credentials,failure):
    path=tmp_path/'credential.json'
    if failure=='malformed':save(path,{'version':99})
    elif failure=='permissions':save(path,credentials[1]).chmod(0o644)
    elif failure=='symlink':
        target=save(tmp_path/'real.json',credentials[1]);path.symlink_to(target)
    with configured(path):
        assert not auth.dashboard_auth_ready()
        assert not auth.verify_login('legacy','legacy-plaintext')
        request=SimpleNamespace(cookies={'gamma_dashboard_session':auth.session_cookie_value('legacy')})
        assert not auth.is_authenticated(request)
        assert not auth.websocket_is_authenticated(request)

def test_legacy_login_still_works_but_incomplete_auth_rejects_forged_cookies():
    with patch.multiple(settings,dashboard_credentials_file='',dashboard_auth_enabled=True,
                        dashboard_auth_username='owner',dashboard_auth_password='legacy',dashboard_session_secret='stable'):
        assert auth.verify_login('owner','legacy')
        good=SimpleNamespace(cookies={'gamma_dashboard_session':auth.session_cookie_value('owner')})
        assert auth.is_authenticated(good)
        with patch.object(settings,'dashboard_session_secret',''):
            forged='owner:'+hmac.new(b'',b'owner',hashlib.sha256).hexdigest()
            bad=SimpleNamespace(cookies={'gamma_dashboard_session':forged})
            assert not auth.is_authenticated(bad)
            assert not auth.websocket_is_authenticated(bad)

def test_password_update_invalidates_http_and_websocket_cookies(tmp_path,credentials):
    password,record=credentials;path=save(tmp_path/'credentials.json',record)
    with configured(path):
        old=auth.authenticate_login('darkess78',password)
        request=SimpleNamespace(cookies={'gamma_dashboard_session':old})
        assert auth.is_authenticated(request) and auth.websocket_is_authenticated(request)
        replacement={**record,'password_hash':passwords.hash_password('a different private phrase')}
        save(path,replacement)
        assert not auth.is_authenticated(request) and not auth.websocket_is_authenticated(request)
        assert not auth.verify_login('darkess78',password)
        assert auth.verify_login('darkess78','a different private phrase')

def test_login_cookie_uses_the_same_credential_snapshot_as_password_check(tmp_path,credentials):
    password,record=credentials;path=save(tmp_path/'credentials.json',record)
    with configured(path):
        def changed(*args):
            save(path,{**record,'credential_version':uuid.uuid4().hex})
            return True
        with patch.object(auth,'verify_password',side_effect=changed):
            old=auth.authenticate_login('darkess78',password)
        assert not auth.is_authenticated(SimpleNamespace(cookies={'gamma_dashboard_session':old}))

def test_real_login_with_hash_only_and_secure_cookie(tmp_path,credentials):
    from gamma.coordination.dashboard import build_app
    from gamma.dashboard.shana_client import ShanaApiClient
    password,record=credentials;path=save(tmp_path/'credentials.json',record)
    backend=ShanaApiClient(base_url='http://isolated',transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'ok':True})))
    with configured(path,dashboard_cookie_secure=True), TestClient(build_app(backend),base_url='https://testserver') as client:
        assert client.get('/dashboard/home',follow_redirects=False).status_code==303
        login=client.post('/login',data={'username':'darkess78','password':password},follow_redirects=False)
        assert login.status_code==303
        cookie=login.headers['set-cookie'].lower()
        assert 'httponly' in cookie and 'secure' in cookie and 'samesite=lax' in cookie
        assert client.get('/dashboard/home').status_code==200
        assert auth.auth_config().password==''

def test_new_processes_use_saved_credentials_after_restart(tmp_path,credentials):
    import subprocess,sys
    password,record=credentials;path=save(tmp_path/'credentials.json',record)
    project=Path(__file__).parents[1]
    program="import json,sys; from gamma.dashboard.auth import authenticate_login; value=json.load(sys.stdin); print(json.dumps({'ok':authenticate_login(value['username'],value['password']) is not None}))"
    env={**os.environ,'PYTHONPATH':str(project/'src'),'SHANA_DASHBOARD_CREDENTIALS_FILE':str(path),'SHANA_DASHBOARD_AUTH_ENABLED':'true'}
    for _ in range(2):
        result=subprocess.run([sys.executable,'-c',program],input=json.dumps({'username':'darkess78','password':password}),capture_output=True,text=True,env=env,timeout=15,check=True)
        assert json.loads(result.stdout)=={'ok':True}


def test_fifo_credentials_fail_closed_without_blocking_the_server(tmp_path):
    import subprocess,sys
    fifo=tmp_path/'credentials.fifo';os.mkfifo(fifo,0o600)
    project=Path(__file__).parents[1]
    program="from gamma.dashboard.auth import dashboard_auth_ready; assert not dashboard_auth_ready()"
    env={**os.environ,'PYTHONPATH':str(project/'src'),'SHANA_DASHBOARD_CREDENTIALS_FILE':str(fifo),'SHANA_DASHBOARD_AUTH_ENABLED':'true'}
    result=subprocess.run([sys.executable,'-c',program],capture_output=True,text=True,env=env,timeout=5)
    assert result.returncode==0,result.stderr
