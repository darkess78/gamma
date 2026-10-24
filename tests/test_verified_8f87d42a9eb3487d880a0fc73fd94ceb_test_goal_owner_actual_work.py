from copy import deepcopy
from gamma.coding.lessons import LessonStore

def valid(key='a'):
 return {'id':'work-'+key*32,'status':'completed','tasks':[{'id':'feature','editable':['src/gamma/memory/example.py'],'status':'verified','attempts':2}], 'application':{'passed':True,'evidence':{'installed_checks':{'passed':True,'count':12},'activation':{'passed':True}}}}

def test_actual_controller_record_and_owner_preference(tmp_path):
 store=LessonStore(tmp_path/'lessons'); record=valid()
 store.ingest_autonomous(record)
 rows=store.list(); assert len(rows)==1
 assert rows[0]['id']=='lesson-repository_verification'
 assert rows[0]['evidence_runs']==[record['id']] and rows[0]['observation_count']==1
 store.ingest_autonomous(record); assert store.list()[0]['observation_count']==1
 store.edit(rows[0]['id'],text='Keep my own lesson',active=False)
 store.ingest_autonomous(valid('b')); row=store.list()[0]
 assert row['text']=='Keep my own lesson' and row['active'] is False and row['authority']=='owner_edited'
 assert row['observation_count']==2
 assert store.history(row['id'])[0]['provenance']=='autonomous_ingest'

def test_missing_or_invalid_skill_uses_repository_default(tmp_path):
 store=LessonStore(tmp_path/'lessons')
 for key,skill in [('a',None),('b',{}),('c','../bad')]:
  record=valid(key); record['tasks'][0]['skill']=skill; store.ingest_autonomous(record)
 assert len(store.list())==1
 assert store.list()[0]['id']=='lesson-repository_verification'
 assert store.list()[0]['observation_count']==3

def test_invalid_proof_does_not_become_learning(tmp_path):
 cases=[]
 for key in ('passed','activation','count','tasks'):
  record=valid()
  if key=='passed':record['application']['passed']=1
  if key=='activation':record['application']['evidence']['activation']={}
  if key=='count':record['application']['evidence']['installed_checks']['count']=True
  if key=='tasks':record['tasks'][0]['status']='already_satisfied'
  cases.append(record)
 store=LessonStore(tmp_path/'lessons')
 for record in cases:store.ingest_autonomous(record)
 assert store.list()==[]
