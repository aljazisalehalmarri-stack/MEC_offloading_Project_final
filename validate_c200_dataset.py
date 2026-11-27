"""Read-only structural and connectivity checks for the C200 dataset."""
from pathlib import Path
from collections import Counter,defaultdict,deque
import argparse,json,math

def validate(d):
    expected={'User':200,'EdgeServer':220,'NetworkSwitch':220,'BaseStation':220,'NetworkLink':420}
    for k,n in expected.items():assert len(d[k])==n,(k,len(d[k]),n)
    idx={k:{x['attributes']['id']:x for x in d[k]} for k in expected}
    for k,n in expected.items():assert set(idx[k])==set(range(1,n+1)),k+' nonconsecutive or duplicate IDs'
    peers=[x for x in d['EdgeServer'] if x['attributes']['role']=='edge_node'];hubs=[x for x in d['EdgeServer'] if x['attributes']['role']=='cluster_server']
    assert len(peers)==200 and len(hubs)==20
    assert Counter(x['attributes']['cluster_id'] for x in peers)==Counter({c:10 for c in range(1,21)})
    for node in peers+hubs:
        a=node['attributes'];i=a['id'];c=a['cluster_id'];assert c==((i-1)//10+1 if i<=200 else i-200)
        assert node['relationships']['network_switch']['id']==i and node['relationships']['base_station']['id']==i
        assert idx['NetworkSwitch'][i]['relationships']['edge_servers']==[{'class':'EdgeServer','id':i}]
        assert idx['BaseStation'][i]['relationships']['network_switch']['id']==i
        assert a['cpu_demand']==a['memory_demand']==0 and a['remaining_energy_j']>0
    for u in d['User']:
        i=u['attributes']['id'];hub=201+(i-1)//10;assert u['relationships']['base_station']['id']==hub
        assert {'class':'User','id':i} in idx['BaseStation'][hub]['relationships']['users']
    graph=defaultdict(set);pairs=set();intra=inter=0
    for e in d['NetworkLink']:
        a=e['attributes'];ns=[x['id'] for x in e['relationships']['nodes']];u,v=ns
        assert u in idx['NetworkSwitch'] and v in idx['NetworkSwitch'] and u!=v
        edge=tuple(sorted(ns));assert edge not in pairs;pairs.add(edge);graph[u].add(v);graph[v].add(u)
        assert a['bandwidth']==12.5 and a['active'] and a['bandwidth_demand']==0
        if u>200 and v>200:inter+=1;assert math.isclose(a['delay'],.010)
        else:intra+=1;assert math.isclose(a['delay'],.005)
    expected_pairs=set()
    for c in range(20):
        for j in range(10):
            expected_pairs.add(tuple(sorted((201+c,c*10+j+1))));expected_pairs.add(tuple(sorted((c*10+j+1,c*10+(j+1)%10+1))))
        expected_pairs.add(tuple(sorted((201+c,201+(c+1)%20))))
    assert pairs==expected_pairs and intra==400 and inter==20
    seen={1};queue=deque([1])
    while queue:
        for v in graph[queue.popleft()]:
            if v not in seen:seen.add(v);queue.append(v)
    assert len(seen)==220
    assert [x['attributes']['id'] for x in peers if x['attributes']['memory']>=18000]==[13]
    return {'result':'PASS','users':200,'peer_edge_devices':200,'clusters':20,'cluster_servers':20,'switches':220,'base_stations':220,'links':420,'intra_cluster_links':400,'inter_cluster_links':20,'connected_switches':len(seen),'peers_per_cluster':10,'unique_high_memory_peer':13}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--dataset',type=Path,default=Path(__file__).resolve().parent/'C200.json');a=p.parse_args();print(json.dumps(validate(json.loads(a.dataset.read_text(encoding='utf-8-sig'))),indent=2))
