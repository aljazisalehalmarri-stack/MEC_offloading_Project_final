"""
dgwo.py

Validation-oriented Grey Wolf Optimizer for the current MEC project.

Current optimization flow:

    Candidate Set P_i
          ↓
    Discrete Candidate Space
          ↓
    Wolf Initialization
          ↓
    Fitness Evaluation
          ↓
    Alpha / Beta / Delta
          ↓
    GWO Position Update
          ↓
    Discrete Mapping
          ↓
    Fitness Re-evaluation
          ↓
    Best Candidate

This version is for validating the optimization stage.

It does NOT:
- perform inter-cluster/global search
- perform task offloading
- update EdgeSimPy resources
- change the candidate set
"""

import random
import math
from time import perf_counter

from fitness import calculate_fitness, calculate_energy_consumption
from energy_model import total_offload_energy_j
from network_resources import get_network_resources


# ==========================================================
# DGWO Parameters
# ==========================================================

DEFAULT_WOLVES = 10
DEFAULT_ITERATIONS = 5
DEFAULT_SEED = 42


# ==========================================================
# Discrete Mapping
# ==========================================================

def position_to_index(position, number_of_candidates):
    """
    Map a continuous GWO position to a discrete candidate index.

    The search interval is:

        [0, number_of_candidates - 1]

    The nearest integer index is selected, with positions halfway
    between indices rounded upward. The result is clipped to the
    valid candidate-index range.
    """

    if number_of_candidates <= 0:
        raise ValueError(
            "number_of_candidates must be greater than zero."
        )

    # Nearest-integer mapping for non-negative GWO positions.
    index = int(position + 0.5)

    index = max(
        0,
        min(
            index,
            number_of_candidates - 1
        )
    )

    return index


def position_to_candidate(position, candidate_nodes):
    """
    Convert a continuous wolf position into an EdgeServer
    object from the candidate set.
    """

    if not candidate_nodes:
        raise ValueError(
            "candidate_nodes cannot be empty."
        )

    index = position_to_index(
        position,
        len(candidate_nodes)
    )

    return candidate_nodes[index]


# ==========================================================
# Wolf Initialization
# ==========================================================

def initialize_wolves(number_of_wolves, number_of_candidates):
    """
    Initialize wolves across all candidate regions.

    The first wolves cover every candidate region when the
    number of wolves is at least the number of candidates.
    One wolf is sampled per equal-width stratum over the entire
    candidate range. This preserves diversity without using fitness
    values to bias the starting population.
    """

    if number_of_wolves < 3:
        raise ValueError(
            "At least 3 wolves are required for Alpha, Beta, Delta."
        )

    if number_of_candidates <= 0:
        raise ValueError(
            "At least one candidate is required."
        )

    # Stratified initialization across the entire candidate-index interval.
    # Unlike the previous initialization, this does not favor the first
    # `number_of_wolves` candidates when there are more candidates than wolves.
    wolves = []
    span = float(number_of_candidates)
    for wolf_index in range(number_of_wolves):
        lower = span * wolf_index / number_of_wolves
        upper = span * (wolf_index + 1) / number_of_wolves
        # Use a random point within each stratum, clipped to the legal range.
        wolves.append(min(float(number_of_candidates - 1),
                          random.uniform(lower, upper)))
    random.shuffle(wolves)

    return wolves


# ==========================================================
# Evaluate One Wolf
# ==========================================================

def evaluate_wolf(task, position, candidate_nodes, energy_reference=None):
    """
    Map a wolf to a candidate and calculate its fitness.
    """

    candidate = position_to_candidate(
        position,
        candidate_nodes
    )

    fitness_result = calculate_fitness(
        task,
        candidate,
        energy_reference=energy_reference,
    )

    return candidate, fitness_result


# ==========================================================
# Candidate Distribution
# ==========================================================

def get_candidate_distribution(wolves, candidate_nodes):
    """
    Count how many wolves currently map to each candidate.
    """

    distribution = {
        candidate.id: 0
        for candidate in candidate_nodes
    }

    for position in wolves:
        candidate = position_to_candidate(
            position,
            candidate_nodes
        )

        distribution[candidate.id] += 1

    return distribution


# ==========================================================
# GWO Position Update
# ==========================================================

def update_position(
    position,
    alpha_position,
    beta_position,
    delta_position,
    a
):
    """
    Apply the standard GWO position update:

        X(t+1) = (X1 + X2 + X3) / 3
    """

    # Alpha
    r1 = random.random()
    r2 = random.random()

    A1 = 2.0 * a * r1 - a
    C1 = 2.0 * r2

    D_alpha = abs(
        C1 * alpha_position
        - position
    )

    X1 = (
        alpha_position
        - A1 * D_alpha
    )

    # Beta
    r1 = random.random()
    r2 = random.random()

    A2 = 2.0 * a * r1 - a
    C2 = 2.0 * r2

    D_beta = abs(
        C2 * beta_position
        - position
    )

    X2 = (
        beta_position
        - A2 * D_beta
    )

    # Delta
    r1 = random.random()
    r2 = random.random()

    A3 = 2.0 * a * r1 - a
    C3 = 2.0 * r2

    D_delta = abs(
        C3 * delta_position
        - position
    )

    X3 = (
        delta_position
        - A3 * D_delta
    )

    return (
        X1
        + X2
        + X3
    ) / 3.0


# ==========================================================
# DGWO
# ==========================================================

def run_dgwo(
    task,
    candidate_nodes,
    number_of_wolves=DEFAULT_WOLVES,
    iterations=DEFAULT_ITERATIONS,
    seed=DEFAULT_SEED,
    verbose=True,
    log_iterations="all",
    penalize_infeasible=False,
):
    """
    Run GWO over the feasible candidate EdgeServer objects.

    Lower fitness is better.

    Returns
    -------
    dict
        best_candidate
        best_candidate_id
        best_fitness
        best_position
        history
    """

    if not candidate_nodes:
        raise ValueError(
            "DGWO cannot run with an empty candidate set."
        )

    if number_of_wolves < 3:
        raise ValueError(
            "DGWO requires at least 3 wolves."
        )

    if iterations <= 0:
        raise ValueError(
            "iterations must be greater than zero."
        )

    _prior_random_state = random.getstate()
    random.seed(seed)

    number_of_candidates = len(
        candidate_nodes
    )

    # Fixed task-wide Min-Max bounds: shared by every wolf evaluation.
    energy_values = [total_offload_energy_j(calculate_energy_consumption(task, node), get_network_resources(task, node)) for node in candidate_nodes]
    if penalize_infeasible:
        energy_values = [v for v in energy_values if math.isfinite(v) and v >= 0]
        energy_reference = (min(energy_values), max(energy_values)) if energy_values else (0.0, 0.0)
    else:
        energy_reference = (min(energy_values), max(energy_values))
        if energy_reference[0] < 0 or not all(math.isfinite(x) for x in energy_reference):
            raise ValueError("Invalid energy normalization bounds")
    if verbose:
        print(f"Energy normalization: candidate E_total range = {energy_reference[0]:.9f} .. {energy_reference[1]:.9f} J")

    # Search interval [0, number_of_candidates - 1] for nearest-index mapping.
    lower_bound = 0.0
    upper_bound = float(
        number_of_candidates - 1
    )

    wolves = initialize_wolves(
        number_of_wolves,
        number_of_candidates
    )

    global_best_candidate = None
    global_best_fitness = float("inf")
    global_best_position = None

    history = []

    # Cache is scoped to this single offloading decision. Resource states
    # must remain unchanged during this call for reuse to be valid.
    fitness_cache = {}
    fitness_calculations = 0
    cache_hits = 0
    total_wolf_evaluations = 0
    optimization_start = perf_counter()

    # ======================================================
    # Main optimization loop
    # ======================================================

    for iteration in range(
        1,
        iterations + 1
    ):

        evaluations = []

        # --------------------------------------------------
        # Evaluate population
        # --------------------------------------------------

        for position in wolves:

            candidate_index = position_to_index(
                position,
                number_of_candidates
            )
            candidate = candidate_nodes[candidate_index]
            total_wolf_evaluations += 1

            if candidate_index in fitness_cache:
                fitness_result = fitness_cache[candidate_index]
                cache_hits += 1
            else:
                fitness_result = calculate_fitness(task, candidate, energy_reference=energy_reference, penalize_infeasible=penalize_infeasible)
                fitness_cache[candidate_index] = fitness_result
                fitness_calculations += 1

            fitness_value = fitness_result["fitness"]

            evaluations.append(
                (
                    position,
                    candidate,
                    fitness_value
                )
            )

            if fitness_value < global_best_fitness:

                global_best_fitness = fitness_value
                global_best_candidate = candidate
                global_best_position = position

        # --------------------------------------------------
        # Rank current population
        # --------------------------------------------------

        evaluations.sort(
            key=lambda item: item[2]
        )

        alpha_position, alpha_candidate, alpha_fitness = (
            evaluations[0]
        )

        beta_position, beta_candidate, beta_fitness = (
            evaluations[1]
        )

        delta_position, delta_candidate, delta_fitness = (
            evaluations[2]
        )

        # --------------------------------------------------
        # Candidate distribution
        # --------------------------------------------------

        distribution = get_candidate_distribution(
            wolves,
            candidate_nodes
        )

        # --------------------------------------------------
        # Best-so-far information
        # --------------------------------------------------

        if verbose and (log_iterations == "all" or iteration in (1, iterations)):

            print()
            print(
                f"Iteration {iteration}"
            )

            print(
                f"  Alpha -> Node "
                f"{alpha_candidate.id} | "
                f"Position = "
                f"{alpha_position:.4f} | "
                f"Fitness = "
                f"{alpha_fitness:.6f}"
            )

            print(
                f"  Beta  -> Node "
                f"{beta_candidate.id} | "
                f"Position = "
                f"{beta_position:.4f} | "
                f"Fitness = "
                f"{beta_fitness:.6f}"
            )

            print(
                f"  Delta -> Node "
                f"{delta_candidate.id} | "
                f"Position = "
                f"{delta_position:.4f} | "
                f"Fitness = "
                f"{delta_fitness:.6f}"
            )

            print(
                f"  Candidate Distribution = "
                f"{distribution}"
            )

            print(
                f"  Best-so-far -> Node "
                f"{global_best_candidate.id} | "
                f"Fitness = "
                f"{global_best_fitness:.6f}"
            )

        history.append({
            "iteration":
                iteration,

            "alpha_node":
                alpha_candidate.id,

            "alpha_position":
                alpha_position,

            "alpha_fitness":
                alpha_fitness,

            "beta_node":
                beta_candidate.id,

            "beta_position":
                beta_position,

            "beta_fitness":
                beta_fitness,

            "delta_node":
                delta_candidate.id,

            "delta_position":
                delta_position,

            "delta_fitness":
                delta_fitness,

            "best_so_far_node":
                global_best_candidate.id,

            "best_so_far_fitness":
                global_best_fitness,

            "candidate_distribution":
                distribution.copy(),
        })

        # --------------------------------------------------
        # GWO control parameter a
        # --------------------------------------------------

        if iterations == 1:

            a = 0.0

        else:

            a = (
                2.0
                -
                (
                    2.0
                    * (iteration - 1)
                    / (iterations - 1)
                )
            )

        # --------------------------------------------------
        # Update wolves
        # --------------------------------------------------

        updated_wolves = []

        for position in wolves:

            new_position = update_position(
                position,
                alpha_position,
                beta_position,
                delta_position,
                a
            )

            # Keep position in the valid index interval [0, n - 1].
            new_position = max(
                lower_bound,
                min(
                    new_position,
                    upper_bound
                )
            )

            updated_wolves.append(
                new_position
            )

        wolves = updated_wolves

    # ======================================================
    # Optimization performance (does not include later offloading)
    # ======================================================

    optimization_time_ms = (perf_counter() - optimization_start) * 1000.0

    if verbose:
        print()
        print("DGWO Performance:")
        print(f"  Total Wolf Evaluations     : {total_wolf_evaluations}")
        print(f"  Actual Fitness Calculations: {fitness_calculations}")
        print(f"  Cache Hits                 : {cache_hits}")
        print(f"  Optimization Time          : {optimization_time_ms:.3f} ms")

    # ======================================================
    # Return best solution
    # ======================================================

    random.setstate(_prior_random_state)
    return {
        "best_candidate":
            global_best_candidate,

        "best_candidate_id":
            global_best_candidate.id,

        "best_fitness":
            global_best_fitness,

        "best_position":
            global_best_position,

        "history":
            history,

        "total_wolf_evaluations": total_wolf_evaluations,
        "fitness_calculations": fitness_calculations,
        "cache_hits": cache_hits,
        "optimization_time_ms": optimization_time_ms,
    }
