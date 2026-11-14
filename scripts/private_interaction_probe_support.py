"""Non-mutating request timing observer for finite private interaction checks."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request


def child(project,state):
    original=urllib.request.urlopen
    def observed(request,*args,**kwargs):
        payload=None
        if isinstance(request,urllib.request.Request) and request.full_url.endswith('/api/generate') and request.data:
            payload=json.loads(request.data)
        started=time.monotonic()
        response=original(request,*args,**kwargs)
        if payload:
            read=response.read
            def measured_read(*values,**options):
                raw=read(*values,**options)
                try:
                    result=json.loads(raw)
                    system=payload.get('system','')
                    shape=payload.get('format')
                    record={'at':time.time(),'transport_seconds':round(time.monotonic()-started,4),
                        'model':payload.get('model'),'kind':'private_draft' if 'Private Coordination Context' in system else 'safety_or_other',
                        'system_sha256':hashlib.sha256(system.encode()).hexdigest(),
                        'schema_sha256':hashlib.sha256(json.dumps(shape,sort_keys=True).encode()).hexdigest(),
                        'keep_alive':payload.get('keep_alive'),'output_characters':len(result.get('response','')),
                        **{key:result.get(key) for key in ('load_duration','prompt_eval_duration','eval_duration','total_duration','prompt_eval_count','eval_count','done_reason')}}
                    with (state/'provider-timings.jsonl').open('a') as stream:
                        stream.write(json.dumps(record)+'\n')
                except (ValueError,TypeError):pass
                return raw
            response.read=measured_read
        return response
    urllib.request.urlopen=observed
    from growth_probe_support import child as normal_child
    normal_child(project,state)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',required=True)
    parser.add_argument('--endpoint',required=True)
    parser.add_argument('--child-state',type=Path,required=True)
    args=parser.parse_args()
    child(Path(__file__).resolve().parents[1],args.child_state)
