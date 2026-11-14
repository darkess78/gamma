"""Authenticated private Monitor with no route to legacy stream execution."""
from contextlib import asynccontextmanager
import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .app import PrivateInput, Control, PersonalControl, WorkControl, GrowthControl


def _shell(body):
    if '/static/coordination_shell.js' not in body:
        body = body.replace('</head>', '<link rel="stylesheet" href="/static/coordination_shell.css">'
            '<script src="/static/coordination_shell.js" defer></script></head>')
    if '/static/coordination_designs.js' not in body:
        body = body.replace('</head>', '<link rel="stylesheet" href="/static/coordination_designs.css">'
            '<script src="/static/coordination_designs.js" defer></script></head>')
    if 'data-shana-default-style=' not in body:
        body = re.sub(r'<body\b', '<body data-shana-default-style="night-signal"', body, count=1)
    mode = os.environ.get('GAMMA_COORDINATION_REVIEW_MODE')
    if mode in {'mock','real'} and 'id="coordinationReviewMode"' not in body:
        label = ('Mock review: responses are synthetic. Your normal Shana is unchanged.' if mode == 'mock'
                 else 'Private test session with separate state.')
        body = re.sub(r'(<body[^>]*>)', lambda match:match[1] + '<p class="shana-review-banner" id="coordinationReviewMode" role="status">' + label + '</p>', body, count=1)
    return body


class MonitorInput(PrivateInput):
    synthesize_speech: Literal[False] = False
    output_target_policy: Literal['dashboard_monitor'] = 'dashboard_monitor'
    fast_mode: bool = True


def build_app(client):
    from ..dashboard import main
    from ..config import settings
    if not settings.dashboard_auth_enabled or not main.dashboard_auth_ready():
        raise ValueError('private Monitor requires dashboard authentication')
    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            client.close()
    app = FastAPI(title='Shana isolated private Monitor', lifespan=lifespan)
    app.middleware('http')(main.require_dashboard_auth)
    app.add_exception_handler(main.ShanaClientError, main.shana_client_error_handler)
    app.mount('/static', StaticFiles(directory=main.STATIC_DIR), name='dashboard-static')
    for route in main.app.routes:
        if getattr(route, 'path', None) in {'/health', '/login', '/logout'}:
            app.router.routes.append(route)

    @app.get('/')
    @app.get('/dashboard')
    def private_home():
        return RedirectResponse('/dashboard/home')

    @app.get('/dashboard/home')
    def home_page():
        return HTMLResponse(_shell((Path(__file__).parent/'home.html').read_text(encoding='utf-8')), headers={'Cache-Control':'no-store'})

    @app.get('/dashboard/designs')
    def designs_page():
        return HTMLResponse(_shell((Path(__file__).parent/'designs.html').read_text(encoding='utf-8')), headers={'Cache-Control':'no-store'})

    @app.get('/dashboard/activities')
    def activities_page():
        return HTMLResponse(_shell((Path(__file__).parent/'shared_activities.html').read_text(encoding='utf-8')), headers={'Cache-Control':'no-store'})

    @app.get('/dashboard/rehearsal')
    def rehearsal_page():
        return HTMLResponse(_shell((Path(__file__).parent/'stream_rehearsal.html').read_text(encoding='utf-8')), headers={'Cache-Control':'no-store'})

    @app.post('/api/monitor/input')
    def private_input(payload: MonitorInput):
        values = payload.model_dump(exclude={'synthesize_speech', 'output_target_policy', 'fast_mode'})
        return client.request_json('POST', '/v1/coordination/input', json_payload=values, timeout=180)

    @app.get('/api/coordination/status')
    def status(audience: Literal['owner_private']):
        return client.request_json('GET', '/v1/coordination/status', params={'audience': audience})

    @app.post('/api/coordination/control')
    def control(payload: Control):
        return client.request_json('POST', '/v1/coordination/control', json_payload=payload.model_dump())

    @app.get('/api/coordination/personal')
    def personal(audience: Literal['owner_private']):
        return client.request_json('GET','/v1/coordination/personal',params={'audience':audience})

    @app.post('/api/coordination/personal')
    def personal_control(payload: PersonalControl):
        return client.request_json('POST','/v1/coordination/personal',json_payload=payload.model_dump())

    @app.get('/dashboard/personal')
    def personal_page():
        return HTMLResponse(_shell((Path(__file__).parent/'personal.html').read_text(encoding='utf-8')))

    @app.post('/api/coordination/work')
    def work_control(payload: WorkControl):
        return client.request_json('POST','/v1/coordination/work',json_payload=payload.model_dump())

    @app.get('/api/coordination/work')
    def work_state(audience: Literal['owner_private'], task_id: str | None = None):
        if task_id is not None and not re.fullmatch(r'work-[a-f0-9]{32}',task_id):
            raise ValueError('invalid_task_id')
        params={'audience':audience}
        if task_id is not None:
            params['task_id']=task_id
        return JSONResponse(client.request_json('GET','/v1/coordination/work',params=params),headers={'Cache-Control':'no-store'})

    @app.get('/api/coordination/work/artifact')
    def work_artifact(audience: Literal['owner_private'], task_id: str):
        if not re.fullmatch(r'work-[a-f0-9]{32}',task_id):
            raise ValueError('invalid_task_id')
        value=client.request_json('GET','/v1/coordination/work/artifact',params={'audience':audience,'task_id':task_id})
        return JSONResponse(value,headers={'Content-Disposition':f'attachment; filename="{task_id}-candidate.json"',
                                           'Cache-Control':'no-store'})

    @app.get('/dashboard/work')
    def work_page():
        return HTMLResponse(_shell((Path(__file__).parent/'work.html').read_text(encoding='utf-8')))

    @app.get('/api/coordination/growth')
    def growth_state(audience: Literal['owner_private']):
        return JSONResponse(client.request_json('GET','/v1/coordination/growth',params={'audience':audience}),
                            headers={'Cache-Control':'no-store'})

    @app.post('/api/coordination/growth')
    def growth_control(payload: GrowthControl):
        return JSONResponse(client.request_json('POST','/v1/coordination/growth',json_payload=payload.model_dump()),
                            headers={'Cache-Control':'no-store'})

    @app.get('/dashboard/growth')
    def growth_page():
        return HTMLResponse(_shell((Path(__file__).parent/'growth.html').read_text(encoding='utf-8')))

    from .interaction_routes import add_dashboard_routes
    add_dashboard_routes(app,client)
    from .shared_activity_routes import add_dashboard_routes as add_shared_routes
    from .stream_session_routes import add_dashboard_routes as add_stream_routes
    from .phone_routes import add_dashboard_routes as add_phone_routes
    from .maintenance_routes import add_dashboard_routes as add_maintenance_routes
    from .channel_audio_routes import add_dashboard_routes as add_channel_audio_routes
    for register in (add_shared_routes, add_stream_routes, add_phone_routes, add_maintenance_routes, add_channel_audio_routes):
        register(app,client)

    @app.get('/dashboard/presence')
    def presence_page():
        return HTMLResponse(_shell((Path(__file__).parent/'presence.html').read_text(encoding='utf-8')),
                            headers={'Cache-Control':'no-store'})

    @app.get('/dashboard/monitor')
    def monitor(request: Request):
        body = main.dashboard_monitor_page(request).body.decode('utf-8')
        body = body.replace('<option value="owner_mic">', '<option value="private_text" selected>Private text</option><option value="owner_mic">')
        body = body.replace('id="coordinationActivityPanel" style="display:none"', 'id="coordinationActivityPanel"')
        body = body.replace('value="monitor-local-stream"', 'value="monitor-private"')
        body = body.replace('Send local input or stream context.', 'Private text does not request speech. Confirm the private audience before sending.')
        body = body.replace('<body>', '<body class="coordination-private">')
        mode = os.environ.get('GAMMA_COORDINATION_REVIEW_MODE')
        if mode in {'mock', 'real'}:
            label = 'Mock review: responses are synthetic and work execution is unavailable.' if mode == 'mock' else 'Isolated review: private test state, no live activation.'
            body = body.replace('<body class="coordination-private">', '<body class="coordination-private"><p role="status" id="coordinationReviewMode">' + label + '</p>')
            if mode == 'mock':
                for action in ('practice', 'permit'):
                    body = body.replace('onclick="coordinationAction(\'' + action + '\')"', 'disabled onclick="coordinationAction(\'' + action + '\')"')
        body = body.replace('</head>', '''<script>window.GAMMA_PRIVATE_COORDINATION = true;</script>
<style>.coordination-private .panel:not(#coordinationInputPanel):not(#coordinationActivityPanel):not(.subtitle-panel),
.coordination-private .audio-gate, .coordination-private .header-actions, .coordination-private .overlay-links {display:none}</style>
</head>''')
        body = body.replace('<script src="/static/live.js?v=20260618"></script>', '')
        return HTMLResponse(_shell(body))
    return app


def create_app():
    from ..config import settings
    from ..dashboard import main
    from ..dashboard.shana_client import ShanaApiClient
    project = settings.project_root.resolve()
    if (os.environ.get('GAMMA_ISOLATED_COORDINATION') != '1'
        or Path(__file__).resolve().parents[1] != project / 'src' / 'gamma'
        or project.name in {'gamma-main', 'gamma-modular-phase1'}
        or any((project / p).exists() for p in ('.env', 'config/app.local.toml', 'config/voices.local.toml'))):
        raise RuntimeError('private Monitor requires the isolated portable checkout and explicit opt-in')
    if not settings.dashboard_auth_enabled or not main.dashboard_auth_ready() or not settings.api_auth_enabled or not settings.api_bearer_token:
        raise RuntimeError('isolated Monitor requires configured API and dashboard authentication')
    origin = os.environ.get('GAMMA_COORDINATION_API_ORIGIN', '')
    url = urlsplit(origin)
    if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', '::1'} or not url.port
        or url.port < 1024 or url.port in {8000, 8001, 8080, 11434}
        or url.username or url.password or url.path or url.query or url.fragment):
        raise RuntimeError('select an explicit separate loopback coordination API origin')
    client = ShanaApiClient(base_url=origin)
    try:
        if client.request_json('GET', '/health').get('scope') != 'isolated_owner_private':
            raise RuntimeError('Monitor backend did not identify as the isolated private coordinator')
        app = build_app(client)
        prefix = os.environ.get('GAMMA_COORDINATION_REVIEW_PREFIX', '')
        if prefix:
            from .review_ingress import ReviewIngress
            return ReviewIngress(app, prefix)
        return app
    except BaseException:
        client.close()
        raise
