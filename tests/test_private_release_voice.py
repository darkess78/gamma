"""Strict typed release voice options and actual stock-profile H factories."""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import select
import shutil
import subprocess
import sys
from typing import get_args
import uuid

import httpx
import pytest

from gamma.coordination.private_release_voice import validate_voice_extra
from gamma.coordination import private_release_manifest as contract
from gamma.coordination.private_release import release_api_bearer
from gamma.coordination.store import Store
from gamma.coordination.transfer import export_snapshot
from gamma.schemas.response import EmotionTag
from test_private_release import core


EMOTIONS = get_args(EmotionTag)


def test_current_canonical_emotion_tags_are_the_only_map_labels():
    values = {emotion: .84 for emotion in EMOTIONS}; values['default'] = .84
    assert validate_voice_extra({'speed_by_emotion': values}, EMOTIONS) is None
    assert len(values) == 8
    with pytest.raises(ValueError): validate_voice_extra({'speed_by_emotion': {'other': .84}}, EMOTIONS)


@pytest.mark.parametrize('value', [None, [], '', {'api_key':'secret'}, {'unknown':1},
    {'speed': True}, {'temperature': False}, {'temperature': math.nan}, {'top_p': math.inf}, {'top_p':0},
    {'max_new_tokens':2049}, {'max_new_tokens':True}, {'max_new_tokens':0}, {'min_new_tokens':-1},
    {'min_new_tokens':101,'max_new_tokens':100}, {'speed':.74}, {'speed':1.21}, {'output_peak':.96},
    {'seed':-1}, {'seed':2**32}, {'top_k':1001}, {'x_vector_only_mode':1}, {'do_sample':'false'},
    {'repetition_penalty':None}, {'repetition_penalty':.49}, {'speed_by_emotion':{}},
    {'speed_by_emotion':{'default':True}}, {'speed_by_emotion':{'default':{'secret':'x'}}},
    {'speed_by_emotion':{'default':math.nan}}, {'speed_by_emotion':{'default':1.21}},
    {'output_peak_by_emotion':{'default':.79}}, {'output_peak_by_emotion':{'neutral':'0.58'}},
    {'speed_by_emotion':{'secret':'x'}}, {'output_peak_by_emotion':[]},
])
def test_rejects_unknown_secret_malformed_nonfinite_and_resource_options(value):
    with pytest.raises(ValueError): validate_voice_extra(value, EMOTIONS)


def test_valid_boundary_options_are_not_coerced_or_mutated():
    value={'seed':0,'top_k':1000,'max_new_tokens':2048,'min_new_tokens':100,'temperature':0.,
        'top_p':1.,'repetition_penalty':.5,'speed':.75,'output_peak':.35,'do_sample':False,
        'x_vector_only_mode':True,'speed_by_emotion':{'default':1.2},'output_peak_by_emotion':{'neutral':.78}}
    original=deepcopy(value);assert validate_voice_extra(value, EMOTIONS) is None;assert value==original


@pytest.mark.parametrize('profile', ['qwen_shana_dataset_xvec_soft','qwen_shana_dataset_ref_soft'])
def test_actual_stock_profile_manifest_and_source_bound_factory(tmp_path, profile):
    if os.name!='posix':pytest.skip('Actual factory proof runs on Gamma POSIX.')
    project=Path(__file__).resolve().parents[1]
    reference=project/'assets/voice_references/shana/shana_dataset_ref_22s.wav'
    if not reference.is_file():pytest.skip('Known existing Gamma reference is not installed; no download.')
    expected='b00ed74629d994d3b2d85185476a61827cb8ce665a6502c5a2b29c4265b1b3d5'
    assert not reference.is_symlink() and hashlib.sha256(reference.read_bytes()).hexdigest()==expected
    identifier=uuid.uuid4().hex
    a=tmp_path/('private-release-test-'+identifier+'-a');b=tmp_path/('private-release-test-'+identifier+'-b')
    inventory=contract.source_inventory(project)
    for root in (a,b):
        root.mkdir(mode=0o700)
        for name in inventory:
            target=root/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700);shutil.copyfile(project/name,target)
        for name in ('scripts/private_release.py','scripts/verify_private_release.py'):
            target=root/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700);shutil.copyfile(project/name,target)
        target=root/reference.relative_to(project);target.parent.mkdir(parents=True,mode=0o700);shutil.copyfile(reference,target);target.chmod(0o600)
    state=a/'data/coordination/state';owner=core(state);owner.close()
    baseline=tmp_path/'baseline';export_snapshot(state,baseline)
    manifest_path=tmp_path/'manifest.json'
    environment={**os.environ,'SHANA_PROJECT_ROOT':str(a),'PYTHONPATH':str(a/'src'),'PYTHONDONTWRITEBYTECODE':'1','TMPDIR':'/tmp'}
    prepared=subprocess.run([sys.executable,str(a/'scripts/private_release.py'),'prepare','--source',str(a),'--state',str(state),
        '--baseline',str(baseline),'--reader',str(b),'--lock-root',str(tmp_path/'shared-locks'),
        '--original-lock-root',str(tmp_path/'compat-locks'),'--model','qwen3.8:27b','--voice',profile,'--output',str(manifest_path)],
        cwd=a,env=environment,capture_output=True,text=True,timeout=30)
    assert prepared.returncode==0,prepared.stdout+prepared.stderr
    manifest=json.loads(manifest_path.read_text());contract.validate_manifest(manifest)
    assert manifest['models']['voice']==profile and manifest['models']['voice_pin']['reference_audio_sha256']==expected
    assert manifest['models']['voice_pin']['provider']=='qwen-tts'
    control=tmp_path/'control';control.mkdir(mode=0o700)
    credentials={key:secrets.token_urlsafe(32)for key in ('api_token','password','session_secret')}
    argv=[sys.executable,str(a/'scripts/verify_private_release.py'),'--child','--manifest',str(manifest_path),'--control',str(control)]
    environment['GAMMA_PRIVATE_RELEASE_TEST_TRIPWIRE']='http://127.0.0.1:9'
    errors=(control/'stderr.log').open('w')
    process=subprocess.Popen(argv,cwd=a,env=environment,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,text=True)
    received=[]
    try:
        process.stdin.write(json.dumps(credentials)+'\n');process.stdin.flush()
        assert select.select([process.stdout],[],[],30)[0], 'Actual factory did not become ready.'
        line=process.stdout.readline();received.append(line)
        assert line, (control/'stderr.log').read_text()
        ready=json.loads(line);assert ready['phase']=='ready' and ready['pid']==process.pid
        token=release_api_bearer(manifest,'test',credentials['api_token'])
        with httpx.Client(base_url=ready['api'],trust_env=False,timeout=10) as client:
            assert client.get('/v1/coordination/private-release').status_code==401
            client.headers['Authorization']='Bearer '+token
            status=client.get('/v1/coordination/private-release');assert status.status_code==200
            assert status.json()['identity_id']==manifest['state']['identity_id']
            response=client.post('/v1/coordination/input',json={'audience':'owner_private','request_id':'stock_voice_factory',
                'session_id':'authored-stock-voice','text':'A controlled stock-profile factory check.'})
            assert response.status_code==200,response.text
            assert 'Controlled fixture reply' in response.json()['result']['assistant_response']['display_text']
        process.stdin.write('stop\n');process.stdin.flush();process.wait(timeout=20)
        assert process.returncode==0
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=10)
        process.stdin.close();received.append(process.stdout.read());process.stdout.close();errors.close()
    assert not Path('/proc',str(process.pid)).exists()
    before=Store(state).read('brain','state');assert not before['owner']
    target=a/reference.relative_to(project);target.write_bytes(target.read_bytes()+b'changed')
    rejected=subprocess.run(argv,cwd=a,env=environment,input=json.dumps(credentials)+'\nstop\n',capture_output=True,text=True,timeout=30)
    assert rejected.returncode!=0 and 'effective_release_voice_changed' in rejected.stderr
    assert Store(state).read('brain','state')==before
    proof={'profile':profile,'actual_source_factory':True,'manifest_release_id':manifest['release_id'],
        'manifest_digest':contract.digest(manifest),'voice_pin':manifest['models']['voice_pin'],
        'source_pin':manifest['source'],'factory_owned_pid':process.pid,'owned_process_absent':True,
        'authenticated_private_http':True,'reference_drift_rejected_before_owner_claim':True,
        'controlled_semantic_boundary':True,'actual_synthesis':False,'model_calls':0,'human_acceptance':False}
    (tmp_path/'proof.json').write_text(json.dumps(proof,indent=2)+'\n');(tmp_path/'proof.json').chmod(0o600)
