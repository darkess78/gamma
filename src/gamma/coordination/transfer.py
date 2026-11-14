"""Private portable snapshots and a sanitized projection for the P1 reader.

These operations never restore an earlier journal over a later one. Close the
owner before copying; a restored identity must acquire the normal identity lease.
"""
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import zipfile

from .store import private_path, process_lock


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_manifest_entries(files):
    """Reviewed worker validator, tightened to reject trailing-newline hashes."""
    if not isinstance(files,dict) or not files or len(files)>20000 or 'coordination.sqlite3' not in files:
        raise ValueError('invalid_snapshot_entries')
    for name,value in files.items():
        if not isinstance(name,str) or not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{64}',value):
            raise ValueError('invalid_snapshot_hash_or_name')
        if name=='coordination.sqlite3':
            continue
        match=re.fullmatch(r'artifacts/([a-f0-9]{64})\.json',name)
        if not match or match[1]!=value:
            raise ValueError('invalid_snapshot_artifact_name')
    return sorted(files)


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False), encoding='utf-8')
    path.chmod(0o600)


def _records(path):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('snapshot_database_integrity')
        return [(kind,key,json.loads(body)) for kind,key,body in db.execute('SELECT kind,id,body FROM records')]


def _closed(rows):
    brain=next(body for kind,key,body in rows if (kind,key)==('brain','state'))
    if brain.get('owner'):
        raise ValueError('close_owner_before_transfer')


def _invalidate_delivery_copy(path):
    """Keep no-replay receipts while making copied transports unavailable.

    Canonical accepted turns remain unchanged. Audio and connection leases are
    operational state and cannot be resumed on a different owner or device.
    """
    changed=False
    with closing(sqlite3.connect(path)) as db, db:
        from .shared_activity import invalidate_snapshot as invalidate_shared
        from .stream_session import invalidate_snapshot as invalidate_stream
        from .phone import invalidate_snapshot as invalidate_phone
        existing = {row[0] for row in db.execute('SELECT DISTINCT kind FROM records')}
        for kind in ('maintenance_backup','maintenance_restore'):
            for key,raw in db.execute('SELECT id,body FROM records WHERE kind=?',(kind,)).fetchall():
                value=json.loads(raw)
                value.update(original_status=value.get('status'), status='historical_copy', local_available=False)
                db.execute('UPDATE records SET body=? WHERE kind=? AND id=?',(json.dumps(value),kind,key))
                changed=True
        if existing & {'shared_meta','shared_observation','shared_reaction','shared_command'}:
            invalidate_shared(db)
            changed=True
        if any(kind.startswith('stream_') for kind in existing):
            invalidate_stream(db)
            changed=True
        if any(kind.startswith('phone_') for kind in existing):
            invalidate_phone(db)
            changed=True
        if any(kind.startswith('channel_audio_') for kind in existing):
            from .channel_audio import invalidate_snapshot as invalidate_channel_audio
            invalidate_channel_audio(db)
            changed=True
        for key,raw in db.execute("SELECT id,body FROM records WHERE kind='channel_review'").fetchall():
            value=json.loads(raw)
            value.update(status='cancelled',response=None,expires_at=0)
            db.execute("UPDATE records SET body=? WHERE kind='channel_review' AND id=?",(json.dumps(value),key))
            changed=True
        for kind,key,raw in db.execute("SELECT kind,id,body FROM records WHERE kind LIKE 'interaction_%'").fetchall():
            value=json.loads(raw)
            if kind=='interaction_meta':
                value.update(voice_enabled=False,speech_enabled=False,
                             output_epoch=value.get('output_epoch',0)+1)
            elif kind=='interaction_consumer':
                value.update(connected=False,expires_at=0)
            elif kind=='interaction_output':
                value.update(status='cancelled',reason='snapshot_transport_not_resumable',display_text='',
                             speech_text='',audio_available=False,expires_at=0)
            elif kind=='interaction_request':
                value['receipt']={'ok':True,'status':'cancelled','reason':'snapshot_no_replay','output_id':None}
            else:
                raise ValueError('unknown_interaction_snapshot_record')
            db.execute('UPDATE records SET body=? WHERE kind=? AND id=?',
                (json.dumps(value,ensure_ascii=False,allow_nan=False),kind,key))
            changed=True
    return changed


def _artifact_references(rows, directory):
    """A copied journal must never point at missing immutable work evidence."""
    seen=set()
    def inspect(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key in {'checkpoint_ref','initial_checkpoint_ref','source_ref','original_source_ref',
                           'result_ref','plan_ref','research_ref','candidate_source_ref',
                           'evaluation_ref','frozen_ref','evidence_ref','baseline_ref','candidate_ref'} and item:
                    if not isinstance(item,str) or not re.fullmatch(r'[a-f0-9]{64}',item):
                        raise ValueError('invalid_snapshot_artifact_reference')
                    verify(item)
                else:
                    inspect(item)
        elif isinstance(value,list):
            for item in value: inspect(item)
    def verify(ref):
        if ref in seen: return
        seen.add(ref)
        path=private_path(directory/(ref+'.json'))
        if not path.is_file() or _hash(path)!=ref:
            raise ValueError('snapshot_artifact_reference_missing_or_changed')
        inspect(json.loads(path.read_text(encoding='utf-8')))
    for kind,_key,body in rows:
        if kind in {'run','activity'} or kind.startswith('growth_'): inspect(body)


@contextmanager
def identity_lease(state_root, identity_lock_root):
    """P1 wrapper lease: same stable identity and same lock directory as P2."""
    rows=_records(private_path(state_root)/'coordination.sqlite3')
    meta=next(body for kind,key,body in rows if (kind,key)==('personal_meta','state'))
    identity=meta['identity_id']
    if not re.fullmatch(r'[a-f0-9]{32}',identity):
        raise ValueError('invalid_identity_id')
    with process_lock(private_path(identity_lock_root)/(identity+'.lock')):
        yield


def export_snapshot(state_root, bundle_root):
    """Copy the current closed journal and all immutable practice artifacts."""
    source,bundle=private_path(state_root),private_path(bundle_root)
    if bundle.exists() or bundle.is_relative_to(source):
        raise ValueError('snapshot_requires_new_external_directory')
    _closed(_records(source/'coordination.sqlite3'))
    bundle.mkdir(parents=True,mode=0o700)
    try:
        with closing(sqlite3.connect((source/'coordination.sqlite3').as_uri()+'?mode=ro',uri=True)) as old:
            old.execute('PRAGMA query_only=ON')
            with closing(sqlite3.connect(bundle/'coordination.sqlite3')) as new, new:
                old.backup(new)
        (bundle/'coordination.sqlite3').chmod(0o600)
        delivery_invalidated=_invalidate_delivery_copy(bundle/'coordination.sqlite3')
        rows=_records(bundle/'coordination.sqlite3')
        _closed(rows)
        artifacts=source/'artifacts'
        if artifacts.exists():
            private_path(artifacts)
            (bundle/'artifacts').mkdir(mode=0o700)
            for artifact in artifacts.iterdir():
                private_path(artifact)
                if not artifact.is_file() or not re.fullmatch(r'[a-f0-9]{64}\.json',artifact.name):
                    raise ValueError('unexpected_artifact_file')
                if _hash(artifact)!=artifact.stem:
                    raise ValueError('artifact_hash_mismatch')
                shutil.copyfile(artifact,bundle/'artifacts'/artifact.name)
                (bundle/'artifacts'/artifact.name).chmod(0o600)
        _artifact_references(rows,bundle/'artifacts')
        files={p.relative_to(bundle).as_posix():_hash(p) for p in bundle.rglob('*') if p.is_file()}
        meta=next(body for kind,key,body in rows if (kind,key)==('personal_meta','state'))
        manifest={'format':1,'identity_id':meta['identity_id'],'files':files,
            'record_count':len(rows),'journal_is_current':True,'contains_private_state':True}
        if delivery_invalidated:
            manifest['operational_delivery_invalidated']=True
        _json(bundle/'manifest.json',manifest)
        return manifest
    except BaseException:
        # Keep the failed copy for diagnosis; never report it as a restorable snapshot.
        (bundle/'manifest.json').unlink(missing_ok=True)
        raise


def restore_snapshot(bundle_root, target_root):
    """Validate a complete bundle before creating a new private state directory."""
    bundle,target=private_path(bundle_root),private_path(target_root)
    if target.exists() or target.is_relative_to(bundle):
        raise ValueError('restore_requires_new_external_directory')
    manifest=json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format')!=1 or not isinstance(manifest.get('files'),dict):
        raise ValueError('invalid_snapshot_manifest')
    validate_manifest_entries(manifest['files'])
    actual={p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file() and p.name!='manifest.json'}
    if actual!=set(manifest['files']) or 'coordination.sqlite3' not in actual:
        raise ValueError('snapshot_file_set_mismatch')
    for name,expected in manifest['files'].items():
        rel=PurePosixPath(name)
        if rel.is_absolute() or '..' in rel.parts or '\\' in name:
            raise ValueError('invalid_snapshot_path')
        if name!='coordination.sqlite3' and not re.fullmatch(r'artifacts/[a-f0-9]{64}\.json',name):
            raise ValueError('unexpected_snapshot_file')
        path=private_path(bundle/name)
        if _hash(path)!=expected:
            raise ValueError('snapshot_hash_mismatch')
        if name.startswith('artifacts/') and path.stem!=expected:
            raise ValueError('artifact_hash_mismatch')
    rows=_records(bundle/'coordination.sqlite3')
    _closed(rows)
    meta=next(body for kind,key,body in rows if (kind,key)==('personal_meta','state'))
    if meta['identity_id']!=manifest.get('identity_id') or len(rows)!=manifest.get('record_count'):
        raise ValueError('snapshot_identity_or_count_mismatch')
    _artifact_references(rows,bundle/'artifacts')
    target.mkdir(parents=True,mode=0o700)
    for name in manifest['files']:
        dest=target/name
        dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        shutil.copyfile(bundle/name,dest)
        dest.chmod(0o600)
        if _hash(private_path(dest)) != manifest['files'][name]:
            raise ValueError('restored_copy_hash_mismatch')
    # Verify the actual destination after copying, including dependency closure.
    # A valid source check before a copy does not bind bytes changed during I/O.
    restored_rows=_records(target/'coordination.sqlite3')
    _closed(restored_rows)
    _artifact_references(restored_rows,target/'artifacts')
    return manifest


def prepare_p1_source(project_root, target_root, baseline_archive, baseline_manifest):
    """Assemble portable current base plus the exact reviewed P1 overlay."""
    project,target=private_path(project_root),private_path(target_root)
    if target.exists():
        raise ValueError('p1_source_requires_new_directory')
    target.mkdir(parents=True,mode=0o700)
    for name in ('src','config'):
        for path in (project/name).rglob('*'):
            private_path(path)
        shutil.copytree(project/name,target/name,ignore=shutil.ignore_patterns(
            '__pycache__','*.pyc','app.local.toml','voices.local.toml'))
    for name in ('pyproject.toml','README.md'):
        shutil.copyfile(project/name,target/name)
    expected=json.loads(Path(baseline_manifest).read_text(encoding='utf-8'))['files']
    with zipfile.ZipFile(baseline_archive) as archive:
        if set(archive.namelist())!=set(expected):
            raise ValueError('p1_archive_file_set_mismatch')
        for name in archive.namelist():
            rel=PurePosixPath(name)
            if rel.is_absolute() or '..' in rel.parts or '\\' in name:
                raise ValueError('invalid_p1_archive_path')
            content=archive.read(name)
            if hashlib.sha256(content).hexdigest()!=expected[name]:
                raise ValueError('p1_archive_hash_mismatch')
            path=target/name
            if not path.is_relative_to(target):
                raise ValueError('invalid_p1_archive_path')
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(content)
    if any((target/name).exists() for name in ('.env','config/app.local.toml','config/voices.local.toml')):
        raise ValueError('p1_projection_contains_local_configuration')
    return {'verified_baseline_files':len(expected),'core_sha256':expected['src/gamma/coordination/core.py']}


def project_legacy(state_root, project_root):
    """Replace legacy read surfaces in a NEW P1 workspace with current accepted state.

    Original imported SQLite/JSON is never copied here. Unknown or pending records
    stay in the canonical journal and cannot become old-reader current facts.
    """
    root,project=private_path(state_root),private_path(project_root)
    if (project/'data').exists():
        raise ValueError('legacy_projection_requires_fresh_data_directory')
    rows=_records(root/'coordination.sqlite3')
    _closed(rows)
    if any(kind in {'run','activity'} and body.get('kind')=='practical' for kind,_key,body in rows):
        raise ValueError('practical_work_requires_current_reader')
    if any(kind in {'growth_experience','growth_hypothesis','growth_trial','growth_evaluation',
                    'growth_opportunity','growth_choice'} for kind,_key,_body in rows):
        raise ValueError('growth_requires_current_reader')
    if any(kind.startswith(('shared_','stream_','phone_')) for kind,_key,_body in rows):
        raise ValueError('connected_channels_require_current_reader')
    lookup={(kind,key):body for kind,key,body in rows}
    identity=lookup[('personal_identity','self')]
    brain=lookup[('brain','state')]
    data=project/'data'
    data.mkdir(mode=0o700)
    _json(project/'config'/'persona.yaml',identity['fields'])  # JSON is valid YAML.
    _json(project/'src'/'gamma'/'persona'/'relationship_state.json',{})
    _json(project/'src'/'gamma'/'persona'/'style.json',{})
    (project/'src'/'gamma'/'persona'/'core.md').write_text(
        'Current authored character settings, not a remembered experience.\n'+
        json.dumps(identity['fields'],ensure_ascii=False),encoding='utf-8')
    (data/'core_memories.md').write_text('',encoding='utf-8')
    mood=brain.get('mood','neutral')
    allowed={'neutral','happy','teasing','concerned','excited','embarrassed','annoyed'}
    mood=mood if mood in allowed else 'neutral'
    _json(data/'assistant_state.json',{'current_emotion':mood,'recent_emotions':[mood],'notes':[]})
    _json(data/'assistant_emotion_memory.json',{'state':{'current_emotion':mood,
        'intensity':brain.get('mood_intensity',0),'recent_emotions':[mood],'notes':[]},'episodes':[],'patterns':[]})
    path=data/'memory'/'gamma.db'
    path.parent.mkdir(mode=0o700)
    count=0
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE profilefact (id INTEGER PRIMARY KEY,category VARCHAR NOT NULL,fact_text VARCHAR NOT NULL,'
            'confidence FLOAT NOT NULL,importance FLOAT NOT NULL,subject_type VARCHAR NOT NULL,subject_name VARCHAR,'
            'relationship_to_user VARCHAR,attribution VARCHAR,created_at DATETIME NOT NULL)')
        for kind,key,record in rows:
            if kind!='personal_record' or record.get('status')!='active' or record.get('scope')!='owner_private':
                continue
            source=lookup.get(('personal_source',record.get('source_id')),{})
            if source.get('status') not in {'active','administrative','pruned'}:
                continue
            subject=record.get('subject_id')
            person=lookup.get(('person',subject),{})
            if subject=='owner':
                subject_type,subject_name='primary_user',None
            elif subject=='self':
                subject_type,subject_name='other_person',identity['fields'].get('name','Shana')
            elif person.get('status')=='active':
                subject_type,subject_name='other_person',person['name']
            else:
                continue
            confidence=record.get('confidence',.5)
            if type(confidence) not in {int,float} or not math.isfinite(confidence):
                confidence=.5
            provenance={'kind':'manual' if record.get('certainty')=='authored' else 'reported',
                'source':'p2_current_projection','privacy_scope':'local_private','canonical_id':key,
                'canonical_source_id':record['source_id'],'exact_source_retained':source['status']!='pruned'}
            timestamp=record.get('updated_at')
            if type(timestamp) not in {int,float} or not math.isfinite(timestamp):
                timestamp=0
            created=datetime.fromtimestamp(timestamp,timezone.utc).isoformat()
            db.execute('INSERT INTO profilefact VALUES(?,?,?,?,?,?,?,?,?,?)',(None,record.get('kind','fact'),record['text'],
                min(1,max(0,confidence)),.5,subject_type,subject_name,person.get('relationship'),json.dumps(provenance),created))
            count+=1
    path.chmod(0o600)
    return {'projected_current_records':count,'original_legacy_payload_copied':False,
        'identity_revision':identity['revision'],'source_kind':'canonical_current_projection'}
