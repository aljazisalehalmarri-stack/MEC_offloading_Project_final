"""Run an isolated comparison of the proposed policy and direct baselines."""
import argparse,csv,hashlib,itertools,json,math,shutil,statistics,subprocess,sys
from collections import defaultdict
from datetime import datetime
from importlib.metadata import version,PackageNotFoundError
from pathlib import Path
ROOT=Path(__file__).resolve().parent
METHODS=('dgwo','greedy','pso','random','gwo_no_filter','local_iot')
NETWORKS=('C20','C50','C100','C200')
SUPPORTED_NETWORKS=('C10','C20','C50','C100','C200')
SUCCESS={'COMPLETED','COMPLETED_LOCALLY'}

def write_csv(path,rows):
    if not rows:return
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def measure(data):
    done=[t for t in data['tasks'] if t['status'] in SUCCESS]
    mean=lambda a:statistics.mean(a) if a else None
    return dict(completed=len(done),rejected=sum(t['status']=='REJECTED' for t in data['tasks']),
        completion_pct=100*len(done)/data['task_count'],
        deadline_pct=100*sum(t.get('deadline_met') is True for t in data['tasks'])/data['task_count'],
        analytical_delay_s=mean([t['analytical_delay_s'] for t in done]),
        observed_delay_s=mean([t['observed_delay_s'] for t in done]),
        energy_j=sum(t.get('total_system_energy_j') or 0 for t in data['tasks']),
        decision_ms=mean([t['total_decision_ms'] for t in data['full_decisions']]) if data['method']!='local_iot' else None,
        final_verification_attempts=mean([t['final_verification_attempts'] for t in data['full_decisions']]) if data['method']!='local_iot' else None,
        optimizer_evaluation_requests=sum(t.get('fitness_evaluations',0) for t in data['decisions']),
        optimizer_unique_fitness_calculations=sum(t.get('unique_fitness_evaluations',0) for t in data['decisions']))

def validate(data,network,method,seed):
    assert data['protocol']['name']=='direct_baselines_trial_v1'
    assert data['task_count']==data['processed']==len(data['tasks'])==20
    assert data['seed']==data['arrival_seed']==seed
    assert data['method']==method and data['wolves']==10 and data['iterations']==5
    assert data['weights']==[.4,.4,.2]
    assert {t['task_id'] for t in data['tasks']}==set(range(1,21))
    assert not data['pending_edge_tasks'] and not data['pending_source_tasks']
    assert all(t['status'] in SUCCESS|{'REJECTED'} for t in data['tasks'])
    assert len(data['arrival_trace'])==20
    for t in data['tasks']:
        if t['status'] in SUCCESS:
            assert all(math.isfinite(t[k]) and t[k]>=0 for k in ('analytical_delay_s','observed_delay_s','total_system_energy_j'))
            assert math.isclose(t['total_system_energy_j'],t['computation_energy_j']+t.get('transmission_energy_j',0),abs_tol=1e-9)
        if t['status']=='COMPLETED_LOCALLY':
            a=t['source_resource_audit'];assert a['source_resources_released'] is True
            assert a['source_cpu_demand_after_release']==a['source_cpu_demand_before']
            assert a['source_memory_demand_after_release_mb']==a['source_memory_demand_before_mb']
        if t['status']=='REJECTED':assert t['rejection_reason']
    if method in ('greedy','pso','random','gwo_no_filter'):
        assert len(data['full_decisions'])==20
        for d in data['full_decisions']:
            assert d['search_stages']==['NETWORK_WIDE']
            assert d['edge_nodes_before']==d['candidates_after_preselection']==d['candidates_after_checking']==int(network[1:])
            assert not d['preliminary_filtering_used'] and not d['preselection_revalidation_used']
            assert d['final_verification_attempts']>=1

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('quick','comparison','robustness'),default='quick')
    p.add_argument('--seed',type=int,default=42,help='Quick and fixed-seed modes only.')
    p.add_argument('--networks',nargs='+',choices=SUPPORTED_NETWORKS,default=list(NETWORKS),help='Networks to run; C10 remains available.')
    p.add_argument('--output-root',type=Path,default=ROOT/'trial_results')
    a=p.parse_args()
    if len(set(a.networks))!=len(a.networks):p.error('Network names must not repeat.')
    seeds=tuple(range(11,111,11)) if a.mode=='robustness' else (a.seed,)
    methods=METHODS[:-1] if a.mode=='robustness' else METHODS
    repeats=10 if a.mode=='comparison' else 1
    folder=a.output_root.resolve()/f'direct_{a.mode}_{datetime.now():%Y%m%d_%H%M%S_%f}'
    folder.mkdir(parents=True)
    packages={}
    for name in ('edge_sim_py','Mesa','networkx','msgpack','numpy','pandas'):
        try:packages[name]=version(name)
        except PackageNotFoundError:packages[name]='not recorded'
    files=sorted(ROOT.glob('*.py'))+sorted(ROOT.glob('C*.json'))
    (folder/'provenance.json').write_text(json.dumps({'protocol':'direct_baselines_trial_v1',
        'mode':a.mode,'networks':a.networks,'python':sys.version,'packages':packages,
        'full_statistical_evaluation':a.mode!='quick',
        'files':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in files}},indent=2))
    rows=[];traces={};profiles={}
    for network,seed,method,repeat in itertools.product(a.networks,seeds,methods,range(1,repeats+1)):
        tag=f'{network}_{method}_s{seed}_r{repeat}';dest=folder/(tag+'.json');log=folder/(tag+'.txt')
        command=[sys.executable,'-u',str(ROOT/'main_direct_baselines.py'),'--dataset',str(ROOT/(network+'.json')),
            '--method',method,'--optimizer-seed',str(seed),'--arrival-seed',str(seed),
            '--wolves','10','--iterations','5','--weights','0.4,0.4,0.2','--summary-json',str(dest)]
        runtime=folder/('_'+tag+'_runtime');runtime.mkdir()
        print('Running '+tag,flush=True)
        with log.open('wb') as f:r=subprocess.run(command,cwd=runtime,stdout=f,stderr=subprocess.STDOUT)
        if r.returncode or not dest.exists():raise RuntimeError(f'Failed run: {log}')
        data=json.loads(dest.read_text(encoding='utf-8-sig'));validate(data,network,method,seed)
        key=(network,seed)
        if key in traces:assert data['arrival_trace']==traces[key],'Unmatched task arrivals'
        if key in profiles:assert data['task_profiles']==profiles[key],'Unmatched task profiles'
        traces[key]=data['arrival_trace'];profiles[key]=data['task_profiles']
        rows.append(dict(network=network,method=method,seed=seed,repeat=repeat,**measure(data),json_file=dest.name))
        write_csv(folder/'runs.csv',rows);shutil.rmtree(runtime)
    summary=[];groups=defaultdict(list)
    for r in rows:groups[(r['network'],r['method'])].append(r)
    for (network,method),values in groups.items():
        r=dict(network=network,method=method,runs=len(values))
        for metric in measure(data):
            nums=[v[metric] for v in values if v[metric] is not None]
            r[metric+'_mean']=statistics.mean(nums) if nums else None
            r[metric+'_sd']=statistics.stdev(nums) if len(nums)>1 else None
        summary.append(r)
    write_csv(folder/'summary.csv',summary)
    (folder/'validation.json').write_text(json.dumps({'validated_runs':len(rows),'matched_arrival_traces':True,
        'matched_task_profiles_within_network_and_seed':True,'direct_baselines_kept_all_edge_nodes':True,
        'terminal_task_accounting':True,'source_resource_release':True,'mode':a.mode},indent=2))
    archive=shutil.make_archive(str(folder),'zip',root_dir=folder)
    print(f'PASS: {len(rows)} runs. Results: {archive}',flush=True)
if __name__=='__main__':main()
