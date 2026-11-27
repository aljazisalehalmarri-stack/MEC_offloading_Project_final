"""Isolated arrival-model comparison. Never modifies project source files."""
import argparse,collections,csv,hashlib,json,math,os,shutil,statistics,subprocess,sys,ast
from pathlib import Path
from datetime import datetime

def write_csv(path,rows):
    if not rows:return
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def patch_main(text):
    # Split the existing callback: completion/release runs once per tick,
    # while the unchanged assignment body runs once per arrival.
    tree=ast.parse(text)
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='task_arrival')
    lines=text.splitlines(keepends=True)
    start=fn.lineno-1;end=fn.end_lineno
    body=''.join(lines[start:end])
    marker='    # Get next task\n'
    if body.count(marker)!=1:raise RuntimeError('Unsupported main: task marker differs.')
    pos=body.index(marker)
    prefix=body[:pos]
    gate='    if arrival_rng.random() >= arrival_probability:'
    if prefix.count(gate)!=1:raise RuntimeError('Unsupported main: arrival gate differs.')
    gatepos=prefix.index(gate)
    prefix=prefix[:gatepos]
    # Assignment body starts at task = task_list, preserving all return paths.
    taskpos=body.index('    task = task_list[',pos)
    assignment=body[taskpos:]
    new=prefix+'''    mode = os.environ.get("ARRIVAL_TRIAL_MODEL", "original")
    if mode == "original":
        count = int(arrival_rng.random() < arrival_probability)
    else:
        rate = arrival_probability if mode == "poisson_matched" else arrival_rate
        # Exact Poisson count sampler for the small rates used here.
        threshold = math.exp(-rate)
        product = 1.0
        count = -1
        while product > threshold:
            count += 1
            product *= arrival_rng.random()
    count = min(count, len(task_list) - parameters["next_task_index"])
    print(f"ARRIVAL BATCH | step={current_step} | model={mode} | count={count}")
    for _ in range(count):
        _trial_assign_one(parameters)


def _trial_assign_one(parameters):
    current_step = parameters["current_step"]
    task_list = parameters["tasks"]
    next_task_index = parameters["next_task_index"]
'''+assignment
    output=''.join(lines[:start])+new+'\n'+''.join(lines[end:])
    output='import os\n'+output
    # Avoid exporting an inaccurate old arrival-model banner in diagnostics.
    output=output.replace('At most one arrival per 1-second tick (Poisson approximation)',
                          'Arrival model configured by isolated Arrival_Trial runner')
    ast.parse(output)
    return output

def measure(d):
    tasks=d['tasks'];done=[t for t in tasks if t['status'] in ('COMPLETED','COMPLETED_LOCALLY')]
    avg=lambda xs:statistics.mean(xs) if xs else None
    counts=collections.Counter(t['arrival_step'] for t in d['arrival_trace'])
    return dict(completed=len(done),rejected=sum(t['status']=='REJECTED' for t in tasks),
        local_peer=sum(t['status']=='COMPLETED' and t['scope']=='LOCAL' for t in tasks),
        remote_peer=sum(t['status']=='COMPLETED' and t['scope']=='GLOBAL' for t in tasks),
        source=sum(t['status']=='COMPLETED_LOCALLY' for t in tasks),
        deadlines_met=sum(t.get('deadline_met') is True for t in tasks),
        analytical_delay_s=avg([t['analytical_delay_s'] for t in done]),
        observed_delay_s=avg([t['observed_delay_s'] for t in done]),
        energy_j=sum(t.get('total_system_energy_j') or 0 for t in tasks),
        decision_ms=avg([x['total_decision_ms'] for x in d['full_decisions']]),
        max_arrivals_per_tick=max(counts.values()),multi_arrival_ticks=sum(v>1 for v in counts.values()),
        first_arrival_step=min(counts),last_arrival_step=max(counts))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',type=Path,default=Path(__file__).resolve().parent.parent)
    p.add_argument('--networks',nargs='+',default=['C20','C200'])
    p.add_argument('--seeds',nargs='+',type=int,default=[11,42])
    p.add_argument('--methods',nargs='+',choices=['dgwo','greedy','pso','random','gwo_no_filter'],default=['dgwo','greedy','pso','random','gwo_no_filter'])
    a=p.parse_args();root=a.project.resolve()
    mains=['main_final_local_first.py','main_direct_baselines.py']
    for name in mains+[n+'.json' for n in a.networks]:
        if not (root/name).is_file():raise FileNotFoundError(root/name)
    folder=Path(__file__).resolve().parent/'outputs'/datetime.now().strftime('%Y%m%d_%H%M%S_%f');folder.mkdir(parents=True)
    originals={x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in root.glob('*.py')}
    runtime=folder/'isolated_project';runtime.mkdir()
    for pat in ('*.py','C*.json'):
        for src in root.glob(pat):shutil.copy2(src,runtime/src.name)
    for name in mains:
        text=(runtime/name).read_text(encoding='utf-8-sig');(runtime/name).write_text(patch_main(text),encoding='utf-8')
    (folder/'provenance.json').write_text(json.dumps(dict(python=sys.version,project=str(root),source_hashes=originals,
        models={'original':'Bernoulli p=1-exp(-0.5)', 'poisson_matched':'Poisson mean 1-exp(-0.5) per tick', 'poisson_05':'Poisson mean 0.5 per tick'},
        note='20 predefined tasks; task order retained; same-tick assignments sequential with resource updates; release callback once per tick; final batch capped to remaining tasks.'),indent=2))
    rows=[];traces={}
    for network in a.networks:
      for seed in a.seeds:
       for model in ('original','poisson_matched','poisson_05'):
        for method in a.methods:
            tag=f'{network}_{method}_{model}_s{seed}';dest=folder/(tag+'.json');log=folder/(tag+'.txt')
            script=mains[0] if method=='dgwo' else mains[1]
            cmd=[sys.executable,'-u',str(runtime/script),'--dataset',str(runtime/(network+'.json')),'--method',method,'--wolves','10','--iterations','5','--weights','0.4,0.4,0.2','--optimizer-seed',str(seed),'--arrival-seed',str(seed),'--summary-json',str(dest)]
            print('Running',tag,flush=True);work=folder/('_'+tag);work.mkdir()
            with log.open('wb') as f:r=subprocess.run(cmd,cwd=work,env={**os.environ,'ARRIVAL_TRIAL_MODEL':model},stdout=f,stderr=subprocess.STDOUT)
            if r.returncode or not dest.exists():raise RuntimeError(f'Run failed: {log}')
            d=json.loads(dest.read_text(encoding='utf-8-sig'))
            assert d['processed']==d['task_count']==len(d['tasks'])==20,tag
            assert len(d['arrival_trace'])==20,tag
            assert all(t['status'] in ('COMPLETED','COMPLETED_LOCALLY','REJECTED') for t in d['tasks']),tag
            assert not d.get('pending_edge_tasks',0) and not d.get('pending_source_tasks',0),tag
            for task in d['tasks']:
                if task['status']=='COMPLETED_LOCALLY':assert task['source_resource_audit']['source_resources_released'] is True,tag
            key=(network,seed,model)
            if key in traces:assert traces[key]==d['arrival_trace'],f'Unpaired arrivals: {tag}'
            traces[key]=d['arrival_trace']
            rows.append(dict(network=network,seed=seed,arrival_model=model,method=method,**measure(d)))
            write_csv(folder/'runs.csv',rows);shutil.rmtree(work)
    summary=[]
    groups=collections.defaultdict(list)
    for row in rows:groups[(row['network'],row['arrival_model'],row['method'])].append(row)
    for (n,m,method),values in groups.items():
        row=dict(network=n,arrival_model=m,method=method,runs=len(values))
        for metric in measure(d):
            nums=[v[metric] for v in values if v[metric] is not None]
            row[metric+'_mean']=statistics.mean(nums) if nums else None
            row[metric+'_sd']=statistics.stdev(nums) if len(nums)>1 else None
        summary.append(row)
    write_csv(folder/'summary.csv',summary)
    assert originals=={x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in root.glob('*.py')},'Original project changed'
    print(f'PASS: {len(rows)} runs. Original project unchanged. Results: {folder}',flush=True)
    shutil.make_archive(str(folder),'zip',folder)

if __name__=='__main__':main()
