from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gamma.dashboard import coding_routes
from gamma.coding import autonomous_store as store
from gamma.coding.autonomous import RepositoryAgent
from gamma.coding.project_transaction import apply_verified_changes, digest


def test_dashboard_goal_and_control_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(coding_routes, 'settings', SimpleNamespace(data_dir=tmp_path))
    requests=[]
    def submit(request):
        requests.append(request)
        return {'job':store.create_job(tmp_path/'coding/autonomous',request.goal), 'process':{'ok':True}}
    monkeypatch.setattr(coding_routes,'start_goal',submit)
    app=FastAPI();app.include_router(coding_routes.router)
    with TestClient(app) as client:
        assert client.post('/api/coding/work',json={'goal':'short'}).status_code==422
        response=client.post('/api/coding/work',json={'goal':'Improve lessons while preserving owner edits'})
        assert response.status_code==200
        identifier=response.json()['job']['id']
        assert len(requests)==1
        assert client.get('/api/coding/work').json()['jobs'][0]['id']==identifier
        assert client.post(f'/api/coding/work/{identifier}/control',json={'action':'pause'}).json()['requested']=='pause'
        assert client.post(f'/api/coding/work/{identifier}/control',json={'action':'resume'}).status_code==200
        assert client.post(f'/api/coding/work/{identifier}/control',json={'action':'stop'}).status_code==200
        assert client.post(f'/api/coding/work/{identifier}/control',json={'action':'delete'}).status_code==422


def test_stop_before_snapshot_does_not_run_inference(tmp_path):
    root=tmp_path/'jobs';record=store.create_job(root,'Improve the module with a verified result')
    store.control_job(root,record['id'],'stop')
    agent=RepositoryAgent(project=tmp_path/'project',root=root,endpoint='http://127.0.0.1:11434')
    agent.ask=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('No inference'))
    result=agent.execute(record['id'])
    assert result['status']=='stopped'
    assert not (store.job_path(root,record['id'])/'workspace').exists()


def test_activation_failure_rolls_back_installed_source(tmp_path):
    root=tmp_path/'project';path=root/'src/gamma/memory/example.py';path.parent.mkdir(parents=True);path.write_text('x=1\n')
    result=apply_verified_changes(root,{'src/gamma/memory/example.py':'x=2\n'},
        {'src/gamma/memory/example.py':digest(path)},tmp_path/'receipt',
        lambda:{'passed':False,'evidence':{'activation':{'passed':False}}})
    assert result['status']=='rolled_back' and not result['passed']
    assert path.read_text()=='x=1\n'
