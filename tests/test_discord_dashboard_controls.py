from fastapi.testclient import TestClient
from gamma.dashboard import main


def test_discord_control_requires_dashboard_auth(monkeypatch):
    monkeypatch.setattr(main,'is_authenticated',lambda request:False)
    client=TestClient(main.app)
    for url in ['/api/discord/voice/control','/api/coding/projects']:
        response=client.post(url,json={'command':'join'})
        assert response.status_code==401


def test_owner_control_and_invalid_command(monkeypatch):
    monkeypatch.setattr(main,'is_authenticated',lambda request:True)
    class Service:
        def discord_voice_control(self,command): return {'status':'queued','command':command}
    monkeypatch.setattr(main,'get_dashboard_service',lambda:Service())
    client=TestClient(main.app)
    response=client.post('/api/discord/voice/control',json={'command':'pause'})
    assert response.json()=={'status':'queued','command':'pause'}
    assert client.post('/api/discord/voice/control',json={'command':'exec'}).status_code==422
