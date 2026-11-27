"""Reproducible experiment runner; new outputs never replace archived evidence."""
import argparse
import csv
import hashlib
import itertools
import json
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NETWORKS = ('C20', 'C50', 'C100', 'C200')
SUPPORTED_NETWORKS = ('C10', 'C20', 'C50', 'C100', 'C200')
OUTPUT_ROOT = ROOT/'results'
METHODS = ('dgwo', 'greedy', 'pso', 'random', 'gwo_no_filter', 'local_iot')
SEEDS = (11, 22, 33, 44, 55)
WEIGHTS = {'default': (.4,.4,.2), 'delay_priority': (.6,.2,.2),
           'energy_priority': (.2,.6,.2), 'cpu_priority': (.2,.2,.6),
           'delay_cpu_tradeoff': (.4,.3,.3)}
EXPERIMENTS = ('baseline','robustness','filtering','parameters','weights','workload','optimality')

def conditions(experiment, smoke=False):
    nets = NETWORKS
    if experiment == 'baseline':
        for n,m,r in itertools.product(nets, METHODS, range(1, 2 if smoke else 11)):
            yield n,m,42,10,5,WEIGHTS['default'],20,f'repeat{r}'
    elif experiment == 'robustness':
        for n,m,s in itertools.product(nets, METHODS[:-1], (11,) if smoke else range(11,111,11)):
            yield n,m,s,10,5,WEIGHTS['default'],20,'seed'
    elif experiment == 'filtering':
        for n,m,s in itertools.product(nets, ('dgwo','dgwo_unfiltered_local_first'), (11,) if smoke else SEEDS):
            yield n,m,s,10,5,WEIGHTS['default'],20,'seed'
    elif experiment == 'parameters':
        for n,w,i,s in itertools.product(('C20','C200'), (5,10,20), (5,10,20), (11,) if smoke else (11,22,33)):
            yield n,'dgwo',s,w,i,WEIGHTS['default'],20,'seed'
    elif experiment == 'weights':
        for n,s in itertools.product(('C20','C200'), (11,) if smoke else SEEDS):
            for name,weights in WEIGHTS.items():
                yield n,'dgwo',s,10,5,weights,20,name
    elif experiment == 'workload':
        for count,s in itertools.product((20,50,100,200), (11,) if smoke else SEEDS):
            yield 'C100','dgwo',s,10,5,WEIGHTS['default'],count,'seed'
    else:
        for n,s in itertools.product(nets, (55,) if smoke else SEEDS):
            yield n,'dgwo',s,10,5,WEIGHTS['default'],20,'seed'

def write_csv(path, rows):
    if not rows: return
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def metrics(data):
    completed=[t for t in data['tasks'] if t['status'] in {'COMPLETED','COMPLETED_LOCALLY'}]
    total=data['task_count']
    def mean(values): return statistics.mean(values) if values else None
    return dict(completed=len(completed),rejected=sum(t['status']=='REJECTED' for t in data['tasks']),
        completion_pct=100*len(completed)/total,
        deadline_pct=100*sum(t.get('deadline_met') is True for t in data['tasks'])/total,
        analytical_delay_s=mean([t['analytical_delay_s'] for t in completed]),
        observed_delay_s=mean([t['observed_delay_s'] for t in completed]),
        energy_j=sum(t.get('total_system_energy_j') or 0 for t in data['tasks']),
        decision_ms=mean([v['total_decision_ms'] for v in data['full_decisions']]) if data['method']!='local_iot' else None)

def run(experiment,smoke=False):
    folder=OUTPUT_ROOT/f'{experiment}_{"smoke_" if smoke else ""}{datetime.now():%Y%m%d_%H%M%S_%f}'
    folder.mkdir(parents=True)
    script={'filtering':'main_ablation_local_first.py','workload':'main_workload_scalability_5iter.py',
            'optimality':'main_optimality_verification_5iter.py'}.get(experiment,'main_final_local_first.py')
    files=sorted(ROOT.glob('*.py'))+sorted(ROOT.glob('C*.json'))
    (folder/'provenance.json').write_text(json.dumps({'python':sys.version,'executable':sys.executable,
        'experiment':experiment,'networks':list(NETWORKS),'smoke_only':smoke,'files':{p.name:digest(p) for p in files}},indent=2))
    rows=[]; traces={}
    for n,m,seed,w,i,weights,count,label in conditions(experiment,smoke):
        tag=f'{n}_{m}_s{seed}_w{w}_i{i}_n{count}_{label}'
        dest=folder/f'{tag}.json';log=folder/f'{tag}.txt'
        command=[sys.executable,'-u',str(ROOT/script),'--dataset',str(ROOT/f'{n}.json'),'--method',m,
            '--optimizer-seed',str(seed),'--arrival-seed',str(seed),'--wolves',str(w),'--iterations',str(i),
            '--weights',','.join(map(str,weights)),'--summary-json',str(dest)]
        if experiment=='workload':command+=['--task-count',str(count)]
        print(f'Running {experiment}: {tag}',flush=True)
        # Isolate disposable EdgeSimPy logs for each run.
        runtime=folder/f'_{tag}_runtime';runtime.mkdir()
        with log.open('wb') as f:
            result=subprocess.run(command,cwd=runtime,stdout=f,stderr=subprocess.STDOUT)
        if result.returncode or not dest.is_file():raise RuntimeError(f'Failed; inspect {log}')
        data=json.loads(dest.read_text(encoding='utf-8-sig'))
        if (data['processed']!=count or len(data['tasks'])!=count or
            {t['task_id'] for t in data['tasks']}!=set(range(1,count+1)) or
            any(t['status'] not in {'COMPLETED','COMPLETED_LOCALLY','REJECTED'} for t in data['tasks'])):
            raise RuntimeError(f'Incomplete task accounting: {dest}')
        for t in data['tasks']:
            if t['status']=='COMPLETED_LOCALLY':
                a=t.get('source_resource_audit',{})
                if not (a.get('source_resources_released') is True and
                    a['source_cpu_demand_after_release']==a['source_cpu_demand_before'] and
                    a['source_memory_demand_after_release_mb']==a['source_memory_demand_before_mb']):
                    raise RuntimeError(f'Source reservation/release audit failed: {dest}')
        key=(n,seed,count)
        if key in traces and traces[key]!=data['arrival_trace']:raise RuntimeError(f'Unmatched arrivals: {dest}')
        traces[key]=data['arrival_trace']
        rows.append(dict(network=n,method=m,seed=seed,wolves=w,iterations=i,
            weights='/'.join(map(str,weights)),task_count=count,label=label,**metrics(data),json_file=dest.name))
        write_csv(folder/'runs.csv',rows)
        # Disposable logs are regenerated; the authoritative text/JSON remain.
        import shutil
        shutil.rmtree(runtime)
    groups=defaultdict(list)
    for row in rows:
        groups[(row['network'],row['method'],row['wolves'],row['iterations'],row['weights'],row['task_count'])].append(row)
    summary=[]
    for key,values in groups.items():
        row=dict(zip(('network','method','wolves','iterations','weights','task_count'),key));row['runs']=len(values)
        for metric in ('completed','completion_pct','deadline_pct','analytical_delay_s','observed_delay_s','energy_j','decision_ms'):
            nums=[v[metric] for v in values if v[metric] is not None]
            row[metric+'_mean']=statistics.mean(nums) if nums else None
            row[metric+'_sd']=statistics.stdev(nums) if len(nums)>1 else None
        summary.append(row)
    write_csv(folder/'summary.csv',summary)
    print(f'PASS: {len(rows)} runs; outputs: {folder}',flush=True)
    return folder

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--experiment',choices=EXPERIMENTS+('all',),required=True)
    p.add_argument('--smoke',action='store_true',help='Reduced verification only; not final statistical evidence.')
    p.add_argument('--networks',nargs='+',choices=SUPPORTED_NETWORKS,default=list(NETWORKS),help='Networks for baseline, robustness, filtering and optimality; sensitivity and workload retain their original settings.')
    p.add_argument('--output-root',type=Path,default=ROOT/'results')
    args=p.parse_args()
    if len(set(args.networks))!=len(args.networks):p.error('Network names must not repeat.')
    NETWORKS=tuple(args.networks)
    OUTPUT_ROOT=args.output_root.resolve()
    for experiment in EXPERIMENTS if args.experiment=='all' else (args.experiment,):run(experiment,args.smoke)
