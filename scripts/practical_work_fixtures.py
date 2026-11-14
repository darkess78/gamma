"""Fictional owner task packets and independent CLI acceptance for P3.

These are two user-selected disposable tools, not curriculum exercises. Their
actual CLI checks run in the existing no-network, read-only-source sandbox.
"""
from copy import deepcopy


INVENTORY_GOAL = '''Build inventory.py as a standard-library Python command line tool.
Usage: python inventory.py ROOT [--csv OUTPUT]. Recursively inventory regular
files under ROOT, ignoring symbolic links. Print a JSON list sorted by relative
POSIX path. Each row has exactly path (relative string), size (bytes integer),
and extension (lowercase suffix including the dot, or empty). Never include
ROOT itself. If --csv is supplied also write a UTF-8 CSV with that header and
the same rows, using the csv module so commas and Unicode work. ROOT may be
empty. Invalid ROOT prints a concise error to stderr and exits nonzero. Read
only ROOT, and write only the explicitly selected CSV path. No dependencies,
network, application activation or changes outside this disposable tool.'''

INVENTORY_PUBLIC = r'''import csv,json,pathlib,subprocess,sys,tempfile,unittest
class InventoryFlow(unittest.TestCase):
    def test_actual_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder)/'input';root.mkdir();(root/'sub').mkdir()
            (root/'a.txt').write_bytes(b'abc');(root/'sub'/'b.BIN').write_bytes(b'12')
            result=subprocess.run([sys.executable,'inventory.py',str(root)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),[{'path':'a.txt','size':3,'extension':'.txt'},
                {'path':'sub/b.BIN','size':2,'extension':'.bin'}])
    def test_csv_empty_invalid(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder)/'empty';root.mkdir();out=pathlib.Path(folder)/'report.csv'
            result=subprocess.run([sys.executable,'inventory.py',str(root),'--csv',str(out)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),[])
            with out.open(newline='',encoding='utf-8') as stream:self.assertEqual(list(csv.reader(stream)),[['path','size','extension']])
            bad=subprocess.run([sys.executable,'inventory.py',str(root/'missing')],capture_output=True,text=True)
            self.assertNotEqual(bad.returncode,0)
'''

INVENTORY_CHANGE = '''New request: retain all current behavior. Add optional
--extension SUFFIX, case-insensitive, accepting either txt or .txt, selecting
only matching suffixes. JSON and CSV must both use that filter. An empty result
still has the CSV header. Do not follow symlinks, even ones pointing at a file
outside ROOT. Update only inventory.py.'''

INVENTORY_CHANGED_PUBLIC = r'''import json,pathlib,subprocess,sys,tempfile,unittest
class InventoryExtension(unittest.TestCase):
    def test_filter_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder)/'items';root.mkdir()
            (root/'ALPHA.TXT').write_bytes(b'one');(root/'beta.csv').write_bytes(b'two')
            result=subprocess.run([sys.executable,'inventory.py',str(root),'--extension','txt'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),[{'path':'ALPHA.TXT','size':3,'extension':'.txt'}])
'''

TRACKER_GOAL = '''Build tasks.py as a standard-library Python command line tool.
Usage: python tasks.py --db PATH add TITLE; list; complete ID; rename ID TITLE.
Each invocation reads/writes only the specified UTF-8 JSON database. A missing
database starts empty. Tasks have exactly id (positive integer), title (string),
and done (boolean). add assigns increasing stable IDs and prints the added task
as JSON. list prints a JSON list in ID order. complete marks the given task
done, prints it, and is idempotent. rename changes its title, preserving ID and
done, and prints it. Reject blank titles, unknown IDs and malformed databases
with nonzero exit and a concise stderr message, preserving the prior database.
Writes must be atomic with a temporary file in the same directory followed by
replace. Concurrent use is not required for this initial local tool. Unicode
titles work. No network, external dependencies, production changes or activation.'''

TRACKER_PUBLIC = r'''import json,pathlib,subprocess,sys,tempfile,unittest
class TrackerFlow(unittest.TestCase):
    def test_persist_edit_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            db=pathlib.Path(folder)/'tasks.json'
            def run(*args):
                result=subprocess.run([sys.executable,'tasks.py','--db',str(db),*args],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)
            self.assertEqual(run('list'),[])
            first=run('add','Read the notes');second=run('add','Fix bicycle')
            self.assertEqual(first,{'id':1,'title':'Read the notes','done':False});self.assertEqual(second['id'],2)
            self.assertEqual(run('rename','1','Read revised notes'),{'id':1,'title':'Read revised notes','done':False})
            self.assertTrue(run('complete','1')['done']);self.assertTrue(run('complete','1')['done'])
            self.assertEqual(len(run('list')),2)
            before=db.read_bytes()
            bad=subprocess.run([sys.executable,'tasks.py','--db',str(db),'rename','999','bad'],capture_output=True,text=True)
            self.assertNotEqual(bad.returncode,0);self.assertEqual(db.read_bytes(),before)
'''

TRACKER_CHANGE = '''New request: preserve the tracker. Add list --status
open|done|all, default all, filtering by completion. Reject any other status.
Add must reject whitespace-only titles. Update only tasks.py.'''

TRACKER_CHANGED_PUBLIC = r'''import json,pathlib,subprocess,sys,tempfile,unittest
class TrackerFilter(unittest.TestCase):
    def test_filter_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            db=pathlib.Path(folder)/'tasks.json'
            def run(*args):
                result=subprocess.run([sys.executable,'tasks.py','--db',str(db),*args],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)
            run('add','First');run('add','Second');run('complete','1')
            self.assertEqual([x['id'] for x in run('list','--status','open')],[2])
            self.assertEqual([x['id'] for x in run('list','--status','done')],[1])
'''


def packet(kind, *, current=None, changed=False):
    """Owner-selected source and checks. No arbitrary host paths or commands."""
    if kind not in {'inventory','tracker'}:
        raise ValueError('unknown acceptance tool')
    inventory=kind=='inventory'
    name='inventory.py' if inventory else 'tasks.py'
    goal=INVENTORY_GOAL if inventory else TRACKER_GOAL
    public=INVENTORY_PUBLIC if inventory else TRACKER_PUBLIC
    files={name:current if current is not None else '# Owner-requested disposable tool. Implement the documented CLI.\n',
           'test_public.py':public,'README.md':goal}
    checks=[{'name':'public_cli_flow','module':'test_public','kind':'regression'}]
    if changed:
        goal=(INVENTORY_CHANGE if inventory else TRACKER_CHANGE)+'\n\nOriginal contract:\n'+goal
        files['test_changed.py']=INVENTORY_CHANGED_PUBLIC if inventory else TRACKER_CHANGED_PUBLIC
        checks.append({'name':'changed_request_cli','module':'test_changed','kind':'regression'})
    return {'mode':'build','goal':goal,'files':files,'editable':[name],'checks':checks}


INDEPENDENT_INVENTORY = r'''import csv,json,pathlib,subprocess,sys,tempfile
with tempfile.TemporaryDirectory() as folder:
    root=pathlib.Path(folder)/'real flow';root.mkdir();(root/'deep').mkdir()
    (root/'deep'/'notes, caf\u00e9.TxT').write_bytes(b'contents')
    (root/'empty').write_bytes(b'');(root/'table.csv').write_bytes(b'x,y')
    outside=pathlib.Path(folder)/'private.txt';outside.write_bytes(b'not included')
    (root/'linked.txt').symlink_to(outside)
    out=pathlib.Path(folder)/'result.csv'
    args=[sys.executable,'inventory.py',str(root),'--csv',str(out)]
    if CHANGED:args+=['--extension','.tXt']
    result=subprocess.run(args,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    rows=json.loads(result.stdout)
    expected=[{'path':'deep/notes, caf\u00e9.TxT','size':8,'extension':'.txt'}]
    if not CHANGED:expected+=[{'path':'empty','size':0,'extension':''},{'path':'table.csv','size':3,'extension':'.csv'}]
    assert rows==expected,(rows,expected)
    with out.open(newline='',encoding='utf-8') as stream:
        actual=list(csv.DictReader(stream))
    assert actual==[{k:str(v) for k,v in row.items()} for row in expected],actual
print('independent_inventory_user_flow_passed')
'''

INDEPENDENT_TRACKER = r'''import json,pathlib,subprocess,sys,tempfile
with tempfile.TemporaryDirectory() as folder:
    db=pathlib.Path(folder)/'my tasks.json'
    def run(*args):
        result=subprocess.run([sys.executable,'tasks.py','--db',str(db),*args],capture_output=True,text=True)
        assert result.returncode==0,result.stderr
        return json.loads(result.stdout)
    assert run('list')==[]
    first=run('add','Plan caf\u00e9 visit');second=run('add','Read revised report')
    assert first=={'id':1,'title':'Plan caf\u00e9 visit','done':False}
    edited=run('rename',str(first['id']),'Plan Thursday caf\u00e9 visit')
    assert edited=={'id':1,'title':'Plan Thursday caf\u00e9 visit','done':False}
    assert run('complete','2')=={'id':2,'title':'Read revised report','done':True}
    assert len(run('list'))==2
    if CHANGED:
        assert run('list','--status','open')==[edited]
        assert [t['id'] for t in run('list','--status','done')]==[2]
    before=db.read_bytes()
    invalid=subprocess.run([sys.executable,'tasks.py','--db',str(db),'add','   '],capture_output=True,text=True)
    assert invalid.returncode!=0 and db.read_bytes()==before
    db.write_text('{broken',encoding='utf-8');before=db.read_bytes()
    invalid=subprocess.run([sys.executable,'tasks.py','--db',str(db),'add','hello'],capture_output=True,text=True)
    assert invalid.returncode!=0 and db.read_bytes()==before
print('independent_tracker_user_flow_passed')
'''


def independent_user_flow(kind, files, *, changed=False):
    """Independent actual CLI assertions, omitted from the proposal and feedback."""
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace
    from gamma.coding.sandbox import run_practice_check
    from gamma.coding.practice import _digest
    from gamma.coordination.store import private_path
    source=INDEPENDENT_INVENTORY if kind=='inventory' else INDEPENDENT_TRACKER
    module='independent_owner_flow'
    values=deepcopy(files)
    if module+'.py' in values:raise ValueError('independent_check_name_collision')
    values[module+'.py']='CHANGED='+repr(changed)+'\n'+source
    with tempfile.TemporaryDirectory(prefix='shana-p3-independent-') as folder:
        root=Path(folder)
        for name,text in values.items():
            path=private_path(root/name)
            if not path.is_relative_to(root):raise ValueError('candidate_outside_sandbox')
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
        result=run_practice_check(root,SimpleNamespace(name='independent_owner_user_flow',module=module,kind='feature'))
    return {**result,'source_sha256':_digest(files),'kind_of_evidence':'actual_cli_flow_separate_from_learner_checks'}
