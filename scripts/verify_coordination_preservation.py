"""Read-only source/PID/HTTP preservation check for an isolated Gamma trial."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib import request,error


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--expect',action='append',default=[])
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    project=Path(__file__).resolve().parents[1]
    if os.name!='posix' or project.name in {'gamma-main','gamma-modular-phase1'}:
        raise RuntimeError('isolated Gamma candidate required')
    baseline=json.loads(args.baseline.read_text())
    counts={}
    for name,files in baseline.items():
        if name not in {'gamma-main','gamma-modular-phase1'}:raise ValueError('unsupported_baseline_root')
        root=project.parent/name
        for relative,expected in files.items():
            path=root/relative
            if not path.resolve().is_relative_to(root.resolve()):raise ValueError('invalid_baseline_path')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise AssertionError('preserved_source_changed')
        counts[name]=len(files)
    units={'gamma-shana.service','gamma-dashboard.service','gamma-coding-idle.service','gamma-coding-autonomous.service'}
    pids={}
    for binding in args.expect:
        unit,pid=binding.split('=',1)
        if unit not in units:raise ValueError('unsupported_service')
        observed=int(subprocess.check_output(['systemctl','--user','show',unit,'--property=MainPID','--value'],text=True).strip())
        assert observed==int(pid)>0 and Path('/proc',str(observed)).exists(),'production_service_changed'
        pids[unit]=observed
    codes={}
    for port in (8000,8001):
        try:
            with request.urlopen(f'http://127.0.0.1:{port}/health',timeout=5) as response:code=response.status
        except error.HTTPError as exc:code=exc.code
        assert code in {200,401,403},'production_http_unavailable'
        codes[str(port)]=code
    report={'source_hashes_unchanged':counts,'service_pids_unchanged':pids,'health_http_statuses':codes,
        'production_activated':False,'source_baseline_sha256':hashlib.sha256(args.baseline.read_bytes()).hexdigest()}
    args.report.write_text(json.dumps(report,indent=2));args.report.chmod(0o600)
    print(json.dumps(report))


if __name__=='__main__':main()
