"""Run the final report protocol. Archived evidence is never overwritten."""
from pathlib import Path
from datetime import datetime
import argparse,subprocess,sys
ROOT=Path(__file__).resolve().parent
FAMILIES=('comparison','robustness','filtering','parameters','weights','workload','optimality','arrival')
NETS=('C20','C50','C100','C200')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--experiment',choices=FAMILIES+('all',),default='all')
    p.add_argument('--smoke',action='store_true',help='Reduced verification; not final statistical evidence.')
    a=p.parse_args()
    out=ROOT/'generated_results'/datetime.now().strftime('%Y%m%d_%H%M%S_%f');out.mkdir(parents=True)
    for family in FAMILIES if a.experiment=='all' else (a.experiment,):
        if family in ('comparison','robustness'):
            cmd=[sys.executable,'-u',str(ROOT/'run_direct_comparison.py'),'--mode','quick' if a.smoke else family,'--networks',*NETS,'--output-root',str(out)]
        elif family=='arrival':
            cmd=[sys.executable,'-u',str(ROOT/'Arrival_Trial/run_arrival_trial.py'),'--project',str(ROOT)]
            if a.smoke:cmd+=['--networks','C20','--seeds','11','--methods','dgwo']
        else:
            cmd=[sys.executable,'-u',str(ROOT/'run_project_experiments.py'),'--experiment',family,'--networks',*NETS,'--output-root',str(out)]
            if a.smoke:cmd+=['--smoke']
        print('Experiment:',family,flush=True)
        subprocess.run(cmd,cwd=ROOT,check=True)
    print('PASS. New results:',out,'(arrival outputs are under Arrival_Trial/outputs)',flush=True)
if __name__=='__main__':main()
