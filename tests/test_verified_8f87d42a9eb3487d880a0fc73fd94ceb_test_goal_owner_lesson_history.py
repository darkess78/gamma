import copy,json,os
from pathlib import Path
import pytest
from gamma.coding.lessons import LessonStore

def valid():
 return {'id':'work-'+'a'*32,'status':'completed','tasks':[{'status':'verified'}],'application':{'passed':True,'evidence':{'installed_checks':{'passed':True,'count':12},'activation':{'passed':True}}}}

def test_unhashable_task_status_rejected_without_mutation(tmp_path):
 store=LessonStore(tmp_path/'lessons')
 for status in [[],{},None,1,True]:
  data=valid();data['tasks'][0]['status']=status;store.ingest_autonomous(data)
  assert store.list()==[]

def test_history_oversized_rejected(tmp_path):
 root=tmp_path/'lessons';root.mkdir();store=LessonStore(root)
 (root/'lesson-python.history.jsonl').write_text('x'*400001)
 with pytest.raises(ValueError):store.history('lesson-python')

def test_history_rechecks_parent_symlink(tmp_path):
 root=tmp_path/'lessons';outside=tmp_path/'outside';outside.mkdir();store=LessonStore(root)
 (outside/'lesson-python.history.jsonl').write_text(json.dumps({'text':'sentinel','active':True,'authority':'owner_edited','evidence_runs':[],'timestamp':'today'})+'\n')
 root.symlink_to(outside,target_is_directory=True)
 with pytest.raises(ValueError):store.history('lesson-python')

def test_history_private_and_atomic_replacement(tmp_path):
 previous=os.umask(0o022)
 try:
  root=tmp_path/'lessons';store=LessonStore(root)
  store.ingest({'version':1,'production_verified':False,'id':'run-one','tasks':[{'skill':'python','status':'verified_practice'}]})
  path=root/'lesson-python.history.jsonl';assert path.stat().st_mode&0o777==0o600
  old=path.stat().st_ino
  store.edit('lesson-python',text='owner preference',active=False)
  assert path.stat().st_ino!=old
  revisions=store.history('lesson-python')
  assert revisions[0]['text']=='owner preference' and revisions[0]['active'] is False
 finally:os.umask(previous)
