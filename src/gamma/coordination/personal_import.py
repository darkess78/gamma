"""One-way reconciliation from an owner-controlled, immutable legacy copy.

The original backup remains evidence. This importer never opens live data and
never grants action authority from names, relationship warmth or legacy trust.
"""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import sqlite3
import tempfile

from .legacy_rows import convert_legacy, _valid_key, _TABLES
from .personal import digest, identifier, IDENTITY_FIELDS
from .store import private_path


_CHECKPOINT_TABLES = (
    'rollingsessionsummary', 'workingstatecheckpoint', 'scopedworkingstatecheckpoint',
    'scopedsessionsummary', 'stream_self_goals',
)
_OPERATIONAL_TABLES = {
    'stream_temp_memory', 'durableoutputstate', 'stream_viewer_trust',
    'conversationjournalowner', 'conversationinputcursor',
    'conversationcontinuityowner', 'conversationcursor',
}


def _table_census(tables):
    """Account for every copied table without retaining unsupported row payloads."""
    census = {}
    for table, rows in sorted(tables.items()):
        if table in _TABLES:
            disposition = 'converted'
        elif table in _CHECKPOINT_TABLES:
            disposition = 'checkpoint_provenance'
        elif table in _OPERATIONAL_TABLES:
            disposition = 'operational_quarantine'
        elif table == 'sqlite_sequence':
            disposition = 'sqlite_metadata'
        else:
            disposition = 'unsupported_quarantine'
        census[table] = {'disposition': disposition,
            'row_count': len(rows) if isinstance(rows, (list, tuple)) else None,
            'payload_sha256': digest(rows)}
    return census


def reconcile(core, namespace, tables, *, auxiliary=None):
    converted=convert_legacy(namespace,tables)
    report={'sources_added':0,'records_added':0,'people_added':0,'accounts_added':0,'conflicts':0,
            'quarantined':0,'source_namespace_sha256':digest(namespace),'table_census':_table_census(tables)}
    quarantine_ids=set()
    with core.store.transaction(core.token) as db:
        p,s=core.personal,core.store
        p.invalidate(db)
        def protect(source):
            matching=[x for x in s.list(db,'personal_source',None) if x.get('legacy_ref')==source.get('legacy_ref')]
            if source.get('legacy_ref') and any(x['status'] in {'forgotten','deleted'} for x in matching):
                source={k:v for k,v in source.items() if k in {'id','source_kind','scope','legacy_ref','text_sha256',
                    'occurred_at','ingested_at','dependencies'}}
                source.update(status='forgotten',text='',assistant_text='')
            elif source.get('legacy_ref') and any(x['id']!=source['id'] for x in matching):
                source['status']='quarantined'
            return source
        for source in converted['sources']:
            source=deepcopy(source)
            source.setdefault('ingested_at',core.clock())
            source.setdefault('assistant_text','')
            source.setdefault('dependencies',{'sources':[],'memories':[]})
            source.setdefault('text_sha256',digest(source.get('text','')))
            source.setdefault('legacy_ref',{'namespace':namespace,'table':source.get('legacy_table'),'id':source.get('legacy_id')})
            source.setdefault('promoted',False)
            matching=[x for x in s.list(db,'personal_source',None) if x.get('legacy_ref')==source['legacy_ref']]
            if any(x['status'] in {'forgotten','deleted'} for x in matching):
                source={k:v for k,v in source.items() if k in {'id','source_kind','scope','legacy_ref','text_sha256',
                    'occurred_at','ingested_at','dependencies'}}
                source.update(status='forgotten',text='',assistant_text='')
            elif any(x['id']!=source['id'] for x in matching):
                source['status']='quarantined'
            old=s.get(db,'personal_source',source['id'])
            if not old:
                s.put(db,'personal_source',source['id'],source);report['sources_added']+=1
        for record in converted['records']:
            record=deepcopy(record)
            record.setdefault('history_sources',record.pop('history',[]))
            record.setdefault('created_at',core.clock())
            record.setdefault('updated_at',core.clock())
            record.setdefault('legacy_ref',{'namespace':namespace,'table':record.get('legacy_table'),'id':record.get('legacy_id')})
            old=s.get(db,'personal_record',record['id'])
            if old:
                if old.get('source_id')!=record['source_id'] and old['status'] not in {'forgotten','deleted'}:
                    # External/manual changes are proposals; never overwrite a current P2 correction.
                    qid=identifier('proposal','legacy-conflict',record['id'],record['source_id'])
                    s.put(db,'maintenance_proposal',qid,{'id':qid,'kind':'legacy_revision_conflict',
                        'target_id':record['id'],'source_id':record['source_id'],'status':'pending_owner_review'})
                    report['conflicts']+=1
                continue
            incoming_source=s.get(db,'personal_source',record['source_id'])
            if incoming_source and incoming_source['status'] in {'forgotten','deleted','quarantined'}:
                continue
            # A removed prior immutable version prevents reimport resurrection.
            removed=any(x.get('legacy_ref')==record['legacy_ref'] and x['status'] in {'forgotten','deleted'}
                        for x in s.list(db,'personal_record',None))
            if removed: continue
            s.put(db,'personal_record',record['id'],record);report['records_added']+=1
            source=s.get(db,'personal_source',record['source_id'])
            if source:
                source['promoted']=True;s.put(db,'personal_source',source['id'],source)
        for person in converted['people']:
            person=dict(person,status='active',interaction_count=0)
            source=s.get(db,'personal_source',person['source_id'])
            if not source or source['status']!='active':continue
            if not s.get(db,'person',person['id']):
                s.put(db,'person',person['id'],person);report['people_added']+=1
        for account in converted['accounts']:
            tombstone=s.get(db,'account_tombstone',identifier('account',account['platform'],account['platform_user_id']))
            if tombstone and tombstone['status']=='unlinked':
                source=s.get(db,'personal_source',account['source_id'])
                if source and source['status']=='active':
                    source['status']='quarantined';s.put(db,'personal_source',source['id'],source)
                continue
            person=s.get(db,'person',account['person_id'])
            source=s.get(db,'personal_source',account['source_id'])
            if not person or person['status']!='active' or not source or source['status']!='active':continue
            source['dependencies']['sources']=[person['source_id']]
            s.put(db,'personal_source',source['id'],source)
            existing=[a for a in s.list(db,'account',None) if a.get('status')=='active' and
                (a.get('platform'),a.get('platform_user_id'))==(account['platform'],account['platform_user_id'])]
            if any(a['person_id']!=account['person_id'] for a in existing):
                source['status']='quarantined';s.put(db,'personal_source',source['id'],source)
                report['conflicts']+=1;continue
            if existing:continue
            if not s.get(db,'account',account['id']):
                account=dict(account,status='active',confirmed=True,confirmation_kind='established_legacy_identity_row',
                    grants_authority=False)
                s.put(db,'account',account['id'],account);report['accounts_added']+=1
        for item in converted['quarantine']:
            qid=identifier('quarantine',namespace,item)
            quarantine_ids.add(qid)
            s.put(db,'personal_quarantine',qid,{'id':qid,'details':item,'status':'excluded_from_recall'})
        for table in _CHECKPOINT_TABLES:
            for row in tables.get(table,[]):
                pk=row.get('id') if isinstance(row,dict) else None
                composite=False
                if pk is None and isinstance(row,dict) and table!='stream_self_goals':
                    session=row.get('session_id')
                    scope=row.get('privacy_scope')
                    if isinstance(session,str) and _valid_key(session):
                        if table.startswith('scoped') and isinstance(scope,str) and _valid_key(scope):
                            pk=[session,scope];composite=True
                        elif not table.startswith('scoped'):pk=session
                if not isinstance(row,dict) or not (_valid_key(pk) or composite):
                    qid=identifier('quarantine',namespace,table,'missing-id',digest(row))
                    quarantine_ids.add(qid)
                    s.put(db,'personal_quarantine',qid,{'id':qid,'table':table,'reason':'missing_or_invalid_primary_key',
                        'payload_sha256':digest(row),'status':'excluded_from_recall'})
                    continue
                sid=identifier('src','legacy-aux',namespace,table,pk,digest(row))
                if s.get(db,'personal_source',sid):continue
                source={'id':sid,'source_kind':'legacy_checkpoint','origin_kind':table,'scope':'owner_private',
                    'status':'active','text':json.dumps(row,ensure_ascii=False,sort_keys=True),'assistant_text':'',
                    'occurred_at':row.get('created_at') or row.get('updated_at'),'ingested_at':core.clock(),
                    'legacy_ref':{'namespace':namespace,'table':table,'id':pk},
                    'dependencies':{'sources':[],'memories':[]},'promoted':False,'certainty':'legacy_unverified'}
                source['text_sha256']=digest(source['text']);source=protect(source)
                s.put(db,'personal_source',sid,source)
                report['sources_added']+=1
                if source['status']!='active':continue
                # Checkpoint structures are incomplete reports, not an execution permit.
                if table in {'workingstatecheckpoint','scopedworkingstatecheckpoint','stream_self_goals'}:
                    mid=identifier('mem','legacy-aux',namespace,table,pk)
                    if not s.get(db,'personal_record',mid):
                        s.put(db,'personal_record',mid,{'id':mid,'kind':'commitment','subject_id':'self',
                            'topic':'legacy unfinished checkpoint','text':source['text'][:600],'source_id':sid,
                            'source_kind':source['source_kind'],'scope':'owner_private','source_scope':'owner_private',
                            'certainty':'legacy_unverified','confidence':.3,'status':'pending','revision':1,
                            'history_sources':[],'created_at':core.clock(),'updated_at':core.clock(),
                            'legacy_ref':source['legacy_ref'],
                            'execution_authority':False})
                        report['records_added']+=1
        for relative,value in (auxiliary or {}).items():
            sid=identifier('src','legacy-file',namespace,relative,digest(value))
            if s.get(db,'personal_source',sid):continue
            text=json.dumps(value,ensure_ascii=False,sort_keys=True)
            source={'id':sid,'source_kind':'legacy_authored_edit' if relative=='config/persona.yaml' else
                'legacy_state_file','origin_kind':relative,'scope':'owner_private','status':'active','text':text,
                'text_sha256':digest(text),'assistant_text':'','occurred_at':None,'ingested_at':core.clock(),
                'dependencies':{'sources':[],'memories':[]},'promoted':False,'certainty':'legacy_unverified',
                'legacy_ref':{'namespace':namespace,'file':relative}}
            source=protect(source)
            if relative=='config/persona.yaml' and p.identity(db)['field_sources']:
                source['status']='quarantined' if source['status']=='active' else source['status']
            s.put(db,'personal_source',sid,source)
            report['sources_added']+=1
            if source['status']!='active':
                report['conflicts']+=source['status']=='quarantined'
                continue
            if relative=='config/persona.yaml' and isinstance(value,dict):
                from ..persona.disclosure import persona_view
                fields=persona_view(value,private=True)
                identity=p.identity(db)
                # Authored input is an explicit import edit, not a fabricated experienced conversation.
                eid=identifier('edit','legacy',namespace,relative,digest(value))
                s.put(db,'admin_edit',eid,{'id':eid,'source_id':sid,'kind':'legacy_authored_identity',
                    'previous_revision':identity['revision'],'fields':fields,'at':core.clock(),'lived_experience':False})
                source['dependencies']['sources']=list(set(identity['field_sources'].values()))
                s.put(db,'personal_source',sid,source)
                identity['fields'].update({k:v for k,v in fields.items() if k in IDENTITY_FIELDS})
                identity['field_sources'].update({k:sid for k in fields if k in IDENTITY_FIELDS})
                identity['revision']+=1;s.put(db,'personal_identity','self',identity)
        for table, census in report['table_census'].items():
            if census['disposition'] in {'operational_quarantine','unsupported_quarantine'} and tables[table]:
                qid=identifier('quarantine','legacy-table',namespace,table)
                quarantine_ids.add(qid)
                status='not_personal_state' if census['disposition']=='operational_quarantine' else 'unsupported_legacy_table'
                s.put(db,'personal_quarantine',qid,{'id':qid,'table':table,'row_count':census['row_count'],
                    'payload_sha256':census['payload_sha256'],'status':status,'raw_evidence':'owner_controlled_backup'})
        # Count distinct content-free references, not raw rows or SQLite metadata.
        report['quarantined']=len(quarantine_ids)
        payload_hash=hashlib.sha256(json.dumps({'tables':tables,'auxiliary':auxiliary},sort_keys=True,
            ensure_ascii=False).encode()).hexdigest()
        report['migration_id']=identifier('migration',namespace,payload_hash)
        if not s.get(db,'personal_migration',report['migration_id']):
            s.put(db,'personal_migration',report['migration_id'],dict(report,id=report['migration_id'],at=core.clock(),atomic=True,
                legacy_claims_verified=False,execution_authority_granted=False))
    return report


def reconcile_snapshot(core, snapshot_root, *, namespace='gamma-main:personal-memory'):
    """Read only a private copied manifest. Never infer the live source location."""
    root=private_path(snapshot_root)
    manifest=json.loads((root/'manifest.json').read_text())
    database=_snapshot_member(root,manifest['database']['snapshot_path'],'database')
    database_bytes=database.read_bytes()
    if hashlib.sha256(database_bytes).hexdigest()!=manifest['database']['snapshot_sha256']:
        raise ValueError('snapshot_database_hash_mismatch')
    with tempfile.TemporaryDirectory(prefix='verified-legacy-',dir=core.store.root) as temporary:
        verified=Path(temporary)/'memory.sqlite3'
        verified.write_bytes(database_bytes);verified.chmod(0o600)
        # SQLite online backups can retain a WAL-mode header in their complete
        # standalone image. A private immutable copy reads those verified bytes
        # without looking for live WAL/SHM files or rereading a changed source.
        with sqlite3.connect(verified.as_uri()+'?mode=ro&immutable=1',uri=True) as db:
            db.execute('PRAGMA query_only=ON');db.row_factory=sqlite3.Row
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('snapshot_database_integrity')
            tables={}
            for item in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                table=item[0]
                quoted='"'+table.replace('"','""')+'"'
                tables[table]=[dict(r) for r in db.execute('SELECT * FROM '+quoted)]
    auxiliary={}
    for item in manifest.get('copied_files',[]):
        relative=item['relative_path'];path=_snapshot_member(root,item['snapshot_path'],'file')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=item['sha256']:
            raise ValueError('snapshot_auxiliary_hash_mismatch')
        text=raw.decode('utf-8')
        if relative.endswith('.json'): auxiliary[relative]=json.loads(text)
        elif relative.endswith('.yaml'):
            import yaml
            auxiliary[relative]=yaml.safe_load(text)
        else: auxiliary[relative]=text
    return reconcile(core,namespace,tables,auxiliary=auxiliary)


def _snapshot_member(root, value, kind):
    """Support portable relative members and historical absolute copy manifests."""
    if not isinstance(value,str) or not value:
        raise ValueError('snapshot_member_path_invalid')
    member=Path(value)
    if not member.is_absolute() and '\\' in value:
        raise ValueError('snapshot_member_path_not_portable')
    path=private_path(member if member.is_absolute() else root/member)
    if not path.is_relative_to(root):raise ValueError('snapshot_'+kind+'_outside_copy')
    return path
