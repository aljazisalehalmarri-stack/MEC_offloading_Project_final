"""Independent selection policies. Lower task fitness is better."""
import math
import random
from time import perf_counter
from fitness import calculate_fitness, calculate_energy_consumption
from dgwo import run_dgwo
from energy_model import total_offload_energy_j
from network_resources import get_network_resources


def choose(task, candidates, method, seed, wolves, iterations):
    start = perf_counter()
    if not candidates:
        return [], {'decision_ms': 0.0, 'fitness_evaluations': 0, 'unique_fitness_evaluations': 0}
    if len(candidates) == 1:
        return list(candidates), {'decision_ms': 0.0, 'fitness_evaluations': 0, 'unique_fitness_evaluations': 0}
    if method == 'random':
        rng = random.Random(seed)
        selected = rng.choice(candidates)
        return [selected] + [c for c in candidates if c.id != selected.id], {
            'decision_ms': (perf_counter()-start)*1000, 'fitness_evaluations': 0, 'unique_fitness_evaluations': 0}
    if method in ('greedy', 'pso'):
        energy_values = [
            total_offload_energy_j(
                calculate_energy_consumption(task, candidate),
                get_network_resources(task, candidate),
            )
            for candidate in candidates
        ]
        valid_energies = [
            energy for energy in energy_values if math.isfinite(energy) and energy >= 0
        ]
        reference = (min(valid_energies), max(valid_energies)) if valid_energies else (0.0, 0.0)
    if method == 'greedy':
        ranked = sorted(candidates, key=lambda c: (calculate_fitness(task,c,energy_reference=reference)['fitness'],c.id))
        return ranked, {'decision_ms': (perf_counter()-start)*1000,
                        'fitness_evaluations': len(candidates), 'unique_fitness_evaluations': len(candidates)}
    if method in ('dgwo','gwo_no_filter'):
        result = run_dgwo(task, candidates, number_of_wolves=wolves, iterations=iterations,
                          seed=seed, verbose=False,
                          penalize_infeasible=(method == "gwo_no_filter"))
        first = result['best_candidate']
        # Any execution-time fallback is ordered by feasibility, not by exact diagnostic benchmark.
        ordered = [first] + [c for c in candidates if c.id != first.id]
        return ordered, {'decision_ms': (perf_counter()-start)*1000,
                         'fitness_evaluations': result['total_wolf_evaluations'],
                         'unique_fitness_evaluations': result['fitness_calculations'],
                         'fitness_gap': result.get('optimality_gap')}
    if method == 'pso':
        # Discrete PSO on candidate indices; same number of particles/iterations as DGWO.
        rng = random.Random(seed)
        n=len(candidates)
        cache={}
        calls=0
        def evaluate(x):
            nonlocal calls
            calls += 1
            i=max(0,min(n-1,int(x+0.5)))
            if i not in cache:
                cache[i]=calculate_fitness(task,candidates[i],energy_reference=reference)['fitness']
            return cache[i],i
        positions=[rng.uniform(0,n-1) for _ in range(wolves)]
        velocities=[0.0]*wolves
        pbest=list(positions)
        pvalues=[evaluate(x)[0] for x in positions]
        gidx=min(range(wolves),key=lambda i:pvalues[i]); gbest=pbest[gidx]; gval=pvalues[gidx]
        for _ in range(iterations):
            for j in range(wolves):
                velocities[j]=0.7*velocities[j]+1.4*rng.random()*(pbest[j]-positions[j])+1.4*rng.random()*(gbest-positions[j])
                positions[j]=max(0,min(n-1,positions[j]+velocities[j]))
                val,_=evaluate(positions[j])
                if val < pvalues[j]: pvalues[j]=val;pbest[j]=positions[j]
                if val < gval: gval=val;gbest=positions[j]
        _,best_idx=evaluate(gbest)
        first=candidates[best_idx]
        return [first]+[c for c in candidates if c.id!=first.id], {
            'decision_ms':(perf_counter()-start)*1000,
            'fitness_evaluations':calls,'unique_fitness_evaluations':len(cache)}
    raise ValueError(f'Unknown method {method}')
