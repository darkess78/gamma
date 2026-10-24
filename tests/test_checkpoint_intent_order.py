"""Actual scoped SQLite ordering, retention and additive compatibility only."""
from concurrent.futures import ThreadPoolExecutor
from asyncio import CancelledError
from datetime import datetime, timedelta, timezone
import hashlib
import json
import shutil
import sqlite3
import os
import subprocess
import sys

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from gamma.config import settings
from gamma.memory import continuity as module
from gamma.memory.continuity import (ContinuityService, ConversationJournalEntry,
    ConversationInputCursor, JournalOwnershipError)
from gamma.memory.journal_identity import capture_identity


def store(path): return ContinuityService(database_url='sqlite:///' + str(path))


def begin(service, text='request', session='s', scope='local_private'):
    return service.begin_exchange(session_id=session, text=text, speaker_name='fixture', input_source='fixture', privacy_scope=scope)


def complete(service, exchange, goal, session='s', scope='local_private', silent=False, **updates):
    service.complete_exchange(exchange_id=exchange, session_id=session, assistant_text='' if silent else 'done '+goal,
        trace_id='fixture', output_target='dashboard_monitor', privacy_scope=scope,
        state_updates={'current_objective':goal,'active_topic':goal,**updates}, tool_metadata={'fixture_effect':'retained'})


def working(service, session='s', scope='local_private'):
    return service.snapshot(session, privacy_scope=scope)['working_state']


def cursor(service, session='s', scope='local_private'):
    with Session(service._engine) as db: return db.get(ConversationInputCursor, (session,scope))


def test_equal_time_and_reversed_uuid_order_cannot_change_receive_order(tmp_path, monkeypatch):
    frozen=datetime.now(timezone.utc);monkeypatch.setattr(module,'_utc_now',lambda:frozen)
    service=store(tmp_path/'equal.db')
    ids=iter(['f'*32,'0'*32]);monkeypatch.setattr(module,'uuid4',lambda:type('ID',(),{'hex':next(ids)})())
    a=begin(service)
    b=begin(service)
    with Session(service._engine) as db:
        for row in db.exec(select(ConversationJournalEntry)):row.occurred_at=frozen;db.add(row)
        db.commit()
    complete(service,b,'new');saved=working(service);complete(service,a,'old')
    assert working(service)==saved and saved['current_objective']=='new'


def test_pruned_newer_rows_and_reused_id_cannot_restore_old_context(tmp_path, monkeypatch):
    now=datetime.now(timezone.utc);monkeypatch.setattr(module,'_utc_now',lambda:now)
    monkeypatch.setattr(settings,'conversation_journal_retention_days',1)
    service=store(tmp_path/'reuse.db');a=begin(service);b=begin(service);complete(service,b,'new')
    with Session(service._engine) as db:
        old_b_id=db.exec(select(ConversationJournalEntry).where(ConversationJournalEntry.exchange_id==b,ConversationJournalEntry.role=='user')).one().id
    now+=timedelta(days=3);service.prune();assert cursor(service).latest_sequence==2
    c=begin(service)
    with Session(service._engine) as db:
        row=db.exec(select(ConversationJournalEntry).where(ConversationJournalEntry.exchange_id==c)).one()
        assert row.id==old_b_id and row.input_sequence==3
    saved=working(service);complete(service,a,'obsolete');assert working(service)==saved
    complete(service,c,'newest');assert working(service)['current_objective']=='newest'


def test_cursor_survives_no_journal_rows_while_fresh_checkpoint_remains(tmp_path, monkeypatch):
    now=datetime.now(timezone.utc);monkeypatch.setattr(module,'_utc_now',lambda:now)
    monkeypatch.setattr(settings,'conversation_journal_retention_days',1)
    service=store(tmp_path/'silent.db');a=begin(service);now+=timedelta(days=3)
    complete(service,a,'fresh',silent=True)
    with Session(service._engine) as db: assert not list(db.exec(select(ConversationJournalEntry)))
    assert cursor(service).latest_sequence==1 and working(service)['current_objective']=='fresh'
    service=store(tmp_path/'silent.db');b=begin(service)
    with Session(service._engine) as db:
        row=db.exec(select(ConversationJournalEntry)).one();assert row.id==1 and row.input_sequence==2
    complete(service,b,'next');assert working(service)['current_objective']=='next'


@pytest.mark.parametrize('error', [OSError, KeyboardInterrupt, CancelledError])
def test_failed_new_receive_rolls_back_marker_and_does_not_supersede(tmp_path, error):
    service=store(tmp_path/'rollback.db');a=begin(service)
    def reject(connection,db_cursor,statement,parameters,context,many):
        if statement.startswith('INSERT INTO conversationjournalentry'):raise error('synthetic receive failure')
    event.listen(service._engine,'before_cursor_execute',reject)
    try:
        with pytest.raises(error):begin(service,'failed')
    finally:event.remove(service._engine,'before_cursor_execute',reject)
    assert cursor(service).latest_sequence==1
    complete(service,a,'valid');assert working(service)['current_objective']=='valid'


def test_forged_state_update_marker_cannot_make_old_work_current(tmp_path):
    service=store(tmp_path/'forged.db');a=begin(service);b=begin(service);complete(service,b,'new')
    saved=working(service);complete(service,a,'old',latest_sequence=999,input_sequence=999)
    assert working(service)==saved and cursor(service).latest_sequence==2


def test_distinct_working_session_keys_remain_independent_with_common_journal_prefix(tmp_path):
    service=store(tmp_path/'long.db');prefix='x'*120
    first,second=prefix+'first',prefix+'second'
    a=begin(service,session=first);b=begin(service,session=second)
    complete(service,b,'second',session=second);complete(service,a,'first',session=first)
    assert working(service,session=first)['current_objective']=='first'
    assert working(service,session=second)['current_objective']=='second'


def test_pending_failed_new_input_still_prevents_stale_checkpoint_replacement(tmp_path):
    service=store(tmp_path/'failed.db');seed=begin(service);complete(service,seed,'saved')
    a=begin(service);b=begin(service);assert service.fail_exchange(b)
    complete(service,a,'old');assert working(service)['current_objective']=='saved'


def test_internal_markers_are_absent_from_payloads_prompts_and_model_dump(tmp_path):
    service=store(tmp_path/'private.db');a=begin(service);complete(service,a,'visible')
    values=json.dumps(service.snapshot('s'))+service.prompt_context('s',privacy_scopes={'local_private'})
    assert 'input_sequence' not in values and 'latest_sequence' not in values
    with Session(service._engine) as db:
        for row in db.exec(select(ConversationJournalEntry)):assert 'input_sequence' not in row.model_dump()
    assert 'latest_sequence' not in cursor(service).model_dump()


def test_retention_cleans_cursor_only_after_scope_context_and_work_expire(tmp_path, monkeypatch):
    now=datetime.now(timezone.utc);monkeypatch.setattr(module,'_utc_now',lambda:now)
    monkeypatch.setattr(settings,'conversation_journal_retention_days',1)
    service=store(tmp_path/'cleanup.db');a=begin(service);complete(service,a,'old')
    now+=timedelta(days=3);service.prune()
    assert working(service) is None and cursor(service) is None
    b=begin(service);assert cursor(service).latest_sequence==1
    service.delete_session('s');assert cursor(service) is None
    with pytest.raises(JournalOwnershipError):complete(service,b,'deleted')


def test_counter_exhaustion_fails_without_wrapping_or_receiving_input(tmp_path):
    service=store(tmp_path/'max.db');a=begin(service)
    with Session(service._engine) as db:
        row=db.get(ConversationInputCursor,('s','local_private'));row.latest_sequence=2**63-1;db.add(row);db.commit()
    with pytest.raises(RuntimeError,match='cursor state is invalid'):begin(service)
    assert cursor(service).latest_sequence==2**63-1
    with Session(service._engine) as db: assert len(list(db.exec(select(ConversationJournalEntry))))==1


OLD_SCHEMA='''
CREATE TABLE conversationjournalentry (id INTEGER PRIMARY KEY, exchange_id TEXT NOT NULL,
session_id TEXT NOT NULL, role TEXT NOT NULL, speaker_name TEXT,input_source TEXT NOT NULL,
text TEXT NOT NULL,occurred_at DATETIME NOT NULL,trace_id TEXT,output_target TEXT,
privacy_scope TEXT NOT NULL,completion_status TEXT NOT NULL,tool_metadata_json TEXT NOT NULL,owner_id TEXT);
CREATE TABLE conversationjournalowner (owner_id TEXT PRIMARY KEY,host TEXT NOT NULL,pid INTEGER NOT NULL,
process_started FLOAT NOT NULL,checked_at DATETIME NOT NULL);
CREATE TABLE workingstatecheckpoint (session_id TEXT PRIMARY KEY, active_topic TEXT NOT NULL,
current_objective TEXT NOT NULL,pending_questions_json TEXT NOT NULL,commitments_json TEXT NOT NULL,
relevant_people_json TEXT NOT NULL,last_meaningful_interaction DATETIME,last_assistant_action TEXT NOT NULL,
next_intended_action TEXT NOT NULL,privacy_scope TEXT NOT NULL,updated_at DATETIME NOT NULL);
'''


def legacy_database(path):
    host,pid,started=capture_identity();now=datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as db:
        db.executescript(OLD_SCHEMA)
        db.execute('INSERT INTO conversationjournalowner VALUES(?,?,?,?,?)',('legacy-owner',host,pid,started,now))
        db.execute('INSERT INTO conversationjournalentry VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (1,'legacy-old','s','user','fixture','fixture','old work',now,None,None,'local_private','pending','{}','legacy-owner'))
        db.execute('INSERT INTO workingstatecheckpoint VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('s','saved topic','retained newer objective','[]','["PRIVATE_COMMITMENT"]','[]',now,'newer action','next','public',now))


def test_copied_legacy_database_keeps_ambiguous_saved_context_then_new_input_advances(tmp_path):
    original=tmp_path/'original.db';legacy_database(original);before=hashlib.sha256(original.read_bytes()).hexdigest()
    path=tmp_path/'copy.db';shutil.copyfile(original,path);service=store(path);service._owner_id='legacy-owner'
    saved=working(service);assert saved['current_objective']=='retained newer objective'
    assert working(service,scope='public') is None
    complete(service,'legacy-old','must-not-invent-order');assert working(service)==saved
    with Session(service._engine) as db:
        row=db.exec(select(ConversationJournalEntry).where(ConversationJournalEntry.exchange_id=='legacy-old',ConversationJournalEntry.role=='user')).one()
        assert row.completion_status=='completed' and row.input_sequence is None
    a=begin(service,'post-change');assert cursor(service).latest_sequence==1
    complete(service,a,'new known order');assert working(service)['current_objective']=='new known order'
    assert working(service)['commitments']==['PRIVATE_COMMITMENT']
    reopened=store(path);assert cursor(reopened).latest_sequence==1
    assert hashlib.sha256(original.read_bytes()).hexdigest()==before


@pytest.mark.parametrize('legacy', [False, True])
def test_concurrent_service_initialization_and_receive_are_serialized(tmp_path, legacy):
    path=tmp_path/'init.db'
    if legacy:legacy_database(path)
    def receive(i):
        service=store(path);return service,begin(service,str(i))
    with ThreadPoolExecutor(max_workers=6) as pool: pairs=list(pool.map(receive,range(12)))
    service=store(path)
    with Session(service._engine) as db:
        rows=list(db.exec(select(ConversationJournalEntry).where(ConversationJournalEntry.input_sequence.is_not(None))))
        assert sorted(row.input_sequence for row in rows)==list(range(1,13))
    assert cursor(service).latest_sequence==12
    for caller,exchange in pairs:complete(caller,exchange,exchange)
    with Session(service._engine) as db:
        newest=db.exec(select(ConversationJournalEntry).where(ConversationJournalEntry.role=='user',ConversationJournalEntry.input_sequence==12)).one()
    assert working(service)['current_objective']==newest.exchange_id


CHILD='''
import sys
from sqlmodel import Session,select
from gamma.memory.continuity import ContinuityService,ConversationJournalEntry
s=ContinuityService(database_url='sqlite:///'+sys.argv[1])
for _ in range(6):
 e=s.begin_exchange(session_id='s',text='fixture',speaker_name='fixture',input_source='fixture',privacy_scope='local_private')
 with Session(s._engine) as db:n=db.exec(select(ConversationJournalEntry).where(ConversationJournalEntry.exchange_id==e)).one().input_sequence
 s.complete_exchange(exchange_id=e,session_id='s',assistant_text='fixture',trace_id=None,output_target='dashboard_monitor',privacy_scope='local_private',state_updates={'current_objective':'goal-'+str(n)})
'''


@pytest.mark.skipif(os.name!='posix',reason='Gamma Linux process/SQLite proof')
def test_separate_process_receive_counters_and_completions_are_monotonic(tmp_path):
    path=tmp_path/'process.db';store(path)
    children=[subprocess.Popen([sys.executable,'-B','-c',CHILD,str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(4)]
    for child in children:
        out,err=child.communicate(timeout=20);assert child.returncode==0,out+err
    service=store(path);assert cursor(service).latest_sequence==24
    assert working(service)['current_objective']=='goal-24'
