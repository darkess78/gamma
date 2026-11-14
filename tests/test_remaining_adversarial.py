"""Independent resource, copied-owner and browser-lifecycle adversarial checks."""
from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
from pathlib import Path
import sqlite3
import subprocess
from threading import Event, Lock
from unittest.mock import patch

import pytest

from gamma.coding.durable_practice import PracticeRunner
from gamma.coordination.core import Core
from gamma.coordination.maintenance import Maintenance
import gamma.coordination.maintenance as maintenance_module
from gamma.coordination.store import Store
import gamma.coordination.transfer as transfer_module
from gamma.schemas.response import AssistantResponse


def make(root, draft=None):
    store=Store(root)
    return Core(store,draft=draft or (lambda *_:AssistantResponse(display_text='Private result.',delivery_mode='text_only')),
        runner=PracticeRunner(store,propose=lambda *_:{},check=lambda *_:{}))


def test_restore_capacity_accounts_for_concurrent_reservations(tmp_path):
    core=make(tmp_path/'state')
    try:
        service=Maintenance(core)
        backup=service.backup('before-race')
        with core.store.transaction(core.token) as db:
            for index in range(7):core.store.put(db,'maintenance_restore',f'old-{index}',{'ok':True})
        original=maintenance_module.restore_snapshot
        entered=Event();both=Event();release=Event();lock=Lock();count=0
        def held(*args,**kwargs):
            nonlocal count
            with lock:
                count+=1;entered.set()
                if count==2:both.set()
            assert release.wait(5)
            return original(*args,**kwargs)
        results=[]
        with patch.object(maintenance_module,'restore_snapshot',held),ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(service.rehearse,backup['id']);assert entered.wait(5)
            second=pool.submit(service.rehearse,backup['id'])
            both.wait(.5);release.set()
            for future in (first,second):
                try:results.append(future.result(5))
                except ValueError as error:results.append(error)
        with core.store.transaction(core.token) as db:count=len(core.store.list(db,'maintenance_restore',None))
        assert count<=8,f'Concurrent restore admissions exceeded declared capacity: {count}'
        assert sum(isinstance(r,dict) and r.get('ok') for r in results)<=1
    finally:core.close()


def test_backup_setup_failure_is_not_left_preparing_forever(tmp_path):
    core=make(tmp_path/'state')
    try:
        service=Maintenance(core)
        original=Path.mkdir
        def fail_staging(path,*args,**kwargs):
            if path.parent==service.root and path.name.endswith('-staging'):
                raise OSError('controlled staging directory failure')
            return original(path,*args,**kwargs)
        with patch.object(Path,'mkdir',fail_staging):
            with pytest.raises(OSError,match='controlled staging'):
                service.backup('failed-setup')
        record=core.store.read('maintenance_backup','failed-setup')
        assert record['status']=='failed_review_required'
    finally:core.close()


def test_live_backup_cannot_start_a_second_owner_of_the_same_identity(tmp_path):
    core=make(tmp_path/'live')
    try:
        service=Maintenance(core);backup=service.backup('copy')
        service.rehearse(backup['id'])
        restored=next(service.root.glob('restore-*'))
        before=Store(restored).read('brain','state')
        with pytest.raises((BlockingIOError,OSError)):
            second=make(restored)
            second.close()
        assert Store(restored).read('brain','state')==before
        assert core.store.read('brain','state')['owner']==core.token[1]
    finally:core.close()


def test_erasing_a_source_clears_checked_channel_response_and_prevents_replay(tmp_path):
    core=make(tmp_path)
    try:
        with core.store.transaction(core.token) as db:
            source=core.personal.administrative_source(db,'source',text='A private selected observation.')
        context={'channel':'shared_activity','audience':'owner_private','source_ids':[source]}
        response=core.review_channel(context,'review-source')
        assert response.display_text=='Private result.'
        core.personal_control('forget','forget',{'target_id':source})
        row=core.store.read('channel_review','review-source')
        assert row['response'] is None and row['status']=='redacted'
        assert core.review_channel(context,'review-source').reason_code=='channel_review_not_replayed'
    finally:core.close()


def test_backup_invalidates_review_cache_without_mutating_live_response(tmp_path):
    core=make(tmp_path/'live')
    try:
        core.review_channel({'channel':'phone_reminder','audience':'owner_private','commitment':'A private reminder.'},'review')
        live=core.store.read('channel_review','review')
        service=Maintenance(core);backup=service.backup('copy')
        copy=Store(service.root/backup['id'])
        row=copy.read('channel_review','review')
        assert row['response'] is None and row['status']=='cancelled'
        assert core.store.read('channel_review','review')==live
    finally:core.close()


def test_restore_verifies_the_copied_bytes_after_source_changes(tmp_path):
    core=make(tmp_path/'live')
    try:
        service=Maintenance(core);backup=service.backup('copy-race')
        source=service.root/backup['id']/'coordination.sqlite3'
        expected=hashlib.sha256(source.read_bytes()).hexdigest()
        original=transfer_module.shutil.copyfile
        changed=False
        def mutate_then_copy(old,new,*args,**kwargs):
            nonlocal changed
            if Path(old)==source and not changed:
                changed=True
                with sqlite3.connect(source) as db:
                    brain=json.loads(db.execute("SELECT body FROM records WHERE kind='brain' AND id='state'").fetchone()[0])
                    brain['focus']='focused'
                    db.execute("UPDATE records SET body=? WHERE kind='brain' AND id='state'",(json.dumps(brain),))
            return original(old,new,*args,**kwargs)
        with patch.object(transfer_module.shutil,'copyfile',mutate_then_copy):
            try:result=service.rehearse(backup['id'])
            except ValueError:return  # A changed source must be rejected.
        restored=next(service.root.glob('restore-*'))/'coordination.sqlite3'
        assert not result['ok'] or hashlib.sha256(restored.read_bytes()).hexdigest()==expected, \
            'Restore accepted bytes that were changed after manifest verification.'
    finally:core.close()


def test_restored_backup_catalog_cannot_claim_missing_local_snapshots_are_available(tmp_path):
    core=make(tmp_path/'live')
    try:
        service=Maintenance(core)
        first=service.backup('first')
        second=service.backup('second')
        service.rehearse(second['id'])
        restored=next(service.root.glob('restore-*'))
        record=Store(restored).read('maintenance_backup','first')
        manifest=restored/'recovery'/first['id']/'manifest.json'
        assert record['status']!='verified_snapshot' or manifest.exists(), \
            'Copied catalog exposes a verified local backup whose files were not restored.'
        assert core.store.read('maintenance_backup','first')['status']=='verified_snapshot'
    finally:core.close()


def run_browser_contract(source,body):
    # Node executes on Gamma. This is a controlled JS lifecycle test, not a
    # claim of visual Brave validation or an attempt to bypass its URL guard.
    program="""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map(),ready=[],windows=new Map(),calls=[],notices=[];
function element(id){if(!nodes.has(id))nodes.set(id,{id,checked:false,disabled:false,value:'osrs_screen',
  hidden:false,textContent:'',children:[],classList:{toggle(){}},handlers:new Map(),
  addEventListener(name,fn){this.handlers.set(name,fn);},append(...x){this.children.push(...x);},
  replaceChildren(){this.children=[];},getTracks(){return [];}});return nodes.get(id);}
const document={hidden:false,getElementById:element,createElement:()=>element('new-'+nodes.size),
  querySelectorAll:()=>[],addEventListener:(name,fn)=>{if(name==='DOMContentLoaded')ready.push(fn);}};
const window={addEventListener:(name,fn)=>windows.set(name,fn),ShanaUI:{
  id:()=>String(calls.length),notice:(node,text)=>{node.textContent=text;notices.push(text);},feed(){},
  request:async(path,values,options)=>{calls.push({path,values,options});return reply(path,values);}}};
const sandbox={document,window,navigator:{mediaDevices:{}},AbortController,AbortSignal,setInterval(){},console};
const tick=()=>new Promise(r=>setImmediate(r));
"""+body+"""
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),sandbox);
(async()=>{for(const fn of ready)fn();await tick();await check();})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result=subprocess.run(['node','-e',program,str(Path(source).resolve())],text=True,capture_output=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr


def test_home_never_claims_failed_or_preparing_backup_is_verified():
    run_browser_contract('src/gamma/dashboard/static/coordination_home.js',"""
function reply(path,values){
  if(values)return {status:'failed_review_required'};
  if(path.includes('/maintenance'))return {free_bytes:2e9,small_write_admitted:true,backups:[]};
  return {awake:true,focus:'relaxed',activities:[]};
}
async function check(){await element('homeBackup').handlers.get('click')();await tick();
  assert(!notices.some(n=>n==='Recovery copy verified.'),'Failed backup was announced as verified.');}
""")


def test_selected_window_pagehide_revokes_server_capture_lease():
    run_browser_contract('src/gamma/dashboard/static/shared_activities.js',"""
const track={label:'Private test window',stopped:false,getSettings:()=>({displaySurface:'window'}),
  stop(){this.stopped=true;},addEventListener(){}};
sandbox.navigator.mediaDevices.getDisplayMedia=async()=>({getVideoTracks:()=>[track],getTracks:()=>[track]});
function reply(path,values){
  if(path.includes('/minecraft/'))throw new Error('no test world');
  if(path.includes('/status'))return {image_analysis_available:true,state:{source_epoch:1},context:{shared_goal:'',mode:'relaxed'}};
  return {source_epoch:2};
}
async function check(){element('sharingPrivate').checked=true;await element('sharingPrivate').handlers.get('change')();await tick();
  await element('sharingStart').handlers.get('click')();
  assert(calls.some(c=>c.values&&c.values.action==='select'));
  windows.get('pagehide')();await tick();assert(track.stopped);
  assert(calls.some(c=>c.values&&c.values.action==='stop'),'Closing the page left the selected-source lease live.');}
""")


def test_selected_window_start_cannot_overlap_a_pending_picker():
    run_browser_contract('src/gamma/dashboard/static/shared_activities.js',"""
let pickerCalls=0,resolvePicker;
const pendingPicker=new Promise(r=>{resolvePicker=r;});
sandbox.navigator.mediaDevices.getDisplayMedia=()=>{pickerCalls++;return pendingPicker;};
function reply(path,values){
  if(path.includes('/minecraft/'))throw new Error('no world');
  return {image_analysis_available:true,state:{source_epoch:1},context:{shared_goal:'',mode:'relaxed'}};
}
async function check(){element('sharingPrivate').checked=true;await element('sharingPrivate').handlers.get('change')();await tick();
  const start=element('sharingStart').handlers.get('click');
  start();start();assert.equal(pickerCalls,1,'Two capture permission requests ran concurrently.');}
""")


def test_shared_page_makes_no_private_request_before_confirmation():
    run_browser_contract('src/gamma/dashboard/static/shared_activities.js',"""
function reply(path,values){return {image_analysis_available:true,state:{source_epoch:1},
  context:{shared_goal:'Do not reveal this before confirmation.',mode:'relaxed'}};}
async function check(){assert.equal(calls.length,0,'Page fetched private shared context before confirmation.');
  assert.notEqual(element('sharingGoal').value,'Do not reveal this before confirmation.');}
""")


def test_late_private_readiness_cannot_reveal_goal_after_deconfirmation():
    run_browser_contract('src/gamma/dashboard/static/shared_activities.js',"""
let resolveStatus;const delayed=new Promise(r=>{resolveStatus=r;});
function reply(path,values){
  if(path.includes('/minecraft/'))throw new Error('no world');
  if(path.includes('/status'))return delayed;
  return {source_epoch:3};
}
async function check(){element('sharingPrivate').checked=true;
  const checking=element('sharingPrivate').handlers.get('change')();await tick();
  element('sharingPrivate').checked=false;await element('sharingPrivate').handlers.get('change')();
  resolveStatus({image_analysis_available:true,state:{source_epoch:2},
    context:{shared_goal:'Private goal from a late response',mode:'focused',observation:{summary:'Private observation'}}});
  await checking;await tick();
  assert.notEqual(element('sharingGoal').value,'Private goal from a late response');
  assert.notEqual(element('sharingObservation').textContent,'Private observation');}
""")


def test_privacy_loss_stops_a_server_selection_still_pending():
    run_browser_contract('src/gamma/dashboard/static/shared_activities.js',"""
let resolveSelect;const selected=new Promise(r=>{resolveSelect=r;});
const track={label:'Private test window',getSettings:()=>({displaySurface:'window'}),
  stop(){},addEventListener(){}};
sandbox.navigator.mediaDevices.getDisplayMedia=async()=>({getVideoTracks:()=>[track],getTracks:()=>[track]});
function reply(path,values){
  if(path.includes('/minecraft/'))throw new Error('no world');
  if(path.includes('/status'))return {image_analysis_available:true,state:{source_epoch:1},context:{shared_goal:'',mode:'relaxed'}};
  if(values&&values.action==='select')return selected;
  return {source_epoch:3};
}
async function check(){element('sharingPrivate').checked=true;
  await element('sharingPrivate').handlers.get('change')();await tick();
  const starting=element('sharingStart').handlers.get('click')();await tick();
  assert(calls.some(c=>c.values&&c.values.action==='select'));
  element('sharingPrivate').checked=false;await element('sharingPrivate').handlers.get('change')();await tick();
  assert(calls.some(c=>c.values&&c.values.action==='stop'),'Privacy loss did not fence a pending server selection.');
  resolveSelect({source_epoch:2});await starting;}
""")
