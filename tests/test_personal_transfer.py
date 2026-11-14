"""Current-journal snapshot integrity and safe legacy reader projection."""
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from gamma.coordination.core import Core
from gamma.coordination.store import Store
from gamma.coordination.transfer import export_snapshot, restore_snapshot, project_legacy, prepare_p1_source, identity_lease, validate_manifest_entries
from gamma.coding.durable_practice import PracticeRunner
from gamma.schemas.response import AssistantResponse


def make(root):
    store=Store(root)
    return Core(store,draft=lambda *_:AssistantResponse(display_text='Recorded.',delivery_mode='text_only'),
        runner=PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}))


def test_snapshot_round_trip_current_journal_and_artifacts(tmp_path):
    core=make(tmp_path/'state')
    core.input('A fictional postmigration experience.','fiction','experience')
    core.personal_control('edit','identity',{'fields':{'description':'Current authored settings.'}})
    ref=core.runner.artifacts.put({'files':{'solution.py':'fictional_source'}})
    identity=core.personal_state()['identity_id']
    with pytest.raises(ValueError,match='close_owner'):
        export_snapshot(tmp_path/'state',tmp_path/'active-copy')
    core.close()
    manifest=export_snapshot(tmp_path/'state',tmp_path/'bundle')
    assert manifest['identity_id']==identity
    assert manifest['files']['artifacts/'+ref+'.json']==ref
    restore_snapshot(tmp_path/'bundle',tmp_path/'restore')
    assert Store(tmp_path/'restore').read('turn','experience')==Store(tmp_path/'state').read('turn','experience')
    assert (tmp_path/'restore'/'artifacts'/(ref+'.json')).read_bytes()==(tmp_path/'state'/'artifacts'/(ref+'.json')).read_bytes()
    with pytest.raises(ValueError,match='new_external'):
        restore_snapshot(tmp_path/'bundle',tmp_path/'restore')


def test_snapshot_rejects_tampering_and_extra_files_before_restore(tmp_path):
    core=make(tmp_path/'state'); core.close()
    export_snapshot(tmp_path/'state',tmp_path/'bundle')
    (tmp_path/'bundle'/'extra.txt').write_text('not a manifest file')
    with pytest.raises(ValueError,match='file_set'):
        restore_snapshot(tmp_path/'bundle',tmp_path/'restore')
    assert not (tmp_path/'restore').exists()
    (tmp_path/'bundle'/'extra.txt').unlink()
    with (tmp_path/'bundle'/'coordination.sqlite3').open('ab') as stream:
        stream.write(b'tampered')
    with pytest.raises(ValueError,match='hash'):
        restore_snapshot(tmp_path/'bundle',tmp_path/'restore')
    assert not (tmp_path/'restore').exists()


def test_snapshot_refuses_missing_practice_artifact(tmp_path):
    core=make(tmp_path/'state')
    activity=core.control('practice','practice',exercise_id='retry_budget')['activity']
    core.close()
    (tmp_path/'state'/'artifacts'/(activity['checkpoint_ref']+'.json')).unlink()
    with pytest.raises(ValueError,match='reference_missing'):
        export_snapshot(tmp_path/'state',tmp_path/'bundle')
    assert not (tmp_path/'bundle'/'manifest.json').exists()


@pytest.mark.parametrize('evidence_key', ['plan_ref', 'research_ref', 'candidate_source_ref'])
def test_snapshot_rejects_missing_nested_practical_evidence(tmp_path, evidence_key):
    core=make(tmp_path/'state')
    evidence=core.runner.artifacts.put({'selected_sources':[], 'summary':'Fictional task evidence.'})
    checkpoint=core.runner.artifacts.put({evidence_key:evidence, 'stage':'candidate_saved'})
    with core.store.transaction(core.token) as db:
        core.store.put(db,'run','work-fiction',{'id':'work-fiction','kind':'practical','checkpoint_ref':checkpoint})
    core.close()
    (tmp_path/'state'/'artifacts'/(evidence+'.json')).unlink()
    with pytest.raises(ValueError,match='reference_missing'):
        export_snapshot(tmp_path/'state',tmp_path/'bundle')
    assert not (tmp_path/'bundle'/'manifest.json').exists()


def test_snapshot_preserves_new_practical_work_and_nested_evidence(tmp_path):
    core=make(tmp_path/'state')
    core.input('Keep the unfinished fictional inventory task.','work-session','postmigration')
    source=core.runner.artifacts.put({'files':{'inventory.py':'print("fictional inventory")'}})
    research=core.runner.artifacts.put({'sources':[{'source_ref':source}],'notes':['Selected source only.']})
    plan=core.runner.artifacts.put({'steps':['Inspect source','Prepare candidate','Check actual CSV flow']})
    checkpoint=core.runner.artifacts.put({'source_ref':source,'original_source_ref':source,
        'research_ref':research,'plan_ref':plan,'stage':'candidate_saved','attempt_count':2})
    run={'id':'work-fiction','kind':'practical','checkpoint_ref':checkpoint,
         'initial_checkpoint_ref':checkpoint,'expires_at':1234567,'maximum_attempts':3,
         'status':'paused','desired_state':'pause'}
    with core.store.transaction(core.token) as db:
        core.store.put(db,'run',run['id'],run)
        core.store.put(db,'activity',run['id'],{'id':run['id'],'kind':'practical',
            'worker_run':run['id'],'checkpoint_ref':checkpoint,'status':'paused'})
    core.close()
    manifest=export_snapshot(tmp_path/'state',tmp_path/'bundle')
    restore_snapshot(tmp_path/'bundle',tmp_path/'restored')
    restored=Store(tmp_path/'restored')
    assert restored.read('run',run['id'])==run
    assert restored.read('turn','postmigration')==Store(tmp_path/'state').read('turn','postmigration')
    assert manifest['files']['artifacts/'+research+'.json']==research
    assert manifest['files']['artifacts/'+plan+'.json']==plan
    assert (tmp_path/'restored'/'artifacts'/(checkpoint+'.json')).read_bytes()==(tmp_path/'state'/'artifacts'/(checkpoint+'.json')).read_bytes()


@pytest.mark.parametrize('evidence_key', ['plan_ref', 'research_ref', 'candidate_source_ref'])
def test_restore_rejects_rehashed_manifest_with_missing_practical_evidence(tmp_path,evidence_key):
    core=make(tmp_path/'state')
    evidence=core.runner.artifacts.put({'notes':['Fictional source-bound evidence.']})
    checkpoint=core.runner.artifacts.put({evidence_key:evidence})
    with core.store.transaction(core.token) as db:
        core.store.put(db,'activity','work-fiction',{'checkpoint_ref':checkpoint,'kind':'practical'})
    core.close()
    export_snapshot(tmp_path/'state',tmp_path/'bundle')
    evidence_name='artifacts/'+evidence+'.json'
    (tmp_path/'bundle'/evidence_name).unlink()
    manifest_path=tmp_path/'bundle'/'manifest.json'
    manifest=json.loads(manifest_path.read_text())
    del manifest['files'][evidence_name]
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='reference_missing'):
        restore_snapshot(tmp_path/'bundle',tmp_path/'restored')
    assert not (tmp_path/'restored').exists()


def test_manifest_worker_edge_cases_reject_noncanonical_hashes_and_paths():
    valid={'coordination.sqlite3':'a'*64,'artifacts/'+'b'*64+'.json':'b'*64}
    assert validate_manifest_entries(valid)==sorted(valid)
    for name,value in [('coordination.sqlite3','a'*64+'\n'),('../coordination.sqlite3','a'*64),
        ('/coordination.sqlite3','a'*64),('artifacts/'+'b'*64+'.json/','b'*64),
        ('artifacts\\'+'b'*64+'.json','b'*64),('artifacts/'+'b'*64+'.json','c'*64),
        (1,'a'*64),('manifest.json','b'*64)]:
        with pytest.raises(ValueError):
            validate_manifest_entries({'coordination.sqlite3':'a'*64,name:value})


@pytest.mark.parametrize('evidence_key', ['evaluation_ref','frozen_ref','evidence_ref','baseline_ref','candidate_ref'])
def test_growth_snapshot_rejects_missing_deep_evidence(tmp_path,evidence_key):
    core=make(tmp_path/'state')
    evidence=core.runner.artifacts.put({'kind':'growth_case_response','response':{'display_text':'Fictional evidence.'}})
    frozen=core.runner.artifacts.put({'kind':'growth_frozen_cases','case':{evidence_key:evidence}})
    with core.store.transaction(core.token) as db:
        core.store.put(db,'growth_evaluation','evaluation-fiction',{'id':'evaluation-fiction','frozen_ref':frozen})
    core.close()
    (tmp_path/'state'/'artifacts'/(evidence+'.json')).unlink()
    with pytest.raises(ValueError,match='reference_missing'):
        export_snapshot(tmp_path/'state',tmp_path/'bundle')
    assert not (tmp_path/'bundle'/'manifest.json').exists()


def test_growth_snapshot_preserves_current_learning_and_all_nested_evidence(tmp_path):
    core=make(tmp_path/'state')
    core.input('The fictional missing input was supplied later.','growth-session','growth-experience')
    baseline=core.runner.artifacts.put({'kind':'growth_case_response','phase':'baseline','passed':False})
    candidate=core.runner.artifacts.put({'kind':'growth_case_response','phase':'candidate','passed':True})
    frozen=core.runner.artifacts.put({'kind':'growth_frozen_cases','cases':[{'id':'new-family-case'}]})
    comparison=core.runner.artifacts.put({'kind':'growth_comparison','frozen_ref':frozen,
        'cases':[{'baseline_ref':baseline,'candidate_ref':candidate}]})
    trial={'id':'trial-fiction','revision':2,'state':'accepted','expires_at':1234567,
        'evaluation_ref':comparison,'frozen_ref':frozen}
    with core.store.transaction(core.token) as db:
        core.store.put(db,'growth_trial',trial['id'],trial)
        core.store.put(db,'growth_hypothesis','hyp-fiction',{'id':'hyp-fiction','status':'revised',
            'alternatives':['An isolated failure does not generalize.'],'contradicting_source_ids':['source-fiction']})
    core.close()
    manifest=export_snapshot(tmp_path/'state',tmp_path/'bundle')
    restore_snapshot(tmp_path/'bundle',tmp_path/'restored')
    restored=Store(tmp_path/'restored')
    assert restored.read('growth_trial',trial['id'])==trial
    assert restored.read('growth_hypothesis','hyp-fiction')==Store(tmp_path/'state').read('growth_hypothesis','hyp-fiction')
    assert restored.read('turn','growth-experience')==Store(tmp_path/'state').read('turn','growth-experience')
    for ref in (baseline,candidate,frozen,comparison):
        assert manifest['files']['artifacts/'+ref+'.json']==ref
        assert (tmp_path/'restored'/'artifacts'/(ref+'.json')).read_bytes()==(tmp_path/'state'/'artifacts'/(ref+'.json')).read_bytes()


def test_growth_restore_rejects_rewritten_manifest_omitting_nested_evidence(tmp_path):
    core=make(tmp_path/'state')
    evidence=core.runner.artifacts.put({'kind':'growth_case_response','phase':'candidate'})
    comparison=core.runner.artifacts.put({'kind':'growth_comparison','candidate_ref':evidence})
    with core.store.transaction(core.token) as db:
        core.store.put(db,'growth_trial','trial-fiction',{'id':'trial-fiction','evaluation_ref':comparison})
    core.close();export_snapshot(tmp_path/'state',tmp_path/'bundle')
    name='artifacts/'+evidence+'.json';(tmp_path/'bundle'/name).unlink()
    manifest_path=tmp_path/'bundle'/'manifest.json';manifest=json.loads(manifest_path.read_text())
    del manifest['files'][name];manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='reference_missing'):
        restore_snapshot(tmp_path/'bundle',tmp_path/'restored')
    assert not (tmp_path/'restored').exists()


@pytest.mark.parametrize('kind',['growth_experience','growth_hypothesis','growth_trial',
                                 'growth_evaluation','growth_opportunity','growth_choice'])
def test_p1_projection_fences_material_growth_without_mutating_state_or_reader(tmp_path,kind):
    core=make(tmp_path/'state')
    with core.store.transaction(core.token) as db:
        core.store.put(db,kind,'growth-fiction',{'id':'growth-fiction','status':'pending'})
    core.close()
    before=(tmp_path/'state'/'coordination.sqlite3').read_bytes()
    project=tmp_path/'p1';(project/'src/gamma/persona').mkdir(parents=True);(project/'config').mkdir()
    sentinel=project/'config/persona.yaml';sentinel.write_text('Old-reader source stays unchanged.')
    files={p.relative_to(project).as_posix():p.read_bytes() for p in project.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='growth_requires_current_reader'):
        project_legacy(tmp_path/'state',project)
    assert (tmp_path/'state'/'coordination.sqlite3').read_bytes()==before
    assert {p.relative_to(project).as_posix():p.read_bytes() for p in project.rglob('*') if p.is_file()}==files
    assert not (project/'data').exists()


def test_projection_current_only_no_forgotten_history(tmp_path):
    core=make(tmp_path/'state')
    mid=core.personal_control('old','memory',dict(op='remember',kind='preference',subject_id='owner',
        topic='workshop drink',text='Jasmine tea was the earlier choice.'))['id']
    core.personal_control('new','memory',dict(op='correct',target_id=mid,kind='preference',text='Mint tea is the current choice.'))
    secret=core.personal_control('secret','memory',dict(op='remember',kind='fact',subject_id='owner',
        topic='removed marker',text='scarlet-cosmos-1384'))['id']
    core.personal_control('forget','forget',{'target_id':secret})
    core.personal_control('edit','identity',{'fields':{'description':'An authored current setting.'}})
    core.close()
    project=tmp_path/'p1'
    (project/'src/gamma/persona').mkdir(parents=True)
    (project/'config').mkdir()
    report=project_legacy(tmp_path/'state',project)
    assert report['projected_current_records']==1
    assert not report['original_legacy_payload_copied']
    with sqlite3.connect(project/'data/memory/gamma.db') as db:
        facts=db.execute('SELECT fact_text,attribution FROM profilefact').fetchall()
    assert len(facts)==1 and facts[0][0]=='Mint tea is the current choice.'
    assert json.loads(facts[0][1])['canonical_id']==mid
    assert 'scarlet-cosmos-1384' not in json.dumps(facts)
    assert 'Jasmine tea' not in json.dumps(facts)
    assert json.loads((project/'config/persona.yaml').read_text())['description']=='An authored current setting.'
    with pytest.raises(ValueError,match='fresh_data'):
        project_legacy(tmp_path/'state',project)


@pytest.mark.parametrize('record_kind',['run','activity'])
def test_legacy_projection_rejects_practical_state_before_writing_old_reader(tmp_path,record_kind):
    core=make(tmp_path/'state')
    core.input('A fictional experience after the practical-work milestone.','private','post-p3')
    with core.store.transaction(core.token) as db:
        core.store.put(db,record_kind,'work-p3',{'id':'work-p3','kind':'practical','status':'ready'})
    core.close()
    database=tmp_path/'state'/'coordination.sqlite3'
    original_database=database.read_bytes()
    project=tmp_path/'old-source'
    (project/'src/gamma/persona').mkdir(parents=True)
    (project/'config').mkdir()
    (project/'README.md').write_text('Preserved old source.\n')
    (project/'config/persona.yaml').write_text('description: original\n')
    original_files={path.relative_to(project).as_posix():path.read_bytes()
                    for path in project.rglob('*') if path.is_file()}
    with pytest.raises(ValueError,match='practical_work_requires_current_reader'):
        project_legacy(tmp_path/'state',project)
    assert database.read_bytes()==original_database
    assert {path.relative_to(project).as_posix():path.read_bytes()
            for path in project.rglob('*') if path.is_file()}==original_files
    assert not (project/'data').exists()


def test_old_reader_wrapper_uses_same_identity_lease(tmp_path):
    core=make(tmp_path/'state')
    lock_root=Path(__file__).resolve().parents[1]/'data/coordination/identities'
    with pytest.raises(OSError):
        with identity_lease(tmp_path/'state',lock_root):
            pass
    core.close()
    with identity_lease(tmp_path/'state',lock_root):
        with pytest.raises(OSError): make(tmp_path/'state')


def test_old_source_overlay_hashes_are_checked(tmp_path):
    base=tmp_path/'base'
    (base/'src').mkdir(parents=True)
    (base/'config').mkdir()
    (base/'pyproject.toml').write_text('project')
    (base/'README.md').write_text('readme')
    content=b'genuine previous source'
    name='src/gamma/coordination/core.py'
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'files':{name:hashlib.sha256(content).hexdigest()}}))
    archive=tmp_path/'p1.zip'
    with zipfile.ZipFile(archive,'w') as z: z.writestr(name,content)
    result=prepare_p1_source(base,tmp_path/'copy',archive,manifest)
    assert result['verified_baseline_files']==1
    assert (tmp_path/'copy'/name).read_bytes()==content
    with zipfile.ZipFile(archive,'w') as z: z.writestr(name,b'changed')
    with pytest.raises(ValueError,match='hash'):
        prepare_p1_source(base,tmp_path/'tampered',archive,manifest)
