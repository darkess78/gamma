"""Finite real-model private streaming rehearsal through both authenticated apps.

Events are explicitly authored replay fixtures, not a real Twitch/OSRS session.
The HTTP client reads and acknowledges text. It cannot prove human listening,
public delivery, subjective co-host quality, or meaningful four-hour hosting.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid
from urllib.parse import urlsplit

from growth_probe_support import Host


FILES = ['src/gamma/coordination/'+name+'.py' for name in (
    'stream_session','stream_session_models','stream_session_policy','stream_session_routes',
    'core','channel_review','app','dashboard')]


def hashes(project):
    return {name:hashlib.sha256((project/name).read_bytes()).hexdigest() for name in FILES}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',default='qwen3.8:27b')
    parser.add_argument('--endpoint',default='http://127.0.0.1:11434')
    args=parser.parse_args()
    project=Path(__file__).resolve().parents[1]
    endpoint=urlsplit(args.endpoint)
    if (os.name!='posix' or project.name in {'gamma-main','gamma-modular-phase1'}
        or endpoint.scheme!='http' or endpoint.hostname not in {'127.0.0.1','::1'}
        or endpoint.port!=11434 or endpoint.username or endpoint.password
        or endpoint.path not in {'','/'} or endpoint.query or endpoint.fragment):
        raise RuntimeError('Run only on the isolated Gamma candidate using its local existing model.')
    root=project/'data/coordination/p7'/('connected-'+uuid.uuid4().hex)
    root.mkdir(parents=True,mode=0o700)
    report={'kind':'p7_actual_core_private_replay','complete':False,'author':'neety@openclaw',
        'source_sha256':hashes(project),'model':args.model,'formats':[],
        'synthetic_event_fixtures':True,'physical_hearing_verified':False,'real_twitch_connected':False,
        'public_output_enabled':False,'meaningful_four_hour_session_verified':False,
        'user_quality_acceptance':False}
    started=time.monotonic();host=None;stop=threading.Event();heartbeat=None;active=None
    def save(): (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    try:
        host=Host(project,root/'state',args.model,args.endpoint).start()
        report['host_pid']=host.process.pid
        prefix='/api/coordination/stream'
        def post(path,**values): return host.request('POST',prefix+path,json={'audience':'owner_private',**values})
        def keepalive():
            while not stop.wait(10):
                if active is not None:
                    try: post('/heartbeat',session_id=active,consumer_id='private-replay-client')
                    except Exception: pass
        heartbeat=threading.Thread(target=keepalive,daemon=True);heartbeat.start()
        for format_,broad_plan,question in [
            ('gameplay','Keep company during an OSRS skilling session; discuss the current task without constant coaching.',
             'Shana, what do you think makes a relaxed skilling session enjoyable?'),
            ('coding','Build and test a disposable text parser together; discuss ideas while leaving focused work quiet.',
             'Shana, which boundary cases are useful when testing a text parser?')]:
            session=post('/start',request_id=uuid.uuid4().hex,format=format_,broad_plan=broad_plan,
                         duration_seconds=900,maximum_decisions=12)
            active=session['id']
            consumer={'session_id':active,'consumer_id':'private-replay-client'}
            post('/connect',**consumer)
            row={'format':format_,'session_id':active,'cases':[]};report['formats'].append(row)
            def submit(kind,text='',**values):
                event_id=uuid.uuid4().hex
                result=post('/event',**consumer,event_id=event_id,kind=kind,text=text,
                    source='replay',observed_at=time.time(),actor_alias='ExampleViewer',**values)
                case={'kind':kind,'text':text,'result':result};row['cases'].append(case)
                if result.get('status')=='ready':
                    output=host.request('GET',prefix+'/next',params={'audience':'owner_private',**consumer})['output']
                    assert output and output['id']==result['output_id'],result
                    assert output['publication_authorized'] is False and output['audience']=='owner_private'
                    receipt=post('/ack',**consumer,output_id=output['id'],generation=output['generation'],
                                 revision=output['revision'],status='delivered',receipt_id=uuid.uuid4().hex)
                    assert receipt['status']=='delivered'
                    case['received_text']=output['text'];case['receipt']=receipt
                    assert host.request('GET',prefix+'/next',params={'audience':'owner_private',**consumer})['output'] is None
                save();return result
            first=submit('chat',question)
            assert first['status']=='ready',first
            post('/control',request_id=uuid.uuid4().hex,session_id=active,action='focus')
            assert submit('lull')['reason']=='focused_quiet'
            post('/control',request_id=uuid.uuid4().hex,session_id=active,action='owner_away')
            assert host.request('GET',prefix+'/status',params={'audience':'owner_private'})['active']['owner_present'] is False
            post('/control',request_id=uuid.uuid4().hex,session_id=active,action='relax')
            # Real elapsed time; no accelerated clock or four-hour claim.
            time.sleep(9)
            submit('lull','The owner stepped away briefly. Continue inside the selected broad plan if useful.')
            caught=post('/control',request_id=uuid.uuid4().hex,session_id=active,action='owner_return')
            assert caught['session']['owner_present'] is True and caught['catch_up']
            row['owner_return_received_catch_up']=caught['catch_up']
            post('/disconnect',**consumer);post('/connect',**consumer)
            assert host.request('GET',prefix+'/next',params={'audience':'owner_private',**consumer})['output'] is None
            post('/control',request_id=uuid.uuid4().hex,session_id=active,action='stop')
            active=None
            assert host.request('GET',prefix+'/status',params={'audience':'owner_private'})['active'] is None
        report['source_after_sha256']=hashes(project)
        report['source_stable']=report['source_sha256']==report['source_after_sha256']
        assert report['source_stable'],'Source changed during connected verification; retain receipt and rerun frozen source.'
        report['complete']=True
    except Exception as exc:
        report['error_type']=type(exc).__name__
        raise
    finally:
        stop.set()
        if heartbeat:heartbeat.join(timeout=15)
        if host:
            if active is not None:
                try: host.request('POST','/api/coordination/stream/control',json={
                    'audience':'owner_private','request_id':uuid.uuid4().hex,'session_id':active,'action':'stop'})
                except Exception: pass
            host.stop()
            report['owned_host_absent']=host.process.poll() is not None
        report['seconds']=round(time.monotonic()-started,3)
        save()
        print(json.dumps({'report':str(root.relative_to(project)/'report.json'),
            'complete':report['complete'],'seconds':report['seconds'],'owned_host_absent':report.get('owned_host_absent')}))


if __name__=='__main__':main()
