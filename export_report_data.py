"""Rebuild report plotting tables from the 684 explicitly selected archived runs."""
from pathlib import Path
from collections import defaultdict
import csv,hashlib,json,statistics
ROOT=Path(__file__).resolve().parent

def write_csv(path, rows):
    if not rows:return
    fields=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

def export():
    manifest=json.loads((ROOT/'evidence/manifest.json').read_text())
    out=ROOT/'Plotting_Package/data';out.mkdir(exist_ok=True)
    runs=[];records=[];examples={};groups=defaultdict(list);arrival=[]
    for entry in manifest['runs']:
        path=ROOT/entry['path'];raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError(f'Archived evidence changed: {path}')
        d=json.loads(raw.decode('utf-8-sig'));tasks=d['tasks'];done=[t for t in tasks if t['status'] in ('COMPLETED','COMPLETED_LOCALLY')]
        if d['processed']!=d['task_count'] or len(tasks)!=d['task_count']:raise ValueError(f'Incomplete record: {path}')
        mean=lambda values:statistics.mean(values) if values else None
        full=d['full_decisions'];dec=d['decisions'];family=entry['family']
        r=dict(family=family,network=entry['network'],method=d['method'],seed=d['seed'],wolves=d['wolves'],iterations=d['iterations'],weights='/'.join(map(str,d['weights'])),task_count=d['task_count'],json_file=entry['path'],
            completed=len(done),rejected=sum(t['status']=='REJECTED' for t in tasks),completion_pct=100*len(done)/len(tasks),deadline_pct=100*sum(t.get('deadline_met') is True for t in tasks)/len(tasks),
            analytical_delay_s=mean([t['analytical_delay_s'] for t in done]),observed_delay_s=mean([t['observed_delay_s'] for t in done]),energy_j=sum(t.get('total_system_energy_j') or 0 for t in tasks),
            decision_ms=mean([v['total_decision_ms'] for v in full]) if d['method']!='local_iot' else None,
            verification_attempts=mean([v['final_verification_attempts'] for v in full]) if full and 'final_verification_attempts' in full[0] else None,
            evaluation_requests=sum(v.get('fitness_evaluations',0) for v in dec),unique_calculations=sum(v.get('unique_fitness_evaluations',0) for v in dec))
        if family=='arrival':
            # Model is encoded in archived filenames; original means the one-arrival-per-tick model.
            r['arrival_model']='poisson_matched' if '_poisson_matched_' in path.name else ('poisson_05' if '_poisson_05_' in path.name else 'original')
            arrival.append(r)
        else:
            groups[(family,r['network'],r['method'],r['wolves'],r['iterations'],r['weights'],r['task_count'])].append(r)
        runs.append(r)
        if family=='optimality':records.extend({'network':r['network'],'seed':d['seed'],**v} for v in d['optimality_records'])
        if family=='comparison' and d['method']=='dgwo':examples.setdefault(r['network'],d)
    summaries=defaultdict(list)
    metrics=('completed','rejected','completion_pct','deadline_pct','analytical_delay_s','observed_delay_s','energy_j','decision_ms','verification_attempts','evaluation_requests','unique_calculations')
    def summary(key,values,fields):
        row=dict(zip(fields,key));row['runs']=len(values)
        for m in metrics:
            nums=[v[m] for v in values if v[m] is not None]
            row[m+'_mean']=statistics.mean(nums) if nums else None
            row[m+'_sd']=statistics.stdev(nums) if len(nums)>1 else None
        return row
    for key,values in groups.items():summaries[key[0]].append(summary(key[1:],values,('network','method','wolves','iterations','weights','task_count')))
    ag=defaultdict(list)
    for r in arrival:ag[(r['network'],r['method'],r['arrival_model'])].append(r)
    summaries['arrival']=[summary(k,v,('network','method','arrival_model')) for k,v in ag.items()]
    for family,rows in summaries.items():write_csv(out/(family+'.csv'),rows)
    write_csv(out/'runs.csv',runs);write_csv(out/'optimality_records.csv',records)
    (out/'examples.json').write_text(json.dumps(examples,indent=2))
    (out/'manifest.json').write_text(json.dumps({'counts':manifest['counts'],'runs':len(runs),'task_submissions':sum(r['task_count'] for r in runs),'source_manifest':'evidence/manifest.json'},indent=2))
    print(f'PASS: {len(runs)} runs; {sum(r["task_count"] for r in runs)} tasks; plotting data: {out}')
    return out
if __name__=='__main__':export()
