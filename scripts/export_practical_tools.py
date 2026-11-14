"""Export verified P3 candidates as private, directly runnable review files.

The source is read from the actual closed candidate journal and immutable
artifacts. Generated code runs only through the existing offline sandbox.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
from zoneinfo import ZoneInfo

from gamma.coding.practice import _digest
from gamma.coordination.store import Store, private_path, process_lock
from gamma.coordination.work import WorkRunner
from practical_work_fixtures import independent_user_flow


USAGE = {
    'inventory': '''# File inventory tool

This reviewed candidate uses Python's standard library. It is separate from
running Shana. Choose a folder you want to inspect and an explicit CSV path.

```bash
python inventory.py /path/to/folder
python inventory.py /path/to/folder --csv /path/to/report.csv
python inventory.py /path/to/folder --extension txt --csv /path/to/text-files.csv
```

The JSON and CSV rows are sorted by relative path. The extension filter ignores
case. The tool skips symbolic links. The protected tests in this folder and the
separate export receipt identify the checked version.
''',
    'tracker': '''# Local task tracker

This reviewed candidate uses Python's standard library. It is separate from
running Shana. Choose an explicit JSON database path for your task list.

```bash
python tasks.py --db /path/to/tasks.json add "Read the notes"
python tasks.py --db /path/to/tasks.json list
python tasks.py --db /path/to/tasks.json rename 1 "Read the revised notes"
python tasks.py --db /path/to/tasks.json complete 1
python tasks.py --db /path/to/tasks.json list --status open
python tasks.py --db /path/to/tasks.json list --status done
```

IDs remain stable across edits and completion. Blank new titles, unknown IDs,
malformed JSON and invalid task fields or value types fail without replacing
the prior database. A manually edited database with duplicate IDs or blank
stored titles is not rejected by this candidate. Use its commands to maintain
the task list. Concurrent writers are outside this candidate's scope. The
protected tests and separate export receipt identify the checked version.
''',
}


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    path.chmod(0o600)


def export_tools(report_path, output):
    project=Path(__file__).resolve().parents[1]
    if os.name!='posix' or project.name in {'gamma-main','gamma-modular-phase1'}:
        raise RuntimeError('isolated_Gamma_candidate_required')
    allowed=private_path(project/'data/coordination/p3/review/tools')
    output=private_path(output)
    report_path=private_path(report_path)
    if not output.is_relative_to(allowed) or output.exists():
        raise ValueError('export_requires_new_private_tools_directory')
    if not report_path.is_relative_to(project/'data/coordination/p3') or report_path.name!='report.json':
        raise ValueError('report_must_be_a_P3_candidate_receipt')
    report_bytes=report_path.read_bytes()
    report=json.loads(report_bytes)
    if (report.get('complete') is not True or report.get('error_type') or report.get('error') or not report.get('checks')
        or any(value is not True for value in report['checks'].values())
        or report.get('temporary_processes_absent') is not True
        or report.get('production_activated') is not False
        or type(report.get('external_submissions')) is not int or report['external_submissions']!=0
        or type(report.get('paid_calls')) is not int or report['paid_calls']!=0):
        raise ValueError('report_does_not_show_completed_isolated_journey')
    state=report_path.parent
    if not (state/'coordination.sqlite3').is_file():
        raise ValueError('report_has_no_current_candidate_journal')
    with ExitStack() as leases:
        leases.enter_context(process_lock(state/'owner.lock'))
        store=Store(state)
        runner=WorkRunner(store,propose=lambda *_:(_ for _ in ()).throw(RuntimeError('export_never_infers')))
        candidates={}
        for kind in ('inventory','tracker'):
            saved=report['tasks'][kind+'_changed']
            identifier=saved['id']
            if not isinstance(identifier,str) or not re.fullmatch(r'work-[a-f0-9]{32}',identifier):
                raise ValueError('invalid_reported_tool_id')
            leases.enter_context(process_lock(state/'runs'/(identifier+'.lock')))
            run=store.read('run',identifier)
            if not run or run['status']!='completed' or run['checkpoint_ref']!=saved['run']['checkpoint_ref']:
                raise ValueError('reported_candidate_no_longer_matches_journal')
            checkpoint=runner.verify(run)
            if (checkpoint['source_ref']!=saved['checkpoint']['source_ref']
                or checkpoint['source_sha256']!=saved['checkpoint']['source_sha256']
                or checkpoint['spec']['mode']!='build'
                or checkpoint['spec']['editable']!=['inventory.py' if kind=='inventory' else 'tasks.py']):
                raise ValueError('reported_tool_source_or_scope_changed')
            files=runner.artifacts.get(checkpoint['source_ref'])['files']
            if _digest(files)!=checkpoint['source_sha256'] or 'USAGE.md' in files:
                raise ValueError('invalid_export_source_map')
            candidates[kind]=(run,checkpoint,files)
        output.mkdir(parents=True,mode=0o700)
        manifest={'scope':'isolated_P3_review_tools','created_by':getpass.getuser()+'@'+platform.node(),
            'created_at':datetime.now(ZoneInfo('America/Denver')).isoformat(),
            'source_report':str(report_path.relative_to(project)),'source_report_sha256':_hash(report_bytes),
            'production_activated':False,'external_submissions':0,'paid_calls':0,'tools':{}}
        receipts={}
        for kind,(run,checkpoint,files) in candidates.items():
            folder=output/kind
            folder.mkdir(mode=0o700)
            hashes={}
            for name,text in files.items():
                target=private_path(folder/name)
                if not target.is_relative_to(folder):
                    raise ValueError('exported_source_outside_tool_directory')
                target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                data=text.encode('utf-8');target.write_bytes(data);target.chmod(0o600)
                hashes[name]=_hash(data)
            usage=folder/'USAGE.md'
            usage.write_bytes(USAGE[kind].encode('utf-8'));usage.chmod(0o600)
            reread={name:(folder/name).read_bytes().decode('utf-8') for name in hashes}
            if reread!=files or _digest(reread)!=checkpoint['source_sha256']:
                raise AssertionError('exported_bytes_differ_from_verified_candidate')
            receipt=independent_user_flow(kind,reread,changed=True)
            receipts[kind]=receipt
            entry={'run_id':run['id'],'source_ref':checkpoint['source_ref'],
                'source_sha256':checkpoint['source_sha256'],'source_files':hashes,
                'usage_file':'USAGE.md','usage_sha256':_hash(usage.read_bytes()),
                'usage_is_candidate_source':False,'checks_passed':receipt.get('passed') is True}
            manifest['tools'][kind]=entry
            _write_json(output/'tools-manifest.json',manifest)
            _write_json(output/'verification.json',{'status':'verifying','tools':receipts,'production_activated':False})
            if (receipt.get('passed') is not True or type(receipt.get('exit_code')) is not int
                or receipt['exit_code']!=0 or receipt.get('infrastructure_error') is not False
                or receipt.get('source_sha256')!=checkpoint['source_sha256']
                or any(_hash((folder/name).read_bytes())!=value for name,value in hashes.items())):
                raise AssertionError('exported_tool_independent_flow_failed')
        result={'status':'verified_isolated_tools','tools':receipts,'production_activated':False,
                'source_report_sha256':manifest['source_report_sha256']}
        _write_json(output/'verification.json',result)
        return {'output':str(output),'tools':{kind:entry['source_sha256'] for kind,entry in manifest['tools'].items()},
                'status':result['status'],'production_activated':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(export_tools(args.report,args.output),indent=2))


if __name__=='__main__':main()
