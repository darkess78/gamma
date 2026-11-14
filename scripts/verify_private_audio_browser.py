"""Actual local STT/Core/TTS through the private browser Audio consumer.

Controlled generated input is not a room microphone or proof of human hearing.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request
import uuid

from growth_probe_support import Host, checker_files, hashed, source_files


def catalog(endpoint):
    with urllib.request.urlopen(endpoint.rstrip('/')+'/api/tags',timeout=10) as response:
        return {row['name']:row['digest'] for row in json.load(response)['models']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',default='qwen3.8:27b')
    parser.add_argument('--endpoint',default='http://127.0.0.1:11434')
    parser.add_argument('--profile',default='qwen_shana_dataset_xvec_soft')
    parser.add_argument('--source-snapshot',type=Path,required=True)
    args=parser.parse_args();project=Path(__file__).resolve().parents[1]
    if os.name!='posix' or project.name in {'gamma-main','gamma-modular-phase1'}:raise RuntimeError('isolated Gamma required')
    snapshot=args.source_snapshot.resolve()
    if not snapshot.is_relative_to(project/'data/coordination'):raise ValueError('isolated snapshot required')
    root=project/'data/coordination/p5'/('audio-browser-'+uuid.uuid4().hex);root.mkdir(parents=True,mode=0o700)
    os.environ.update({'SHANA_TTS_PROVIDER':'qwen-tts','SHANA_TTS_PROFILE':args.profile,
        'SHANA_STT_PROVIDER':'faster-whisper','SHANA_STT_MODEL':'base.en','SHANA_STT_DEVICE':'cpu',
        'SHANA_STT_COMPUTE_TYPE':'int8','SHANA_AUDIO_OUTPUT_DIR':str(root/'provider-audio')})
    from gamma.coordination.transfer import restore_snapshot,export_snapshot
    from gamma.voice.tts import TTSService
    restore_snapshot(snapshot,root/'state')
    before_models=catalog(args.endpoint);sources=source_files(project);checkers=checker_files(project)
    report={'kind':'p5_actual_local_audio_browser','complete':False,'production_activated':False,
        'runtime_source':sources,'checker_source':checkers,'model':args.model,'voice_profile':args.profile,
        'human_listening_assessed':False,'physical_microphone_assessed':False,'discord_connected':False}
    def save():(root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    host=None;started=time.monotonic()
    try:
        source_text='Shana, give me two short sentences about why a break can help.'
        source=TTSService().synthesize(source_text,emotion='neutral')
        data=Path(source.audio_path).read_bytes();(root/'controlled-input.wav').write_bytes(data)
        report['controlled_input']={'text':source_text,'sha256':hashlib.sha256(data).hexdigest(),
            'provider':source.provider,'scope':'synthetic declared private source, not voice authentication'}
        host=Host(project,root/'state',args.model,args.endpoint)
        host.child_script=project/'scripts/private_interaction_probe_support.py';host.start()
        report['host_pid']=host.process.pid
        node=subprocess.run(['node',str(project/'scripts/private_presence_real_audio_probe.cjs')],
            input=json.dumps({'url':host.ready['dashboard'],'username':'isolated-owner','password':host.password,
                'audio_base64':base64.b64encode(data).decode(),'screenshot':str(root/'presence.png')}),
            text=True,capture_output=True,timeout=200)
        (root/'browser-stderr.log').write_text(node.stderr)
        if node.stdout:(root/'browser-report.json').write_text(node.stdout+'\n')
        assert node.returncode==0,node.stderr
        report['browser']=json.loads(node.stdout)
        assert report['browser']['complete']
        report['runtime_stable']=sources==source_files(project)
        report['checker_stable']=checkers==checker_files(project)
        assert report['runtime_stable'] and report['checker_stable']
        timings=root/'state/provider-timings.jsonl'
        report['provider_timings']=[json.loads(row) for row in timings.read_text().splitlines()]
        used={row['model'] for row in report['provider_timings']}
        after_models=catalog(args.endpoint)
        report['model_binding']={name:{'before':before_models.get(name),'after':after_models.get(name)} for name in used}
        assert all(value['before'] and value['before']==value['after'] for value in report['model_binding'].values())
        report['complete']=True
    finally:
        if host:host.stop();report['host_absent']=host.process.poll() is not None
        report['seconds']=round(time.monotonic()-started,3);save()
    with sqlite3.connect((root/'state/coordination.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        raw=db.execute('SELECT body FROM records WHERE kind=? AND id=?',('turn',report['browser']['request_id'])).fetchone()
        turn=json.loads(raw[0]);report['actual_stt_transcript']=turn['text']
        report['accepted_final_text']=turn['response']['display_text']
        report['identity_id']=json.loads(db.execute('SELECT body FROM records WHERE kind=? AND id=?',('personal_meta','state')).fetchone()[0])['identity_id']
    manifest=export_snapshot(root/'state',root/'final-snapshot')
    report['snapshot']={'records':manifest['record_count'],'operational_delivery_invalidated':manifest['operational_delivery_invalidated']}
    save();print(json.dumps({'report':str(root/'report.json'),'complete':report['complete'],
        'first_audio_playing_ms':report['browser']['first_audio_playing_ms'],'seconds':report['seconds']}))


if __name__=='__main__':main()
