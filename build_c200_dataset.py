"""Extend the existing ten-peer clustered C100 scenario to C200.
python build_c200_dataset.py
Existing datasets are never overwritten. Only C200.json is generated.
"""
from pathlib import Path
import argparse,copy,json

def build(template,n=200):
    if n<20 or n%10:raise ValueError('Use a multiple of ten peers with at least two clusters.')
    clusters=n//10
    data={k:[] for k in template}
    # Use unchanged resource classes from the first ten peers of C100.
    for c in range(clusters):
        for j in range(10):
            old=j+1;node=c*10+j+1
            for kind in ['EdgeServer','NetworkSwitch','BaseStation','User']:
                obj=copy.deepcopy(template[kind][j]);a=obj['attributes'];rel=obj['relationships']
                a['id']=node
                if 'coordinates' in a:a['coordinates'][1]+=2*c
                if 'coordinates_trace' in a:
                    for point in a['coordinates_trace']:point[1]+=2*c
                if kind=='EdgeServer':
                    a['cluster_id']=c+1
                    rel['base_station']['id']=node;rel['network_switch']['id']=node
                    # Preserve the single remote validation device used by the current datasets.
                    if node==13:a['memory']=template['EdgeServer'][12]['attributes']['memory']
                elif kind=='NetworkSwitch':
                    rel['edge_servers'][0]['id']=node;rel['base_station']['id']=node
                elif kind=='BaseStation':
                    rel['edge_servers'][0]['id']=node;rel['network_switch']['id']=node;rel['users']=[]
                else:rel['base_station']['id']=n+c+1
                data[kind].append(obj)
    for c in range(clusters):
        hub=n+c+1
        for kind in ['EdgeServer','NetworkSwitch','BaseStation']:
            obj=copy.deepcopy(template[kind][100]);a=obj['attributes'];rel=obj['relationships'];a['id']=hub;a['coordinates'][1]+=2*c
            if kind=='EdgeServer':a['cluster_id']=c+1;rel['base_station']['id']=hub;rel['network_switch']['id']=hub
            elif kind=='NetworkSwitch':rel['edge_servers'][0]['id']=hub;rel['base_station']['id']=hub
            else:
                rel['edge_servers'][0]['id']=hub;rel['network_switch']['id']=hub
                # Make the inverse user relationship agree with all ten users' source mappings.
                rel['users']=[{'class':'User','id':c*10+j+1} for j in range(10)]
            data[kind].append(obj)
    def link(a,b,delay):
        obj=copy.deepcopy(template['NetworkLink'][0]);obj['attributes']['id']=len(data['NetworkLink'])+1;obj['attributes']['delay']=delay
        obj['relationships']['nodes']=[{'class':'NetworkSwitch','id':a},{'class':'NetworkSwitch','id':b}];data['NetworkLink'].append(obj)
    for c in range(clusters):
        for j in range(10):link(n+c+1,c*10+j+1,.005)
    for c in range(clusters):
        for j in range(10):link(c*10+j+1,c*10+(j+1)%10+1,.005)
    # Two-cluster datasets use one hub link rather than duplicate reciprocal edges.
    for c in range(clusters if clusters>2 else 1):link(n+c+1,n+(c+1)%clusters+1,.010)
    for k in data:
        if k not in ['EdgeServer','NetworkSwitch','BaseStation','User','NetworkLink']:data[k]=copy.deepcopy(template[k])
    return data

def main():
    root=Path(__file__).resolve().parent
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--template',type=Path,default=root/'C100.json');p.add_argument('--out',type=Path,default=root/'C200.json');a=p.parse_args()
    data=build(json.loads(a.template.read_text(encoding='utf-8-sig')))
    a.out.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8');print('Created',a.out)
if __name__=='__main__':main()
