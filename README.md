# Lightweight Distributed Peer-to-Peer Task Offloading

Python / EdgeSimPy implementation of resource-aware local-first task offloading using DGWO. Cluster servers provide resource information; the source-side policy makes the offloading decision. Source execution is simulated after resource, energy and deadline checks, or the task is rejected with a recorded reason.

## Repository contents

- Root Python modules: simulator, resources, filtering, revalidation, fitness, DGWO and baselines.
- `C20.json`, `C50.json`, `C100.json`, `C200.json`: final scenarios, ten peer devices per cluster.
- `evidence/`: the exact archived run JSON/TXT used in the report; source-path and SHA-256 mappings are in `evidence/manifest.json`.
- `Plotting_Package/data/`: tables regenerated from archived evidence, including individual runs and optimality records.
- `Plotting_Package/plot_results.py`: figure generation from these tables.
- `figures/`: regenerated figures in PNG, PDF and SVG. Figure filenames are identifiers; numbering in the report may differ. The optional weight plot is omitted; weight results remain in the tables.
- `Arrival_Trial/run_arrival_trial.py`: isolated supplementary arrival-model experiment.
- `test_final_topology.py`: counted structure checks and optional EdgeSimPy runtime probes for all four networks.
- `test_c200_runtime.py`: the standalone eight-check C200 runtime probe.
- `CODE_AUDIT.json`, `CLEANUP_NOTES.md`: preparation changes and file checksums.

## Environment and installation (Windows PowerShell)

The archived experiments used Python 3.11.9. Install Git only if needed for your own repository workflow; the pinned EdgeSimPy dependency uses a GitHub archive URL, not a pip package-name lookup.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip check
python -c "import edge_sim_py, mesa, networkx, msgpack, numpy, pandas, matplotlib; print('Environment ready')"
```

`requirements.txt` preserves the recorded package versions and pins EdgeSimPy to commit `76eb5ead74596bb4240759fa4336f1d6f190c70a`. Do not include your `.venv` in GitHub. Internet access is needed for installation. On Linux/macOS, use `python3.11 -m venv .venv` and `source .venv/bin/activate` instead of the first two commands.

## One simulation

```powershell
python -u .\main_final_local_first.py --dataset C200.json --method dgwo --wolves 10 --iterations 5 --weights "0.4,0.4,0.2" --optimizer-seed 42 --arrival-seed 42 --summary-json single_C200.json *> single_C200.txt
```

Candidate handling: one feasible candidate is considered directly; two or three are ranked by fitness; four or more invoke DGWO. Final verification precedes commitment. DGWO checks its best candidate first, then remaining candidates in list order. Failed local search expands outside the source cluster. The default arrival generator uses `p = 1-exp(-0.5)` once per one-second tick and releases at most one task per tick; this is not a full Poisson count generator. The separate arrival experiment samples counts and allows multiple same-tick arrivals.

## Validation

```powershell
python .\test_final_topology.py all --runtime *> topology_validation.txt
python .\test_c200_runtime.py
```

The recorded validation counts are C20: 395, C50: 1,119, C100: 2,719 and C200: 3,203, with zero failures, totaling 7,436 checks. C200 counts each executed assertion in its dedicated validator as one check. Compound assertions count once. Counts characterize these scripts, not standardized or exhaustive coverage. Runtime probes check loaded counts and local/inter-cluster path, propagation delay and transmission time; they are not full task-execution tests.

## Reproduce the final report experiments

Use the report entry point, which selects the direct network-wide baselines for the principal comparison:

```powershell
python -u .\run_report_experiments.py --experiment all *> evaluation_log.txt
```

This executes 684 runs / 15,130 task submissions. It can take time. Original evidence stays unchanged. New runs are saved under timestamped `generated_results/` directories; arrival trials save under `Arrival_Trial/outputs/`. Host timing varies by machine and run, so bit-identical timing is not expected.

Individual experiments:

```powershell
python -u .\run_report_experiments.py --experiment comparison
python -u .\run_report_experiments.py --experiment robustness
python -u .\run_report_experiments.py --experiment filtering
python -u .\run_report_experiments.py --experiment parameters
python -u .\run_report_experiments.py --experiment weights
python -u .\run_report_experiments.py --experiment workload
python -u .\run_report_experiments.py --experiment optimality
python -u .\run_report_experiments.py --experiment arrival
```

For a reduced check: `python .\run_report_experiments.py --experiment comparison --smoke`. Smoke outputs are not statistical evidence.

| Experiment | Scope | Runs |
|---|---|---:|
| Comparison | Four networks, six methods, ten repeats, both seeds 42 | 240 |
| Robustness | Four networks, five edge policies, seeds 11,22,...,110 | 200 |
| Filtering | Four networks, two local-first variants, five seeds | 40 |
| Parameters | C20/C200, 5/10/20 wolves and iterations, three seeds | 54 |
| Weights | C20/C200, five weight settings, five seeds | 50 |
| Workload | C100, 20/50/100/200 tasks, five seeds | 20 |
| Optimality | Four networks, five seeds | 20 |
| Arrival | C20/C200, five edge policies, three models, seeds 11/42 | 60 |

The five-seed set is 11,22,33,44,55; the three-seed set is 11,22,33. The two seeds within a pair have the same numeric value. Fixed-seed repetitions characterize host timing variation; different-seed runs change arrivals and optimizer initialization together.

The lower-level `run_project_experiments.py --experiment baseline` retains the earlier filtered-policy comparison for compatibility; it is **not** the principal report baseline protocol. Use `run_report_experiments.py` or `run_direct_comparison.py` for that comparison.

## Regenerate report data and figures

```powershell
python .\export_report_data.py
python .\Plotting_Package\plot_results.py --out figures
```

This verifies archived JSON hashes and rebuilds data tables before plotting. It does not rerun simulation. The exporter intentionally reads the selected archived observations rather than automatically choosing the most recent folder. New experiment outputs must be reviewed before being adopted as report evidence.

Error bars are sample SD across run-level measurements, not confidence intervals. Energy is modeled task energy, excluding idle infrastructure, result return and decision energy. IoT-only completes nine of twenty fixed-seed tasks; its delay and energy totals do not describe the same completed workload as the edge policies. The workload study increases run length at fixed arrival settings, not instantaneous arrival intensity. The optimality reference is conditional on the candidate set and state at a decision, not a globally optimal multi-task schedule.

