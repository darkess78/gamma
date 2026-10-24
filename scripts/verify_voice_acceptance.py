"""Disposable real-audio acceptance. Private profile audio never leaves Gamma.

Uses actual Whisper/Qwen workers and Chromium media playback. Capture, decision,
and review are explicitly synthetic fixtures; this is not microphone acceptance.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile


def profile_from(root):
    code = ('import json; from dataclasses import asdict; '
            'from gamma.voice.voice_profiles import resolve_tts_config; '
            'print(json.dumps(asdict(resolve_tts_config())))')
    result = subprocess.run([sys.executable, '-c', code], env=dict(os.environ, SHANA_PROJECT_ROOT=str(root)),
                            capture_output=True, text=True, check=True, timeout=20)
    return json.loads(result.stdout)


def toml_value(value):
    if isinstance(value, dict):
        return '{' + ','.join(f'{key}={toml_value(item)}' for key, item in value.items()) + '}'
    return json.dumps(value)


async def run(args, root, source, profile):
    import httpx
    sockets = [socket.socket() for _ in range(4)]
    for sock in sockets:
        sock.bind(('127.0.0.1', 0))
    api_port, dashboard_port, review_port, qwen_port = [sock.getsockname()[1] for sock in sockets]
    sockets[-1].close()
    token, password = secrets.token_hex(24), secrets.token_hex(24)
    profile['qwen_tts_endpoint'] = f'http://127.0.0.1:{qwen_port}/tts'
    # Only the resolved profile is copied into this private temporary fixture.
    # No production/local configuration file is modified or copied wholesale.
    with (root/'config/voices.toml').open('a') as handle:
        handle.write('\n[profiles.acceptance_intended]\n')
        for key, value in profile.items():
            if value is not None and key not in {'profile_id', 'profile_label'}:
                handle.write(f'{key}={toml_value(value)}\n')
    os.environ.update(SHANA_PROJECT_ROOT=str(root), SHANA_DATABASE_URL=f'sqlite:///{root}/data/fixture.db',
        SHANA_LLM_PROVIDER='mock', SHANA_LLM_ROUTER_ENABLED='false', SHANA_BRAIN_MODEL_RUNTIME='worker',
        SHANA_STT_PROVIDER='faster-whisper', SHANA_STT_RUNTIME='worker', SHANA_STT_MODEL=str(args.whisper_model),
        SHANA_STT_DEVICE='cuda:0', SHANA_STT_DEVICE_INDEX='0', SHANA_STT_COMPUTE_TYPE='float16',
        SHANA_TTS_PROVIDER='qwen-tts', SHANA_TTS_RUNTIME='worker', SHANA_TTS_PROFILE='acceptance_intended',
        SHANA_PROACTIVE_IDLE_ENABLED='false', SHANA_AUDIO_UNDERSTANDING_ENABLED='false',
        SHANA_LIVE_VOICE_RUNTIME='core', SHANA_VOICE_UPLOAD_MAX_BYTES='2097152',
        SHANA_API_AUTH_ENABLED='true', SHANA_API_BEARER_TOKEN=token,
        SHANA_BIND_HOST='127.0.0.1', SHANA_PORT=str(api_port),
        SHANA_DASHBOARD_AUTH_ENABLED='true', SHANA_DASHBOARD_AUTH_USERNAME='fixture',
        SHANA_DASHBOARD_AUTH_PASSWORD=password, SHANA_DASHBOARD_SESSION_SECRET=secrets.token_hex(32),
        SHANA_DASHBOARD_COOKIE_SECURE='false', CUDA_VISIBLE_DEVICES=str(args.gpu),
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', GAMMA_LIFECYCLE_MANAGER='',
        SHANA_SPEECH_FILTER_LLM_ENABLED='true', SHANA_SPEECH_FILTER_LLM_MODEL='fixture-review',
        SHANA_LOCAL_LLM_ENDPOINT=f'http://127.0.0.1:{review_port}')
    phrase = 'The quiet garden has seven yellow flowers.'
    (root/'input.txt').write_text(phrase)
    audio = root/'input.wav'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
        f'flite=textfile={root / "input.txt"}:voice=slt', '-af', 'adelay=800|800,apad=pad_dur=2',
        '-ar', '16000', str(audio)], check=True, timeout=20)
    from gamma.system.cuda_env import prepend_cuda_library_path
    qwen_env = prepend_cuda_library_path(dict(os.environ, QWEN_TTS_MODEL=str(args.qwen_model),
        QWEN_TTS_DTYPE='bfloat16', QWEN_TTS_DEVICE='cuda:0', QWEN_TTS_LOCAL_FILES_ONLY='1',
        QWEN_TTS_HOST='127.0.0.1', QWEN_TTS_PORT=str(qwen_port)))
    # Redacted fixture diagnostics: exception classes and code locations only.
    from gamma.voice.tts_runtime import get_tts_worker
    diagnostic_code = """import json, traceback
from gamma.voice.tts_worker import serve
from gamma.voice.tts import TTSService
from gamma.config import settings
class DiagnosticTTS(TTSService):
    def synthesize(self, *args, **kwargs):
        try: return super().synthesize(*args, **kwargs)
        except Exception as exc:
            report=[]
            seen=set()
            while exc is not None and id(exc) not in seen:
                seen.add(id(exc))
                report.append({'type': type(exc).__name__, 'code': getattr(exc,'code',None), 'locations': [(f.name,f.lineno) for f in traceback.extract_tb(exc.__traceback__)]})
                exc=exc.__cause__ or exc.__context__
            (settings.data_dir/'tts-diagnostic.json').write_text(json.dumps(report))
            raise
import sys
serve(sys.stdin.buffer,sys.stdout.buffer,service_factory=DiagnosticTTS)
"""
    get_tts_worker().client.command = [sys.executable, '-c', diagnostic_code]
    import uvicorn
    from gamma.main import app as api
    from gamma.dashboard.main import app as dashboard
    from gamma.presence import save_presence_state, presence_state_for_mode
    from gamma.config import settings
    assert settings.project_root == root
    assert settings.local_llm_endpoint == f'http://127.0.0.1:{review_port}'
    review_calls = []
    from fastapi import FastAPI
    reviewer = FastAPI()
    @reviewer.post('/api/generate')
    async def review_fixture(payload: dict):
        review_calls.append(len(payload.get('prompt', '')))
        return {'response': json.dumps({'action':'allow', 'reason':'synthetic acceptance fixture', 'confidence':1})}
    @api.post('/fixture/stt/crash')
    async def crash_stt():
        from gamma.api.routes import get_voice_roundtrip_service
        client = get_voice_roundtrip_service()._stt._client
        process = client._process
        assert process is not None and process.poll() is None
        process.kill()
        await asyncio.to_thread(process.wait, timeout=5)
        return {"reaped": True, "pid": process.pid}
    save_presence_state(presence_state_for_mode('wake'))
    servers = [uvicorn.Server(uvicorn.Config(app, log_level='error')) for app in (api, dashboard, reviewer)]
    tasks = []
    process = None
    with (root/'qwen.log').open('wb') as log:
        qwen = subprocess.Popen([str(args.tts_python), str(source/'scripts/qwen_tts_server.py')],
            cwd=root, env=qwen_env, stdout=log, stderr=log)
        try:
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                for _ in range(900):
                    try:
                        health = await client.get(f'http://127.0.0.1:{qwen_port}/health')
                        if health.status_code == 200:
                            assert health.json()['device'] == 'cuda:0'
                            break
                    except httpx.HTTPError:
                        pass
                    if qwen.poll() is not None:
                        raise RuntimeError('Isolated Qwen server exited; private diagnostic log withheld')
                    await asyncio.sleep(.1)
                else:
                    raise TimeoutError('Isolated Qwen readiness timed out')
            tasks = [asyncio.create_task(server.serve(sockets=[sock])) for server, sock in zip(servers, sockets)]
            for _ in range(400):
                if all(server.started for server in servers): break
                if any(task.done() for task in tasks): raise RuntimeError('Fixture API exited')
                await asyncio.sleep(.05)
            assert all(server.started for server in servers)
            if args.hardware:
                info = {"api_port": api_port, "dashboard_port": dashboard_port,
                        "username": "fixture", "password": password, "token": token,
                        "root": str(root), "pid": os.getpid()}
                session_file = source/'data/runtime/voice-hardware-session.json'
                session_file.write_text(json.dumps(info))
                session_file.chmod(0o600)
                print(json.dumps({"hardware_dashboard_port": dashboard_port, "hardware_api_port": api_port}), flush=True)
                for _ in range(18000):
                    if (root/'stop').exists(): break
                    await asyncio.sleep(.1)
                from sqlalchemy import select
                from sqlmodel import Session
                from gamma.delivery.playback import get_playback_ledger
                from gamma.memory.state_authority import playback_receipts, playback_offers
                from gamma.api.routes import get_live_turn_runtime
                # Keep identifiers/outcomes only, not private transcripts or microphone PCM.
                history = get_live_turn_runtime().get_recent_history(limit=100)
                evidence = {"scope": "physical Windows browser microphone session", "intended_profile": profile['profile_id'],
                            "rvc_enabled": profile['rvc_enabled'], "decision_review": "synthetic fixtures",
                            "turns": [{key: job.get(key) for key in ('turn_id','status','decision_trace_id','delivery_grant','playback_expected','error','cancel_reason')} for job in history]}
                with Session(get_playback_ledger().engine) as session:
                    evidence['playback'] = [dict(row) for row in session.execute(select(playback_receipts.c.lease_id,
                        playback_receipts.c.event_id, playback_receipts.c.status, playback_offers.c.grant_id).join(playback_offers,
                        (playback_receipts.c.lease_id == playback_offers.c.lease_id) & (playback_receipts.c.event_id == playback_offers.c.event_id))).mappings()]
                (source/'data/runtime/voice-hardware-evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
                session_file.unlink(missing_ok=True)
                return
            env = dict(os.environ, FIXTURE_BASE=f'http://127.0.0.1:{dashboard_port}',
                FIXTURE_API=f'http://127.0.0.1:{api_port}', FIXTURE_PASSWORD=password, FIXTURE_TOKEN=token,
                FIXTURE_WAV=str(audio))
            process = await asyncio.create_subprocess_exec('node', str(source/'scripts/verify_voice_acceptance.cjs'),
                env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(process.communicate(), 240)
            if process.returncode:
                diagnostic = root/'data/tts-diagnostic.json'
                raise RuntimeError(stderr.decode() + stdout.decode() + (diagnostic.read_text() if diagnostic.exists() else ''))
            browser = json.loads(stdout)
            assert review_calls, 'No real required-review boundary invocation'
            from gamma.api.routes import get_voice_roundtrip_service
            stt_state = get_voice_roundtrip_service().stt_status()
            assert stt_state['inference_runtime']['device'] == 'cuda'
            allocations = subprocess.check_output(['nvidia-smi', '--query-compute-apps=pid,used_memory',
                '--format=csv,noheader,nounits'], text=True)
            gpu_pids = {int(row.split(',')[0]) for row in allocations.splitlines()}
            assert qwen.pid in gpu_pids and stt_state['pid'] in gpu_pids
            from sqlalchemy import select
            from sqlmodel import Session
            from gamma.delivery.playback import get_playback_ledger
            from gamma.memory.state_authority import playback_receipts, playback_offers, delivery_grants, delivery_artifacts
            from gamma.api.routes import get_live_turn_runtime
            runtime = get_live_turn_runtime()
            traces = []
            with Session(get_playback_ledger().engine) as session:
                for turn_id in browser['spoken_turns']:
                    job = runtime.get_turn(turn_id)
                    grant = session.execute(select(delivery_grants).where(delivery_grants.c.id == job.delivery_grant)).mappings().one()
                    receipts = list(session.execute(select(playback_receipts).join(playback_offers, (playback_receipts.c.lease_id == playback_offers.c.lease_id) & (playback_receipts.c.event_id == playback_offers.c.event_id)).where(playback_offers.c.grant_id == job.delivery_grant)).mappings())
                    assert grant['decision_id'] == job.decision_trace_id
                    assert receipts
                    artifact = session.execute(select(delivery_artifacts).where(delivery_artifacts.c.grant_id == job.delivery_grant)).mappings().one()
                    from gamma.delivery.artifacts import verified_bytes
                    from pcm_metrics import pcm_metrics
                    from transcript_distance import word_error_rate
                    readback_file = root/'data/runtime/uploads/readback.wav'
                    readback_file.write_bytes(verified_bytes(Path(artifact['path']), artifact['digest'], artifact['size']))
                    metrics = pcm_metrics(str(readback_file))
                    assert metrics['rms'] > .002 and .2 < metrics['duration_seconds'] < 30
                    readback = await asyncio.to_thread(get_voice_roundtrip_service()._stt.transcribe_audio, str(readback_file))
                    error = word_error_rate(job.reply_text, readback)
                    assert error <= .25, ('generated reply readback mismatch', error)
                    readback_file.unlink()
                    traces.append(dict(turn_id=turn_id, decision_id=job.decision_trace_id, grant_id=job.delivery_grant,
                                       playback=[row['status'] for row in receipts], transcript=job.transcript, readback_word_error_rate=error, audio_metrics=metrics))
            assert {status for trace in traces for status in trace['playback']} == {'completed','interrupted'}
            print(json.dumps(dict(scope='generated speech capture; real Whisper/Qwen GPU and Chromium playback; synthetic decision/review',
                intended_profile=profile['profile_id'], rvc_enabled=profile['rvc_enabled'],
                hardware_microphone=False, speaker_audibility=False, qwen_and_whisper_gpu_allocation=True, required_review_calls=len(review_calls),
                traces=traces, browser=browser), indent=2), flush=True)
        finally:
            if process is not None and process.returncode is None:
                process.terminate()
                await process.wait()
            for server in servers: server.should_exit = True
            if tasks: await asyncio.gather(*tasks)
            qwen.terminate()
            try: await asyncio.to_thread(qwen.wait, timeout=15)
            except subprocess.TimeoutExpired:
                qwen.kill()
                await asyncio.to_thread(qwen.wait, timeout=5)
            for sock in sockets: sock.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile-root', type=Path, required=True)
    parser.add_argument('--whisper-model', type=Path, required=True)
    parser.add_argument('--qwen-model', type=Path, required=True)
    parser.add_argument('--tts-python', type=Path, required=True)
    parser.add_argument('--gpu', type=int, default=0)
    parser.add_argument('--hardware', action='store_true', help='Keep private loopback fixture open for supervised physical browser input; maximum 30 minutes')
    args=parser.parse_args()
    source=Path(__file__).resolve().parents[1]
    profile=profile_from(args.profile_root)
    assert profile['provider']=='qwen-tts' and not profile['rvc_enabled'], 'This verifier accepts intended Qwen without RVC only'
    reference=Path(profile['qwen_tts_reference_audio'])
    if not reference.is_absolute(): reference=args.profile_root/reference
    profile['qwen_tts_reference_audio']=str(reference.resolve(strict=True))
    free=int(subprocess.check_output(['nvidia-smi','-i',str(args.gpu),'--query-gpu=memory.free','--format=csv,noheader,nounits'],text=True))
    assert free>=8192
    production=subprocess.check_output(['systemctl','--user','show','gamma-shana','gamma-dashboard','-p','MainPID'])
    with tempfile.TemporaryDirectory(prefix='gamma-voice-acceptance-') as directory:
        root=Path(directory)
        (root/'data').mkdir()
        shutil.copy(source/'pyproject.toml',root)
        shutil.copytree(source/'config',root/'config',ignore=shutil.ignore_patterns('*.local.toml'))
        asyncio.run(run(args,root,source,profile))
    assert production==subprocess.check_output(['systemctl','--user','show','gamma-shana','gamma-dashboard','-p','MainPID'])
    print(json.dumps({'production_pids_unchanged':True,'private_fixture_removed':True}))


if __name__=='__main__': main()
