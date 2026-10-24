"""Owner-invoked repository work on registered, independently checked scopes."""
import argparse
import ast
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager

from .learner import LocalPracticeLearner
from .project_loop import run_project
from .project_checks import CONTRACT, REGRESSION, FEATURE, HOLDOUT
from .practice import PracticeCheck
from .sandbox import run_practice_check


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    temporary.replace(path)


def job_path(root, identifier):
    if not re.fullmatch(r'project-[a-f0-9]{32}',identifier): raise ValueError('Invalid repository job')
    path = Path(root) / identifier
    if any(p.is_symlink() for p in (path,*path.parents)): raise ValueError('Invalid repository storage')
    return path


@contextmanager
def submission_lock(root):
    root=Path(root)
    job_path(root,'project-'+'0'*32)
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=root/'submission.lock'
    if path.is_symlink(): raise ValueError('Invalid queue lock')
    with path.open('a+b') as stream:
        if os.name=='nt':
            import msvcrt
            if path.stat().st_size==0: stream.write(b'0');stream.flush()
            stream.seek(0)
            try: msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError: raise ValueError('Repository queue busy') from None
            try: yield
            finally:
                stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            try: fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError: raise ValueError('Repository queue busy') from None
            try: yield
            finally: fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def create_job(root, goal, model, checker_model='gpt-oss:20b'):
    if not isinstance(goal,str) or not 12 <= len(goal) <= 1200: raise ValueError('Invalid coding goal')
    if not re.search(r'\bstatus\b',goal,re.I) or not re.search(r'\b(discord|bot)\b',goal,re.I):
        raise ValueError('This registered scope handles Discord bot status; broader goals require another scope')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}',model): raise ValueError('Invalid model')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}',checker_model) or checker_model==model:
        raise ValueError('Select a different local checker model')
    identifier = 'project-' + uuid.uuid4().hex
    path = job_path(root,identifier)
    path.mkdir(parents=True,mode=0o700)
    record={'id':identifier,'goal':goal,'model':model,'checker_model':checker_model,'scope':'discord_status','status':'queued',
            'desired_state':'running','created_at':time.time(),'events':[],'model_weights_changed':False}
    write_json(path/'state.json',record)
    return record


def list_jobs(root):
    records=[]
    for p in list(Path(root).glob('project-*'))[:200]:
        state=p/'state.json'
        if p.is_dir() and not p.is_symlink() and state.is_file() and not state.is_symlink() and state.stat().st_size<=262144:
            try: records.append(json.loads(state.read_text(encoding='utf-8')))
            except (OSError,ValueError): continue
    return sorted(records,key=lambda r:r.get('created_at',0),reverse=True)[:20]


def _pure_source(source):
    tree=ast.parse(source)
    if any(not isinstance(n,(ast.FunctionDef,ast.Expr)) for n in tree.body): raise ValueError('Unsafe module scope')
    if any(isinstance(n,(ast.Import,ast.ImportFrom,ast.Global,ast.Nonlocal)) for n in ast.walk(tree)):
        raise ValueError('Pure helper imports prohibited')
    banned={'open','eval','exec','compile','getattr','setattr','delattr','globals','locals','vars','__import__','breakpoint','input'}
    for n in ast.walk(tree):
        if isinstance(n,ast.FunctionDef) and n.decorator_list: raise ValueError('Decorators prohibited')
        if isinstance(n,(ast.While,ast.AsyncFunctionDef,ast.ClassDef)): raise ValueError('Unbounded helper execution prohibited')
        if isinstance(n,ast.Name) and (n.id in banned or n.id.startswith('__')): raise ValueError('Unsafe helper')
        if isinstance(n,ast.Attribute) and n.attr.startswith('_'): raise ValueError('Unsafe attribute')
        if isinstance(n,ast.Expr) and n in tree.body and not (isinstance(n.value,ast.Constant) and isinstance(n.value.value,str)):
            raise ValueError('Module execution prohibited')


def status_summary(process, state, now, project_root):
    """Run the installed pure helper with a restricted builtins namespace."""
    target=Path(project_root)/'src/gamma/integrations/discord/bot_status.py'
    fallback={'connected':False,'voice':'unknown','label':'Status unverified'}
    try:
        if target.is_symlink() or target.stat().st_size>16384: return fallback,None
        source=target.read_text(encoding='utf-8')
        _pure_source(source)
        import builtins
        allowed=('isinstance','dict','list','tuple','set','int','float','bool','str','type','len','any','all','min','max','abs','enumerate','sorted','range')
        namespace={'__builtins__':{name:getattr(builtins,name) for name in allowed}}
        exec(compile(source,str(target),'exec'),namespace)
        result=namespace['summarize'](process,state,now)
        if not isinstance(result,dict) or set(result)!={'connected','voice','label'} or type(result['connected']) is not bool:
            return fallback,None
        return result,hashlib.sha256(source.encode()).hexdigest()
    except Exception:
        return fallback,None


def execute_job(root, identifier, project_root, *, maximum_attempts=5, budget_seconds=600):
    path=job_path(root,identifier)
    record=json.loads((path/'state.json').read_text())
    if record['status']!='queued': return record
    target=Path(project_root)/'src/gamma/integrations/discord/bot_status.py'
    if any(p.is_symlink() for p in (target,*target.parents)) or target.stat().st_size>16384:
        raise ValueError('Invalid registered source')
    initial=target.read_bytes()
    baseline=hashlib.sha256(initial).hexdigest()
    lock=Path(root)/'execution.lock'
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    record.update(status='running',baseline_sha256=baseline)
    write_json(path/'state.json',record)
    (path/'rollback.py').write_bytes(initial)
    try:
        from ..config import settings
        from .lessons import LessonStore
        learner=LocalPracticeLearner(endpoint=settings.local_llm_endpoint, model=record['model'],timeout_seconds=120,
                                     lessons=LessonStore(settings.data_dir/'coding'/'lessons').list())
        deadline = time.monotonic() + budget_seconds
        def checkpoint():
            if time.monotonic() >= deadline: raise TimeoutError("Authorized time budget ended")
            latest=json.loads((path/'state.json').read_text())
            if latest.get('desired_state')!='running' or (path/'stop.requested').exists(): raise RuntimeError('Owner stopped work')
        learner.checkpoint = checkpoint
        def event(value):
            record['stage']=value['stage']
            record['events']=(record['events']+[{**value,'at':time.time()}])[-40:]
            checkpoint()
            write_json(path/'state.json',record)
        def plan(goal):
            event({'stage':'planning','attempt':0})
            if record.get('checker_parent'):
                parent=job_path(root,record['checker_parent'])
                previous=json.loads((parent/'state.json').read_text(encoding='utf-8'))
                tasks=json.loads((parent/'plan.json').read_text(encoding='utf-8'))['tasks']
                if len(tasks)!=1 or tasks[0].get('editable')!=['bot_status.py'] or previous['status']!='needs_checker':
                    raise ValueError('Invalid checker continuation')
                candidate=json.loads((parent/'candidate.json').read_text(encoding='utf-8'))
                event({'stage':'checker','task_id':tasks[0]['id'],'attempt':0})
                critique=checker(tasks[0],candidate,previous['result']['tasks'][0]['evidence'][-2:])
                if not isinstance(critique.get('guidance'),str) or not critique.get('evidence'):
                    raise ValueError('Checker guidance missing')
                tasks[0]['instructions']=critique['guidance']
                record['checker']=critique
                write_json(path/'plan.json',{'goal':goal,'tasks':tasks,'model':record['model'],'checker':critique})
                return tasks
            learner.require_idle()
            response=learner._json('/api/chat',{'model':record['model'],'stream':False,'think':False,'format':'json','keep_alive':0,
                'options':{'num_ctx':8192,'num_predict':600,'temperature':0.1},
                'messages':[{'role':'system','content':'Determine whether the entire owner goal can be satisfied by the pure Discord status helper contract. If it asks for unrelated features, joining channels, sending messages, training or broader project changes, return JSON {"goal_supported":false,"tasks":[]}. Otherwise plan the task and return JSON {"goal_supported":true,"tasks":[{"id":"implement_status","editable":["bot_status.py"],"depends_on":[],"instructions":"your implementation approach"}]}. Only bot_status.py is authorized. Use one task for this single pure helper. Do not claim finished.'},
                            {'role':'user','content':goal+'\nRequired acceptance contract:\n'+CONTRACT}]})
            if response.get('done') is not True or response.get('done_reason')=='length': raise RuntimeError('Incomplete plan')
            planned=json.loads(response['message']['content'])
            if planned.get('goal_supported') is not True: raise ValueError('Goal requires a broader registered scope')
            tasks=planned['tasks']
            if len(tasks)!=1 or tasks[0].get('editable')!=['bot_status.py']: raise ValueError('Unregistered task scope')
            write_json(path/'plan.json',{'goal':goal,'tasks':tasks,'model':record['model']})
            return tasks
        def propose(task,files,feedback):
            proposal=learner(SimpleNamespace(instructions=record['goal']+'\n'+CONTRACT+'\nPlan: '+str(task.get('instructions','')),skill='discord_status'),files,feedback)
            proposal={name:'\n'.join(line.rstrip() for line in source.splitlines())+'\n' for name,source in proposal.items()}
            for source in proposal.values(): _pure_source(source)
            write_json(path/'candidate.json',proposal)
            attempt=sum(e.get('stage')=='coding' for e in record['events'])
            write_json(path/f'attempt-{attempt:02d}.json',{'model':record['model'],'goal':record['goal'],
                'source_before':files,'proposal':proposal,'feedback':feedback,'training_approved':False})
            return proposal
        def checks(task,files,independent=False):
            results=[]
            with tempfile.TemporaryDirectory(prefix='gamma-repository-') as temporary:
                workspace=Path(temporary)
                (workspace/'bot_status.py').write_text(files['bot_status.py'])
                sources={'test_regression':REGRESSION,'test_feature':FEATURE} if not independent else {'test_holdout':HOLDOUT}
                for module,source in sources.items():
                    (workspace/(module+'.py')).write_text(source)
                    result=run_practice_check(workspace,PracticeCheck(module,'regression' if module=='test_regression' else 'feature',module))
                    results.append(result)
            return {'passed':all(r['passed'] for r in results),'evidence':results}
        def checker(task,files,feedback):
            critic=LocalPracticeLearner(endpoint=settings.local_llm_endpoint,model=record.get('checker_model','gpt-oss:20b'),timeout_seconds=120)
            critic.checkpoint = checkpoint
            critic.require_idle()
            prompt=json.dumps({'goal':record['goal'],'contract':CONTRACT,'failed_plan':task,
                               'candidate':files,'failures':feedback[-2:]},ensure_ascii=False)
            if len(prompt.encode())>16000: raise ValueError('Checker context too large')
            thinking='low' if critic.model.startswith('gpt-oss') else False
            response=critic._json('/api/chat',{'model':critic.model,'stream':False,'think':thinking,'format':'json','keep_alive':0,
                'options':{'num_ctx':8192,'num_predict':2048,'temperature':0.1},'messages':[
                    {'role':'system','content':'Independently diagnose the failed repository code using the owner contract and executed failures. The proposed plan and source can be wrong. Refute incorrect assumptions and give a precise corrective implementation approach. Do not change tests or relax the contract. Return ONLY JSON {"guidance":"corrected approach","evidence":"specific observed failure supporting it"}.'},
                    {'role':'user','content':prompt}]})
            record['checker_transport']={key:response.get(key) for key in ('done','done_reason','eval_count','prompt_eval_count','total_duration')}
            write_json(path/'state.json',record)
            if response.get('done') is not True or response.get('done_reason')=='length': raise RuntimeError('Checker incomplete')
            critique=json.loads(response['message']['content'])
            critique['model']=critic.model
            # Evidence comes from executed checks, never from a model's claim.
            critique['evidence']={'executed_failures':feedback[-2:]}
            write_json(path/'checker.json',critique)
            return critique
        def promote(task,files):
            checkpoint()
            source=files['bot_status.py']
            _pure_source(source)
            if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest()!=baseline:
                return {'passed':False,'evidence':'Live source changed; candidate not applied'}
            temp=target.with_suffix('.candidate.tmp')
            temp.write_text(source,encoding='utf-8')
            temp.replace(target)
            # Verify the real installed artifact through the same offline protected contracts.
            installed={'bot_status.py':target.read_text()}
            fixed=checks(task,installed)
            heldout=checks(task,installed,True)
            passed=fixed['passed'] and heldout['passed']
            if not passed:
                if target.read_text()==source:
                    temp.write_bytes(initial)
                    temp.replace(target)
                return {'passed':False,'evidence':{'rollback':True,'fixed':fixed,'holdout':heldout}}
            return {'passed':True,'evidence':{'live_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                    'rollback_path':str(path/'rollback.py'),'installed_checks':fixed,'installed_holdout':heldout}}
        result=run_project(record['goal'],plan=plan,read=lambda task:{'bot_status.py':initial.decode()},
            propose=propose,check=checks,review=lambda task,files:checks(task,files,True),promote=promote,
            maximum_attempts=maximum_attempts,budget_seconds=budget_seconds,checkpoint=checkpoint,event=event,checker=checker)
        record.update(status='completed' if result['passed'] else 'needs_checker',result=result,completed_at=time.time())
        if result['passed']:
            write_json(path/'training-candidate.json',{'model':record['model'],'goal':record['goal'],
                'scope':record['scope'],'baseline_sha256':baseline,'source':target.read_text(encoding='utf-8'),
                'evaluation':result,'training_approved':False,'model_weights_changed':False})
        LessonStore(settings.data_dir/'coding'/'lessons').ingest_project(record)
    except Exception as exc:
        record.update(status='blocked',error=type(exc).__name__,completed_at=time.time())
        reasons={
            'practice deferred because local inference is occupied':'Local inference is occupied. Submit again when Shana is idle.',
            'local inference availability could not be established':'Could not verify local inference availability.',
            'Incomplete plan':'The model returned an incomplete plan; no code was applied.',
            'Goal requires a broader registered scope':'This goal needs a broader coding scope than Discord status.',
            'Owner stopped work':'Stopped by owner at a safe boundary.',
        }
        record['reason']=reasons.get(str(exc),'Execution stopped safely; inspect the saved check evidence.')
    finally:
        latest=json.loads((path/'state.json').read_text())
        record['desired_state']=latest.get('desired_state','running')
        write_json(path/'state.json',record)
        os.close(fd)
        lock.unlink()
    return record


def main():
    from ..config import PROJECT_ROOT,settings
    parser=argparse.ArgumentParser()
    parser.add_argument('--request-id',required=True)
    args=parser.parse_args()
    result=execute_job(settings.data_dir/'coding'/'projects',args.request_id,PROJECT_ROOT)
    print(json.dumps({'id':result['id'],'status':result['status']}))

if __name__=='__main__': main()
