"""Reproducible report figures from exported data or the original project JSON files.
Usage: python plot_results.py
       python plot_results.py --project C:\\path\\MEC_offloading_Project
All error bars are sample standard deviations across RUN-LEVEL means.
Fixed-seed repetitions and different-seed experiments are never pooled.
"""
from pathlib import Path
import argparse,csv,json,statistics,math
from collections import defaultdict
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

NETS=['C20','C50','C100','C200']
METHODS=['dgwo','greedy','pso','random','gwo_no_filter']
LABELS={'dgwo':'Proposed DGWO','greedy':'Greedy','pso':'PSO','random':'Random','gwo_no_filter':'GWO without filtering','local_iot':'IoT only'}
COLORS={'dgwo':'#0072B2','greedy':'#666666','pso':'#009E73','random':'#D55E00','gwo_no_filter':'#A45DA5'}
KINDS={'comparison':('trial_results','direct_comparison_'),'robustness':('trial_results','direct_robustness_'),'filtering':('results','filtering_'),'parameters':('results','parameters_'),'weights':('results','weights_'),'workload':('results','workload_'),'optimality':('results','optimality_')}
METRICS=['completed','rejected','completion_pct','deadline_pct','analytical_delay_s','observed_delay_s','energy_j','decision_ms','verification_attempts','evaluation_requests','unique_calculations']

def from_project(root):
    import subprocess, sys
    subprocess.run([sys.executable, str(root / 'export_report_data.py')], check=True)
    return from_snapshot(root / 'Plotting_Package' / 'data')


def from_snapshot(root):
    result={}
    for kind in KINDS:
        with (root/(kind+'.csv')).open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
        for r in rows:
            for k,v in list(r.items()):
                if k in ('wolves','iterations','task_count','runs'):r[k]=int(v)
                elif k.endswith(('_mean','_sd')):r[k]=float(v) if v else None
        result[kind]=rows
    with (root/'optimality_records.csv').open(encoding='utf-8-sig',newline='') as f:records=list(csv.DictReader(f))
    for r in records:r['optimal_match']=str(r['optimal_match']).lower()=='true'
    return result,records

def one(rows,net,method='dgwo',**filters):
    found=[r for r in rows if r['network']==net and r['method']==method and all(r[k]==v for k,v in filters.items())]
    if len(found)!=1:raise ValueError(f'Expected one row for {net}/{method}/{filters}; found {len(found)}')
    return found[0]

def style(ax,grid='y'):
    ax.spines[['top','right']].set_visible(False);ax.grid(axis=grid,color='#DDE2E7',linewidth=.7);ax.set_axisbelow(True)

def save(fig,out,name):
    fig.savefig(out/(name+'.png'),dpi=600,bbox_inches='tight',facecolor='white')
    fig.savefig(out/(name+'.pdf'),bbox_inches='tight')
    fig.savefig(out/(name+'.svg'),bbox_inches='tight')
    plt.close(fig);print('Saved',name)

def panels(data,metric,label,out,name,digits=3,dots=False):
    fig,axes=plt.subplots(2,2,figsize=(10,6.4),layout='constrained',sharey=True)
    for ax,net in zip(axes.flat,NETS):
        rs=[one(data,net,m) for m in METHODS];y=np.arange(len(rs));v=np.array([r[metric+'_mean'] for r in rs]);err=np.array([r[metric+'_sd'] or 0 for r in rs]);colors=[COLORS[m] for m in METHODS]
        if dots:
            for i,(value,e,c) in enumerate(zip(v,err,colors)):ax.errorbar(value,i,xerr=e,fmt='o',color=c,capsize=3,markersize=6)
            lo=min(v-err);hi=max(v+err);span=max(hi-lo,.04);ax.set_xlim(lo-.12*span,hi+.5*span)
        else:
            ax.barh(y,v,xerr=err,color=colors,height=.56,error_kw={'capsize':3,'elinewidth':.8});ax.set_xlim(0,max(v+err)*1.28)
        for i,value in enumerate(v):ax.annotate(f'{value:.{digits}f}',(value+err[i],i),xytext=(5,0),textcoords='offset points',va='center',fontsize=8)
        ax.set_yticks(y,[LABELS[m] for m in METHODS]);ax.set_title(net,loc='left',fontweight='bold');style(ax,'x');ax.tick_params(axis='y',labelleft=True)
    axes[0,0].invert_yaxis();fig.supxlabel(label);save(fig,out,name)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',type=Path);p.add_argument('--data',type=Path,default=Path(__file__).resolve().parent/'data');p.add_argument('--out',type=Path,default=Path('figures'));a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    data,records=from_project(a.project.resolve()) if a.project else from_snapshot(a.data)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.labelsize':11,'axes.titlesize':12,'legend.fontsize':9,'svg.fonttype':'none','pdf.fonttype':42,'savefig.pad_inches':.12})
    panels(data['comparison'],'decision_ms','Mean decision time per submitted task (ms)',a.out,'Fig_6_1_Baseline_Decision_Time')
    panels(data['comparison'],'energy_j','Mean modeled task energy per run (J)',a.out,'Fig_6_2_Baseline_Energy',4,False)
    panels(data['comparison'],'analytical_delay_s','Mean analytical delay of completed tasks (s)',a.out,'Fig_6_3_Baseline_Analytical_Delay',4,True)
    panels(data['comparison'],'observed_delay_s','Mean observed delay of completed tasks (s)',a.out,'Fig_6_4_Baseline_Observed_Delay',3,True)
    fig,ax=plt.subplots(figsize=(7,4),layout='constrained');ms=METHODS+['local_iot'];vals=[one(data['comparison'],'C100',m)['completion_pct_mean'] for m in ms];ax.bar(np.arange(6),vals,color=[COLORS.get(m,'#999999') for m in ms]);ax.set_xticks(range(6),['DGWO','Greedy','PSO','Random','GWO\nno filter','IoT only']);ax.set_ylim(0,112);ax.set_ylabel('Tasks completed within deadline (%)');style(ax)
    for i,v in enumerate(vals):ax.text(i,v+2,f'{v:g}%',ha='center')
    save(fig,a.out,'Fig_6_5_Completion_Rate')
    fig,ax=plt.subplots(figsize=(7,4),layout='constrained');rs=[one(data['comparison'],n) for n in NETS];v=[r['decision_ms_mean'] for r in rs];e=[r['decision_ms_sd'] for r in rs];ax.errorbar(range(4),v,yerr=e,fmt='o-',capsize=4,color=COLORS['dgwo']);ax.set_xticks(range(4),NETS);ax.set_ylim(bottom=0);ax.set_xlabel('Network configuration');ax.set_ylabel('Mean DGWO decision time per task (ms)');style(ax);save(fig,a.out,'Fig_6_6_Network_Scalability')
    panels(data['robustness'],'decision_ms','Mean decision time per task across ten seeds (ms)',a.out,'Fig_6_7_Robustness_Decision_Time')
    panels(data['robustness'],'energy_j','Mean modeled task energy across ten seeds (J) — focused axis',a.out,'Fig_6_8_Robustness_Energy',4,True)
    fig,ax=plt.subplots(figsize=(7.2,4.4),layout='constrained');x=np.arange(4);w=.34
    for shift,method,label,c in [(-w/2,'dgwo','With preliminary filtering','#0072B2'),(w/2,'dgwo_unfiltered_local_first','Without preliminary filtering','#D55E00')]:
        rs=[one(data['filtering'],n,method) for n in NETS];vals=[r['decision_ms_mean'] for r in rs];errs=[r['decision_ms_sd'] for r in rs];ax.bar(x+shift,vals,w,yerr=errs,label=label,color=c,capsize=3)
        for xx,v,e in zip(x+shift,vals,errs):ax.text(xx,v+e+.05,f'{v:.3f}',ha='center',fontsize=8)
    ax.set_xticks(x,NETS);ax.set_ylim(bottom=0);ax.margins(y=.2);ax.set_ylabel('Mean decision time per task (ms)');ax.set_xlabel('Network configuration');ax.legend(loc='upper left',bbox_to_anchor=(0,1.2),frameon=False);style(ax);save(fig,a.out,'Fig_6_9_Filtering_Ablation')
    for metric,label,name in [('decision_ms','Mean decision time per task (ms)','Fig_6_10_Parameter_Timing'),('energy_j','Mean modeled task energy per run (J)','Fig_6_11_Parameter_Energy')]:
        fig,axes=plt.subplots(1,2,figsize=(11,4.5),layout='constrained')
        for ax,net in zip(axes,['C20','C200']):
            xx=np.arange(3);width=.25
            for j,(wolves,c) in enumerate([(5,'#009E73'),(10,'#0072B2'),(20,'#D55E00')]):
                rs=[one(data['parameters'],net,wolves=wolves,iterations=i) for i in [5,10,20]]
                ax.bar(xx+(j-1)*width,[r[metric+'_mean'] for r in rs],width,yerr=[r[metric+'_sd'] for r in rs],capsize=3,label=f'{wolves} wolves',color=c)
            ax.set_title(net,loc='left',fontweight='bold');ax.set_xticks(xx,[5,10,20]);ax.set_xlabel('DGWO iterations');ax.set_ylabel(label);ax.set_ylim(bottom=0);style(ax)
        axes[0].legend(frameon=False,fontsize=8);save(fig,a.out,name)
    fig,axes=plt.subplots(1,3,figsize=(11,3.8),layout='constrained');rs=sorted(data['workload'],key=lambda r:r['task_count']);xx=[r['task_count'] for r in rs]
    for ax,metric,label in zip(axes,['decision_ms','energy_j','analytical_delay_s'],['Decision time per task (ms)','Task energy per run (J)','Analytical delay per task (s)']):
        ax.errorbar(xx,[r[metric+'_mean'] for r in rs],yerr=[r[metric+'_sd'] or 0 for r in rs],fmt='o-',capsize=4,color='#0072B2');ax.set_xticks(xx);ax.set_xlabel('Tasks submitted');ax.set_ylabel(label);style(ax)
        ax.set_ylim(bottom=0)
    save(fig,a.out,'Fig_6_13_Workload_Scalability')
    fig,ax=plt.subplots(figsize=(7,4),layout='constrained');values=[]
    for net in NETS:
        rr=[r for r in records if r['network']==net];values.append(100*sum(r['optimal_match'] for r in rr)/len(rr))
    ax.bar(NETS,values,color='#0072B2',width=.55);ax.set_ylim(0,110);ax.set_ylabel('Agreement with exhaustive minimum (%)');style(ax)
    for i,v in enumerate(values):ax.text(i,v+2,f'{v:.2f}%',ha='center')
    save(fig,a.out,'Fig_6_14_Optimality_Agreement')
    (a.out/'figure_notes.txt').write_text('Error bars are sample SD across run-level means. Fixed-seed timing and multi-seed robustness remain separate. Energy excludes idle infrastructure, result returns and optimizer energy. Analytical and observed delay dot plots use focused axes; bars and workload axes start at zero. IoT-only delay and energy concern nine completed tasks rather than twenty. Figures compare complete policies, not isolated optimizers.\n',encoding='utf-8')
if __name__=='__main__':main()
