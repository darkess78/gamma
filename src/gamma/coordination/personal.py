"""Accepted personal state shares the coordinator's transaction and generation."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import uuid

from .recall import rank_records

POLICY = {'text_days': 30, 'per_session_sources': 500, 'maximum_sources': 5000,
          'maximum_records': 5000, 'mood_seconds': 1800, 'queue_seconds': 86400}
IDENTITY_FIELDS = frozenset({'name', 'description', 'primary_purpose', 'core_personality', 'temperament',
    'key_traits', 'likes', 'dislikes', 'insecurities', 'speech_style', 'emotional_expression',
    'behavior', 'character_rules', 'relationships', 'self_awareness', 'motivations',
    'behavior_rules','relationship_style','romance_guidelines','scenario_notes','first_message','example_dialogue'})
TERMINAL = {'completed', 'blocked', 'needs_checker', 'stopped'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def identifier(prefix, *parts):
    return prefix + '-' + digest(parts)[:24]


def _timestamp(value):
    if type(value) in {int, float} and math.isfinite(value):
        return float(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
        except ValueError:
            pass
    return None


class PersonalState:
    def __init__(self, store, token, *, clock, identity_seed=None):
        self.store, self.token, self.clock = store, token, clock
        with store.transaction(token) as db:
            if not store.get(db, 'personal_meta', 'state'):
                self._put(db, 'personal_meta', 'state', {'schema_version': 1, 'identity_id': uuid.uuid4().hex,
                    'policy': dict(POLICY), 'created_at': clock()})
            if not store.get(db, 'personal_identity', 'self'):
                fields = deepcopy(identity_seed or {'name': 'Shana', 'core_personality': 'Serious, blunt, quietly caring.'})
                self._put(db, 'personal_identity', 'self', {'id':'self', 'fields':fields, 'baseline_fields':deepcopy(fields),
                    'revision':1, 'field_sources':{}, 'source_kind':'authored_baseline'})
            for pid, name in [('owner', 'Owner'), ('self', 'Shana')]:
                if not store.get(db, 'person', pid):
                    self._put(db, 'person', pid, {'id':pid, 'name':name, 'relationship':'', 'notes':'',
                        'interaction_count':0, 'status':'active', 'scope':'owner_private'})
            brain = store.get(db, 'brain', 'state')
            if 'mood_updated_at' not in brain:
                brain.update(mood_updated_at=clock(), mood_intensity=0 if brain['mood']=='neutral' else .5)
                store.put(db, 'brain', 'state', brain)
            # P1 completed exchanges stay intact and become explicitly sourced archive.
            turns=store.list(db,'turn',None)
            inherited_sources=[s['id'] for s in store.list(db,'personal_source',None)
                               if s['status'] in {'active','administrative','pruned'}]
            inherited_memories=[r['id'] for r in store.list(db,'personal_record',None)
                                if r['status'] in {'active','resolved'}]
            missing=[t for t in turns if t['status']=='completed' and not t.get('redacted')
                     and not store.get(db,'personal_source',identifier('src','turn',t['id']))]
            missing_ids=[identifier('src','turn',t['id']) for t in missing]
            for turn in missing:
                if turn['status'] == 'completed' and not turn.get('redacted'):
                    sid = identifier('src', 'turn', turn['id'])
                    if not store.get(db, 'personal_source', sid):
                        source=self.receive(db, turn)
                        # Old readers cannot report exact prompt dependencies.
                        # Conservatively cover their possible canonical inputs so
                        # later forgetting cannot leave an old quoted reply behind.
                        source['dependencies']={'sources':list(dict.fromkeys(inherited_sources+
                            [other for other in missing_ids if other!=sid])), 'memories':inherited_memories}
                        source['dependency_precision']='conservative_old_reader_projection'
                        self._put(db,'personal_source',sid,source)
                        self.accept(db, turn, turn.get('response') or {}, [], {})

    def _put(self, db, kind, key, value):
        self.store.require_owner(db, self.token)
        self.store.put(db, kind, key, value)

    def meta(self, db):
        return self.store.get(db, 'personal_meta', 'state')

    def identity(self, db):
        return self.store.get(db, 'personal_identity', 'self')

    def receive(self, db, turn):
        sid = identifier('src', 'turn', turn['id'])
        previous = self.store.get(db, 'personal_source', sid)
        if previous:
            return previous
        source = {'id':sid, 'source_kind':'private_conversation', 'origin_kind':'owner_input',
            'scope':'owner_private', 'status':'pending', 'turn_id':turn['id'], 'session_id':turn['session_id'],
            'actor_person_id':'owner', 'text':turn['text'], 'assistant_text':'',
            'text_sha256':digest(turn['text']), 'occurred_at':turn.get('received_at'),
            'ingested_at':self.clock(), 'dependencies':{'memories':[], 'sources':[]}, 'promoted':False}
        self._put(db, 'personal_source', sid, source)
        return source

    def bind_context(self, db, turn, context):
        source = self.receive(db, turn)
        if source['status'] in {'forgotten', 'deleted'}:
            return
        recall = context.get('personal', {}).get('recall', [])
        source['dependencies'] = {'memories':[r['id'] for r in recall if r['id'].startswith('mem-')],
                                  'sources':list({sid for r in recall for sid in
                                      [r['source_id'], *r.get('provenance', {}).get('history_source_ids', [])]})}
        source['dependencies']['sources'] += context.get('personal', {}).get('recent_source_ids', [])
        source['dependencies']['sources'] += list(context.get('personal',{}).get('identity',{}).get('field_sources',{}).values())
        source['dependencies']['sources'] += [p['source_id'] for p in context.get('personal',{}).get('people',[]) if p.get('source_id')]
        owner = context.get('personal', {}).get('owner', {})
        source['dependencies']['sources'] += [sid for sid in
            (owner.get('source_id'), owner.get('identity_reference', {}).get('source_id')) if sid]
        for record in context.get('personal',{}).get('unfinished_and_decisions',[]):
            source['dependencies']['memories'].append(record['id'])
            source['dependencies']['sources'].append(record['source_id'])
        self._put(db, 'personal_source', source['id'], source)

    def accept(self, db, turn, response, changes, action_evidence, note_keys=()):
        source = self.receive(db, turn)
        if source['status'] in {'forgotten', 'deleted'}:
            raise ValueError('personal_source_removed')
        was_accepted = source['status'] == 'active'
        source.update(status='active', assistant_text=response.get('display_text') or '', accepted_at=self.clock())
        self._put(db, 'personal_source', source['id'], source)
        if not was_accepted:
            person = self.store.get(db, 'person', 'owner')
            person['interaction_count'] += 1
            self._put(db, 'person', 'owner', person)
        receipts = []
        for change in changes:
            values = change.model_dump() if hasattr(change, 'model_dump') else dict(change)
            try:
                receipts.append(self.change(db, values, source['id'], model_proposal=True))
            except ValueError as exc:
                receipts.append({'accepted':False, 'reason':str(exc)})
        brain = self.store.get(db, 'brain', 'state')
        origins = brain.setdefault('working_note_sources', {})
        for key in note_keys:
            if key in brain['working_notes']:
                origins[key] = source['id']
        self.store.put(db, 'brain', 'state', brain)
        return {'source_id':source['id'], 'changes':receipts}

    def change(self, db, change, source_id, *, model_proposal=False):
        self.store.require_owner(db, self.token)
        source = self.store.get(db, 'personal_source', source_id)
        if not source or source['status'] not in {'active', 'administrative'}:
            raise ValueError('source_not_accepted')
        op, target = change.get('op'), change.get('target_id')
        if op not in {'remember', 'correct', 'forget', 'resolve', 'hypothesis'}:
            raise ValueError('unsupported_personal_operation')
        existing = self.store.get(db, 'personal_record', target) if target and target.startswith('mem-') else None
        old_source = self.store.get(db, 'personal_source', target) if target and target.startswith('src-') else None
        if op in {'forget', 'resolve', 'correct'} and not existing and not old_source:
            raise ValueError('unknown_personal_target')
        if op == 'forget':
            result = self.forget(db, target)
            return {'accepted':True, 'id':target, 'reason':'forgotten', **result}
        if existing and existing['status'] in {'forgotten', 'deleted'}:
            raise ValueError('forgotten_record_requires_new_explicit_source')
        if op == 'resolve':
            if not existing or existing['kind'] != 'commitment' or existing['subject_id'] != 'owner':
                raise ValueError('resolve_only_reported_owner_commitment')
            return self._revise(db, existing, source, status='resolved', text=existing['text'])
        kind = change.get('kind', 'fact')
        subject = existing['subject_id'] if existing else change.get('subject_id', 'owner')
        if subject not in {'owner', 'self'} and not self.store.get(db, 'person', subject):
            if not any(r.get('subject_id') == subject for r in self.store.list(db, 'personal_record', None)):
                raise ValueError('unknown_personal_subject')
        text = (change.get('text') or '').strip()
        topic = ' '.join((change.get('topic') or (existing or {}).get('topic', '')).split()).casefold()[:100]
        if not text or len(text) > 600 or not topic:
            raise ValueError('personal_text_and_topic_required')
        if kind not in {'fact','preference','decision','commitment','observation','relationship','hypothesis'}:
            raise ValueError('unsupported_personal_kind')
        status = 'pending' if op == 'hypothesis' or kind == 'hypothesis' or (model_proposal and subject == 'self') else 'active'
        if status == 'pending':
            kind = 'hypothesis'
            existing = None
        if not existing:
            existing = next((r for r in self.store.list(db, 'personal_record', None)
                if r['kind']==kind and r['subject_id']==subject and r['topic']==topic and r['status'] in {'active','pending'}),None)
        if existing:
            result = self._revise(db, existing, source, text=text, status=status)
        else:
            if len(self.store.list(db, 'personal_record', None)) >= self.meta(db)['policy']['maximum_records']:
                raise ValueError('personal_record_limit')
            record = {'id':identifier('mem', source_id, kind, subject, topic), 'kind':kind, 'subject_id':subject,
                'topic':topic, 'text':text, 'source_id':source_id, 'source_kind':source['source_kind'],
                'scope':'owner_private', 'source_scope':'owner_private', 'certainty':'inferred' if status=='pending' else
                    'authored' if source['source_kind']=='administrative_edit' else 'reported',
                'confidence':.5 if status=='pending' else .8, 'revision':1, 'status':status,
                'created_at':self.clock(), 'updated_at':self.clock(), 'history_sources':[]}
            if status == 'pending':
                record['proposed_kind'] = change.get('kind', 'fact')
            self._put(db, 'personal_record', record['id'], record)
            result = {'accepted':True, 'id':record['id'], 'reason':'pending_hypothesis' if status=='pending' else 'remembered'}
        if old_source:
            old_source.update(recall_suppressed=True, corrected_by=result['id'])
            self._put(db, 'personal_source', old_source['id'], old_source)
            record = self.store.get(db, 'personal_record', result['id'])
            record['history_sources'] = list(dict.fromkeys(record.get('history_sources', [])+[old_source['id']]))
            self._put(db, 'personal_record', record['id'], record)
        source['promoted'] = True
        self._put(db, 'personal_source', source['id'], source)
        return result

    def _revise(self, db, record, source, *, text, status):
        if record['text'] == text and record['status'] == status:
            return {'accepted':True, 'id':record['id'], 'reason':'unchanged'}
        previous = deepcopy(record)
        previous['status'] = 'superseded'
        self._put(db, 'personal_version', record['id']+':'+str(record['revision']), previous)
        record = deepcopy(record)
        record.update(text=text, source_id=source['id'], source_kind=source['source_kind'], status=status,
            certainty='authored' if source['source_kind']=='administrative_edit' else 'reported',
            updated_at=self.clock(), revision=record['revision']+1,
            history_sources=list(dict.fromkeys(record.get('history_sources', [])+[previous['source_id']])))
        self._put(db, 'personal_record', record['id'], record)
        return {'accepted':True, 'id':record['id'], 'reason':'resolved' if status=='resolved' else 'corrected'}

    def recall(self, db, query, *, audience='owner_private', limit=5, max_chars=3000):
        if audience != 'owner_private':
            return []  # P2 has no reviewed public personal-memory publication path.
        rows = []
        for record in self.store.list(db, 'personal_record', None):
            source = self.store.get(db, 'personal_source', record['source_id'])
            if not source or source['status'] in {'forgotten','deleted'}:
                continue
            row = deepcopy(record)
            if row['status']=='resolved':
                row['status']='active'  # Searchable history, never an unfinished intention.
                row['text']+=' [Owner reported this commitment resolved; completion was not independently verified.]'
            prior = []
            for sid in record.get('history_sources', [])[-2:]:
                old = self.store.get(db, 'personal_source', sid)
                if old and old['status'] not in {'forgotten','deleted','pruned'}:
                    prior.append(old['text'][:240])
            if prior:
                row['text'] += ' [Earlier report, superseded: ' + ' | '.join(prior) + ']'
            rows.append(row)
        for source in self.store.list(db, 'personal_source', None):
            if source['status'] != 'active' or source.get('promoted') or source.get('recall_suppressed'):
                continue
            rows.append({'id':source['id'], 'kind':'archive', 'topic':' '.join((source.get('text') or '').split()[:8]),
                'text':source.get('text','')+(' [Shana replied: '+source['assistant_text']+']' if source.get('assistant_text') else ''),
                'subject_id':source.get('actor_person_id') or 'unresolved', 'scope':'owner_private', 'source_scope':'owner_private',
                'source_id':source['id'], 'source_kind':source['source_kind'], 'certainty':
                    'verified_bounded_result' if source['source_kind']=='verified_worker_observation' else 'reported', 'confidence':.4,
                'status':'active', 'updated_at':_timestamp(source.get('occurred_at')) or source['ingested_at']})
        result = rank_records(rows, query, audience=audience, limit=limit, max_chars=max_chars)
        for item in result:
            source = self.store.get(db, 'personal_source', item['source_id'])
            item['provenance'] = {'source_kind':source['source_kind'], 'occurred_at':source.get('occurred_at'),
                'record_status':(self.store.get(db,'personal_record',item['id']) or {}).get('status','archive'),
                'history_source_ids':(self.store.get(db, 'personal_record', item['id']) or {}).get('history_sources', []),
                'exact_source_retained':source['status'] not in {'pruned','forgotten','deleted'},
                'report_is_not_verified_fact':item['certainty'] not in {'verified_bounded_result'}}
        while result and len(json.dumps(result, ensure_ascii=False)) > max_chars:
            result.pop()
        return result

    def owner_context(self, db):
        """Current owner identity is separate from historical relationship claims."""
        person = self.store.get(db, 'person', 'owner')
        owner = {'person_id': 'owner', 'role': 'owner', 'name': 'Owner', 'name_confirmed': False}
        source = self.store.get(db, 'personal_source', person.get('source_id')) if person.get('source_id') else None
        if not person.get('owner_profile_confirmed') or not source or source.get('status') != 'administrative':
            return None
        owner.update(name=person['name'], pronunciation=person['pronunciation'], name_confirmed=True,
                     source_id=source['id'], confirmation_kind='owner_authenticated_profile')
        known_id = person.get('known_person_id')
        known = self.store.get(db, 'person', known_id) if known_id else None
        if known and known.get('status') == 'active':
            prior = self.store.get(db, 'personal_source', known.get('source_id')) if known.get('source_id') else None
            owner['identity_reference'] = {'person_id': known_id, 'claims_confirmed': False}
            if prior:
                owner['identity_reference'].update(source_id=prior['id'],
                    certainty=prior.get('certainty', 'unverified'))
        return owner

    def context(self, db, query, recent_turns=()):
        self.settle_mood(db)
        brain = self.store.get(db, 'brain', 'state')
        pending = [{'id':r['id'], 'kind':r['kind'], 'text':r['text'][:300], 'status':r['status'],
                    'source_id':r['source_id']} for r in self.store.list(db, 'personal_record', None)
                   if r['status']=='active' and r['kind'] in {'commitment','decision'}][:6]
        owner = self.owner_context(db)
        return {'identity':self.identity(db), **({'owner': owner} if owner is not None else {}),
            'recall':self.recall(db, query),
            'unfinished_and_decisions':pending,
            'people':[{'id':p['id'], 'name':p['name'][:60], 'relationship':p.get('relationship','')[:100],
                       'source_id':p.get('source_id'),'interaction_count':p.get('interaction_count',0)} for p in self.store.list(db,'person',8)
                      if p.get('status')=='active'],
            'recent_source_ids':[identifier('src','turn',t['id']) for t in recent_turns],
            'mood':{'tag':brain['mood'], 'intensity':brain.get('mood_intensity',0),
                    'updated_at':brain.get('mood_updated_at'), 'temporary':True}}

    def settle_mood(self, db):
        brain = self.store.get(db,'brain','state')
        if self.clock()-brain.get('mood_updated_at',self.clock()) >= self.meta(db)['policy']['mood_seconds']:
            brain.update(mood='neutral',mood_intensity=0)
            self._put(db,'brain','state',brain)

    def invalidate(self, db):
        brain = self.store.get(db,'brain','state')
        brain['sequence'] += 1
        self._put(db,'brain','state',brain)

    def forget(self, db, target, *, deleted=False):
        """Redact the source dependency closure, including P1-compatible projections."""
        sources = {r['id']:r for r in self.store.list(db,'personal_source',None)}
        records = self.store.list(db,'personal_record',None)
        memories = {target} if target.startswith('mem-') else set()
        affected = {target} if target.startswith('src-') else set()
        if not memories and target not in sources:
            raise ValueError('unknown_personal_target')
        found = next((r for r in records if r['id']==target),None)
        if memories and not found:
            raise ValueError('unknown_personal_target')
        if found:
            affected.update([found['source_id'],*found.get('history_sources',[])])
        while True:
            old = (len(affected),len(memories))
            for record in records:
                if record['source_id'] in affected or set(record.get('history_sources',[])) & affected:
                    memories.add(record['id'])
                    affected.update([record['source_id'],*record.get('history_sources',[])])
            for sid, source in sources.items():
                deps = source.get('dependencies',{})
                if set(deps.get('sources',[])) & affected or set(deps.get('memories',[])) & memories:
                    affected.add(sid)
            if old == (len(affected),len(memories)):
                break
        status = 'deleted' if deleted else 'forgotten'
        for sid in affected:
            source = sources.get(sid)
            if not source:
                continue
            # Keep stable references, hashes, dates and tombstones; drop all payload metadata.
            retained = {k:v for k,v in source.items() if k in {'id','source_kind','scope','turn_id','session_id',
                'text_sha256','occurred_at','ingested_at','legacy_ref','dependencies'}}
            retained.update(status=status,text='',assistant_text='',redacted_at=self.clock())
            self._put(db,'personal_source',sid,retained)
            if source.get('turn_id'):
                turn = self.store.get(db,'turn',source['turn_id'])
                if turn:
                    turn.update(text='',text_sha256=turn.get('text_sha256') or digest(turn['text']),
                        response=None,redacted=True,status='redacted')
                    self._put(db,'turn',turn['id'],turn)
        for kind in ('personal_record','personal_version'):
            for record in self.store.list(db,kind,None):
                if record['id'] in memories or record['source_id'] in affected:
                    record.update(text='',topic='',status=status,redacted_at=self.clock())
                    key = record['id'] if kind=='personal_record' else record['id']+':'+str(record['revision'])
                    self._put(db,kind,key,record)
        brain = self.store.get(db,'brain','state')
        for key,sid in list(brain.get('working_note_sources',{}).items()):
            if sid in affected:
                brain['working_notes'].pop(key,None)
                brain['working_note_sources'].pop(key,None)
        # Projections that did not yet have per-field provenance cannot safely survive a deletion.
        if affected:
            brain['working_notes'] = {}
            brain['working_note_sources'] = {}
        self._put(db,'brain','state',brain)
        for activity in self.store.list(db,'activity',None):
            if identifier('src','turn',activity.get('source_request')) in affected:
                activity['requested_goal'] = '[removed private request]'
                self._put(db,'activity',activity['id'],activity)
        identity = self.identity(db)
        for field,sid in list(identity.get('field_sources',{}).items()):
            if sid in affected:
                if field in identity['baseline_fields']:
                    identity['fields'][field]=deepcopy(identity['baseline_fields'][field])
                else:
                    identity['fields'].pop(field,None)
                identity['field_sources'].pop(field,None)
                identity['revision']+=1
        self._put(db,'personal_identity','self',identity)
        for edit in self.store.list(db,'admin_edit',None):
            if edit.get('source_id') in affected:
                self._put(db,'admin_edit',edit['id'],{'id':edit['id'],'source_id':edit['source_id'],
                    'kind':edit['kind'],'at':edit['at'],'redacted':True,'lived_experience':False})
        removed_people = set()
        for person in self.store.list(db,'person',None):
            if person.get('source_id') in affected:
                if person['id'] == 'owner':
                    person = {k:v for k,v in person.items() if k in {'id','scope','interaction_count'}}
                    person.update(name='Owner', relationship='', notes='', status='active')
                    self._put(db, 'person', 'owner', person)
                    continue
                removed_people.add(person['id'])
                person={k:v for k,v in person.items() if k in {'id','scope','source_id','interaction_count','legacy_table','legacy_id'}}
                person.update(name='Removed person',relationship='',notes='',status='deleted')
                self._put(db,'person',person['id'],person)
        owner = self.store.get(db, 'person', 'owner')
        if owner.get('known_person_id') in removed_people:
            owner.pop('known_person_id')
            self._put(db, 'person', 'owner', owner)
        for account in self.store.list(db,'account',None):
            if account.get('source_id') in affected:
                if account.get('platform_user_id'):
                    tid=identifier('account',account['platform'],account['platform_user_id'])
                    self._put(db,'account_tombstone',tid,{'id':tid,'status':'unlinked','reason':'source_removed'})
                account={k:v for k,v in account.items() if k in {'id','person_id','source_id','scope','legacy_table','legacy_id'}}
                account.update(platform='',platform_user_id='',display_name='',status='unlinked',grants_authority=False)
                self._put(db,'account',account['id'],account)
        from .shared_activity import redact_removed_sources
        from .phone import redact_sources
        redact_removed_sources(db)
        redact_sources(db, memory_ids=memories, source_ids=affected)
        # A channel review may have used an authored identity field or a selected
        # private observation. Clear cached text conservatively on source erasure.
        for cached in self.store.list(db, 'channel_review', None):
            cached.update(status='redacted', response=None, expires_at=0)
            self._put(db, 'channel_review', cached['id'], cached)
        from .shared_moments import redact_removed_reviews as redact_moment_reviews
        redact_moment_reviews(self.store, db, self.clock())
        from .stream_sources import redact_removed_reviews
        redact_removed_reviews(self.store, db, self.clock())
        self.invalidate(db)
        return {'redacted_sources':len(affected),'redacted_memories':len(memories)}

    def administrative_source(self, db, request_id, *, text='', dependencies=()):
        sid = identifier('src','admin',request_id)
        source = {'id':sid,'source_kind':'administrative_edit','origin_kind':'owner_control',
            'scope':'owner_private','status':'administrative','text':text,'text_sha256':digest(text),
            'occurred_at':self.clock(),'ingested_at':self.clock(),
            'dependencies':{'sources':list(dependencies),'memories':[]},'promoted':True}
        self._put(db,'personal_source',sid,source)
        return sid

    def administer(self, db, request_id, action, values):
        """Owner-authenticated controls. Model proposals never reach this method."""
        self.invalidate(db)
        if action in {'forget','delete'}:
            return self.forget(db,values['target_id'],deleted=action=='delete')
        sid = self.administrative_source(db,request_id)
        if action == 'owner_profile':
            if set(values) - {'name', 'pronunciation', 'known_person_id'}:
                raise ValueError('unsupported_owner_profile_fields')
            for key, maximum in (('name', 60), ('pronunciation', 80)):
                value = values.get(key)
                if (not isinstance(value, str) or not 1 <= len(value) <= maximum
                        or value.strip() != value or not value.isprintable()):
                    raise ValueError('invalid_owner_profile_' + key)
            known_id = values.get('known_person_id')
            if known_id is not None:
                if not isinstance(known_id, str) or not re.fullmatch(r'person-[a-f0-9]{24}', known_id):
                    raise ValueError('invalid_owner_profile_identity_reference')
                known = self.store.get(db, 'person', known_id)
                if not known or known.get('status') != 'active':
                    raise ValueError('owner_profile_identity_reference_unavailable')
            person = self.store.get(db, 'person', 'owner')
            person.update(name=values['name'], pronunciation=values['pronunciation'], source_id=sid,
                          owner_profile_confirmed=True, status='active')
            person.pop('known_person_id', None)
            if known_id is not None:
                person['known_person_id'] = known_id
            source = self.store.get(db, 'personal_source', sid)
            source['text'] = json.dumps(values, sort_keys=True, ensure_ascii=False)
            source['text_sha256'] = digest(source['text'])
            self._put(db, 'personal_source', sid, source)
            self._put(db, 'person', 'owner', person)
            return {'person_id': 'owner', 'source_id': sid, 'name_confirmed': True,
                    'historical_claims_confirmed': False, 'grants_authority': False}
        if action == 'identity':
            fields = values.get('fields',{})
            if not fields or set(fields)-IDENTITY_FIELDS:
                raise ValueError('unsupported_identity_fields')
            if len(json.dumps(fields,ensure_ascii=False))>12000:
                raise ValueError('identity_edit_too_large')
            if any(not isinstance(v,(str,list,dict)) for v in fields.values()):
                raise ValueError('identity_fields_require_text_structure')
            identity = self.identity(db)
            source=self.store.get(db,'personal_source',sid)
            source['dependencies']['sources']=list(set(identity['field_sources'].values()))
            self._put(db,'personal_source',sid,source)
            # Preserve explicit authored edit history separately from conversation sources.
            self._put(db,'admin_edit',request_id,{'id':request_id,'source_id':sid,'kind':'identity',
                'previous_revision':identity['revision'],'previous_field_sources':dict(identity['field_sources']),
                'fields':deepcopy(fields),'at':self.clock(),'lived_experience':False})
            identity['fields'].update(deepcopy(fields))
            identity['field_sources'].update({k:sid for k in fields})
            identity['revision'] += 1
            self._put(db,'personal_identity','self',identity)
            return {'revision':identity['revision'],'source_kind':'administrative_edit'}
        if action == 'memory':
            source = self.store.get(db,'personal_source',sid)
            source['text'] = values.get('text','')
            source['text_sha256'] = digest(source['text'])
            self._put(db,'personal_source',sid,source)
            return self.change(db,values,sid)
        if action == 'person':
            pid = values.get('person_id') or identifier('person','admin',request_id)
            if pid in {'owner','self'} or not re.fullmatch(r'person-[a-f0-9]{24}',pid):
                raise ValueError('invalid_person_id')
            person = self.store.get(db,'person',pid) or {'id':pid,'interaction_count':0,'status':'active','scope':'owner_private'}
            if person.get('source_id'):
                source=self.store.get(db,'personal_source',sid)
                source['dependencies']['sources']=[person['source_id']]
                self._put(db,'personal_source',sid,source)
            for key in ('name','relationship','notes'):
                value = values.get(key,person.get(key,''))
                if not isinstance(value,str) or len(value)>600:
                    raise ValueError('invalid_person_field')
                person[key] = value
            person['source_id'] = sid
            self._put(db,'person',pid,person)
            return {'person_id':pid}
        if action in {'link','unlink'}:
            platform, account = values.get('platform'),values.get('platform_user_id')
            if not all(isinstance(v,str) and 0<len(v)<=200 for v in (platform,account)):
                raise ValueError('exact_platform_and_account_required')
            aid = identifier('account',platform,account)
            matching=[a for a in self.store.list(db,'account',None) if
                      (a.get('platform'),a.get('platform_user_id'))==(platform,account)]
            old = next((a for a in matching if a.get('status')=='active'),matching[0] if matching else None)
            if old:aid=old['id']
            if old and old.get('source_id'):
                previous_source=self.store.get(db,'personal_source',old['source_id'])
                if previous_source:
                    previous_source['recall_suppressed']=True
                    self._put(db,'personal_source',previous_source['id'],previous_source)
            pid = values.get('person_id')
            if action=='unlink':
                if not old or old.get('person_id')!=pid:
                    raise ValueError('account_link_mismatch')
                old.update(status='unlinked',source_id=sid)
                tid=identifier('account',platform,account)
                self._put(db,'account_tombstone',tid,{'id':tid,'status':'unlinked','source_id':sid})
            else:
                if not self.store.get(db,'person',pid) or pid=='self':
                    raise ValueError('unknown_person')
                if values.get('confirmed') is not True:
                    raise ValueError('explicit_account_confirmation_required')
                if old and old.get('status')=='active' and old['person_id']!=pid:
                    raise ValueError('account_already_bound')
                old = {'id':aid,'platform':platform,'platform_user_id':account,'person_id':pid,
                    'status':'active','source_id':sid,'confirmed':True,'grants_authority':False}
                person=self.store.get(db,'person',pid)
                if person.get('source_id'):
                    source=self.store.get(db,'personal_source',sid)
                    source['dependencies']['sources']=[person['source_id']]
                    self._put(db,'personal_source',sid,source)
                tid=identifier('account',platform,account)
                self._put(db,'account_tombstone',tid,{'id':tid,'status':'explicitly_relinked','source_id':sid})
            self._put(db,'account',aid,old)
            return {'account_id':aid,'status':old['status'],'grants_authority':False}
        if action=='policy':
            policy = dict(self.meta(db)['policy'])
            bounds = {'text_days':(0,3650),'per_session_sources':(1,5000),'maximum_sources':(10,20000),
                      'maximum_records':(10,20000),'mood_seconds':(1,86400),'queue_seconds':(1,604800)}
            if not values or set(values)-set(bounds):
                raise ValueError('unsupported_retention_policy')
            for key,value in values.items():
                if type(value)!=int or not bounds[key][0]<=value<=bounds[key][1]:
                    raise ValueError('retention_policy_out_of_bounds')
                policy[key] = value
            meta = self.meta(db)
            meta['policy'] = policy
            self._put(db,'personal_meta','state',meta)
            return {'policy':policy,'pruned':self.prune(db)}
        raise ValueError('unsupported_personal_control')

    def observe_activity(self, db, activity):
        sid = identifier('src','activity',activity['id'],activity['observed_revision'])
        if self.store.get(db,'personal_source',sid):
            return
        source = {'id':sid,'source_kind':'verified_worker_observation','origin_kind':'capability_evidence',
            'scope':'owner_private','status':'active','occurred_at':self.clock(),'ingested_at':self.clock(),
            'artifact_ref':activity.get('checkpoint_ref'),'observed_after_sleep':activity.get('observed_after_sleep',False),
            'text':json.dumps({k:activity.get(k) for k in ('id','goal','status','reason','task_results','observed_after_sleep')},sort_keys=True),
            'assistant_text':'','promoted':False,'dependencies':{'memories':[], 'sources':
                [identifier('src','turn',activity['source_request'])] if activity.get('source_request') else []}}
        source['text_sha256']=digest(source['text'])
        self._put(db,'personal_source',sid,source)

    def correct_activity_sleep_provenance(self, db, activity_id):
        """Correct a derived flag without recreating erased source material."""
        corrected=0
        for source in self.store.list(db,'personal_source',None):
            if (source.get('source_kind')!='verified_worker_observation' or source.get('status')!='active'
                or source.get('origin_kind')!='capability_evidence'):
                continue
            try:
                body=json.loads(source.get('text',''))
            except (ValueError,TypeError):
                continue
            if (not isinstance(body,dict) or body.get('id')!=activity_id
                or not (body.get('observed_after_sleep') is True or source.get('observed_after_sleep') is True)):
                continue
            previous_hash=source.get('text_sha256')
            body['observed_after_sleep']=False
            source.update(observed_after_sleep=False,text=json.dumps(body,sort_keys=True))
            source['text_sha256']=digest(source['text'])
            source.setdefault('derived_provenance_corrections',[]).append({
                'field':'observed_after_sleep','previous':True,'current':False,
                'reason':'activity_not_present_at_sleep_boundary','corrected_at':self.clock(),
                'previous_text_sha256':previous_hash})
            self._put(db,'personal_source',source['id'],source)
            corrected+=1
        return corrected

    def prune(self, db):
        policy = self.meta(db)['policy']
        sources = sorted(self.store.list(db,'personal_source',None),key=lambda s:s.get('ingested_at',0),reverse=True)
        counts, pruned = {},0
        for index,source in enumerate(sources):
            if source['status'] not in {'active','pending','quarantined'}:
                continue
            if source['status']=='pending' and source.get('turn_id'):
                turn=self.store.get(db,'turn',source['turn_id'])
                if turn and turn['status'] in {'pending','queued','recoverable'} and self.clock()<turn.get('expires_at',float('inf')):
                    continue  # In-flight/queued input has its own finite delivery deadline.
            session = source.get('session_id','imported')
            counts[session] = counts.get(session,0)+1
            if (index < policy['maximum_sources'] and counts[session]<=policy['per_session_sources']
                and self.clock()-source.get('ingested_at',0)<policy['text_days']*86400):
                continue
            source.update(text='',assistant_text='',status='pruned',pruned_at=self.clock())
            self._put(db,'personal_source',source['id'],source)
            if source.get('turn_id'):
                turn = self.store.get(db,'turn',source['turn_id'])
                if turn:
                    turn.update(text_sha256=turn.get('text_sha256') or digest(turn['text']),text='',response=None,
                                status='pruned',redacted=True)
                    self._put(db,'turn',turn['id'],turn)
            pruned += 1
        if pruned:
            self.invalidate(db)
        return pruned

    def inspect(self, db):
        return {'identity':self.identity(db),'policy':self.meta(db)['policy'],
            'identity_id':self.meta(db)['identity_id'],
            'records':self.store.list(db,'personal_record',500),
            'people':self.store.list(db,'person',100),'accounts':self.store.list(db,'account',100),
            'sources':[{k:v for k,v in s.items() if k not in {'text','assistant_text'}}
                       for s in self.store.list(db,'personal_source',500)],
            'administrative_edits':self.store.list(db,'admin_edit',100),
            'queued_messages':[{'id':t['id'],'session_id':t['session_id'],'status':t['status'],
                                'received_at':t.get('received_at')} for t in self.store.list(db,'turn',None)
                               if t['status'] in {'queued','recoverable'}]}
