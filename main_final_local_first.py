from edge_sim_py import (
    Simulator,
    EdgeServer,
    User,
    BaseStation,
    NetworkSwitch,
    NetworkLink,
)

from resource_table import build_resource_table
from filtering import filter_candidates
from candidate_checking import check_candidates
from dgwo import run_dgwo
from baselines import choose
from time import perf_counter
from offloading import (
    offload_task,
    execute_task_locally,
    calculate_cpu_units_required,
    get_processing_rate_per_unit,
    calculate_reserved_energy,
)
from network_resources import get_network_resources
from energy_model import transmission_energy_j, source_energy_available
from task import Task

import random
import math
import argparse
from timing_model import estimated_uncontended_delay


# ==========================================================
# Step 1: Static User Mobility
# ==========================================================

def static_mobility(user):
    """
    Keep the IoT user at its current location and
    current Base Station throughout the simulation.
    """
    return


def get_source_cluster_id(user):
    """Source area is determined by the shared access BS, not by an Edge Node."""
    source_base_station = getattr(user, "base_station", None)
    if source_base_station is None:
        raise ValueError(f"User {user.id} has no Base Station.")
    # Cluster access base stations follow the edge nodes in the dataset.
    edge_nodes = [node for node in EdgeServer.all()
                  if getattr(node, "role", None) == "edge_node"]
    node_count = len(edge_nodes)
    cluster_by_access_bs = {node_count + i: i for i in range(1, node_count // 10 + 1)}
    cluster_id = cluster_by_access_bs.get(source_base_station.id)
    if cluster_id is None:
        raise ValueError(f"Unknown shared access Base Station {source_base_station.id}.")
    user.cluster_id = cluster_id
    return cluster_id


# ==========================================================
# Step 2: Reproducible Diverse Task Inputs
# ==========================================================
# Input workload profiles are reproducible test data, NOT hardcoded execution choices.
# Every task uses the same automatic decision rules regardless of task ID.
# Fields: task_id, workload (cycles), memory (MB), data (MB), deadline (s), source.
FIXED_TASK_PROFILES = [
    # Light/medium/heavy CPU, small/large input, short/long deadlines.
    (1, 250_000_000, 1024, 1, 6, 1),    # lightweight
    (2, 1_400_000_000, 4096, 4, 9, 2),  # CPU-intensive
    (3, 100_000_000, 512, 3, 5, 3),     # light compute, high transfer relative to local work
    (4, 650_000_000, 3072, 3, 9, 4),    # medium
    (5, 900_000_000, 4096, 6, 12, 5),   # data-intensive
    (6, 450_000_000, 14500, 2, 30, 6),  # high-memory workload
    (7, 1_600_000_000, 6144, 5, 12, 7), # heavy compute
    (8, 300_000_000, 1024, 1, 5, 8),    # short task
    (9, 750_000_000, 3072, 3, 9, 9),    # mixed
    (10, 650_000_000, 14500, 2, 16, 5),# high-memory workload from cluster 1
    # Additional independent task inputs from previously unused source users.
    # The scheduler NEVER branches on these IDs: ALL EDGE PEERS -> SOURCE IoT.
    (11, 200_000_000, 1024, 2, 1, 10),   # tight deadline: 1-tick IoT can qualify if all Edge paths fail
    (12, 700_000_000, 2048, 2, 8, 11),  # cluster 3
    (13, 1_200_000_000, 4096, 3, 10, 12),
    (14, 350_000_000, 1024, 1, 6, 13),
    (15, 900_000_000, 3072, 4, 11, 14),
    (16, 500_000_000, 2048, 2, 7, 15),
    (17, 1_100_000_000, 4096, 4, 12, 16),  # cluster 4
    (18, 450_000_000, 1024, 1, 6, 17),
    (19, 1_500_000_000, 6144, 5, 14, 18),
    (20, 650_000_000, 2048, 2, 9, 19),


]

tasks = [
    Task(task_id=task_id, workload=workload, memory_required=memory,
         data_size=data, max_delay=deadline, source_user_id=source)
    for task_id, workload, memory, data, deadline, source in FIXED_TASK_PROFILES
]

print("\nREPRODUCIBLE DIVERSE TASK INPUTS")
print("-" * 110)
print(f"Configured tasks: {len(tasks)}")
print("Task properties : heterogeneous input profiles; execution mode decided automatically")

# ==========================================================
# Step 3: Discrete Arrival Approximation Parameters
# ==========================================================

ARRIVAL_SEED = 42
arrival_rng = random.Random(ARRIVAL_SEED)

arrival_rate = 0.5

arrival_probability = (
    1 - math.exp(-arrival_rate)
)


# ==========================================================
# Step 4: Simulation Parameters
# ==========================================================

simulation_parameters = {

    "tasks":
        tasks,

    "next_task_index":
        0,

    "arrival_trace": [],

    # Store feasible candidate sets P_i
    "candidate_sets":
        {},

    "validated_candidate_sets":
        {},

    "offloaded_tasks":
        {},

    # Tasks currently executing on Edge Nodes; their CPU/memory is released
    # when their modeled completion step is reached.
    "active_source_tasks": [],
    "active_edge_tasks":
        [],
}


# ==========================================================
# Step 5: Task Arrival Function
# ==========================================================

def verify_candidate_for_execution(task, candidate):
    """
    Perform a final, execution-time feasibility check immediately before
    committing the task to an Edge Node.

    The check uses the current resource/network state and verifies:
        - candidate validity and current availability
        - current CPU and memory availability
        - current network path/transmission feasibility
        - current total execution delay against the task deadline
        - current energy requirement against remaining energy

    Returns
    -------
    tuple
        (True, details) when the candidate is feasible, otherwise
        (False, reason).
    """
    validated = check_candidates(
        task=task,
        candidate_nodes=[candidate],
        verbose=False,
    )

    if not validated:
        return False, "Candidate failed final resource/network revalidation."

    try:
        network_info = get_network_resources(
            task=task,
            server=candidate,
        )

        cpu_units = calculate_cpu_units_required(
            task,
            candidate,
        )

        available_cpu = float(candidate.cpu - candidate.cpu_demand)
        if cpu_units > available_cpu:
            return (
                False,
                f"Required CPU units {cpu_units} exceed available CPU units {available_cpu:g}.",
            )

        rate_per_unit = get_processing_rate_per_unit(
            candidate,
        )

        allocated_processing_capability = (
            cpu_units * rate_per_unit
        )

        if allocated_processing_capability <= 0:
            return False, "Invalid allocated processing capability."

        computation_delay = (
            float(task.workload)
            / allocated_processing_capability
        )

        communication_delay = float(
            network_info.get("communication_delay", float("inf"))
        )
        transmission_delay = float(
            network_info.get("transmission_delay", float("inf"))
        )

        total_delay = (
            computation_delay
            + communication_delay
            + transmission_delay
        )

        estimated_tick_delay = estimated_uncontended_delay(
            transmission_delay, communication_delay, computation_delay
        )
        if total_delay > float(task.max_delay) or estimated_tick_delay > float(task.max_delay):
            return (
                False,
                f"Analytical delay {total_delay:.4f}s or tick estimate "
                f"{estimated_tick_delay:.4f}s exceeds deadline "
                f"{task.max_delay:.4f}s.",
            )

        reserved_energy = calculate_reserved_energy(
            task,
            allocated_processing_capability,
        )

        remaining_energy = float(
            getattr(candidate, "remaining_energy_j", 0.0)
        )

        if not source_energy_available(task, network_info):
            return False, "Source IoT has insufficient transmission energy."
        if reserved_energy > remaining_energy:
            return (
                False,
                f"Energy requirement {reserved_energy:.6f}J exceeds "
                f"remaining energy {remaining_energy:.6f}J.",
            )

        return True, {
            "cpu_units": cpu_units,
            "allocated_processing_capability": allocated_processing_capability,
            "computation_delay": computation_delay,
            "communication_delay": communication_delay,
            "transmission_delay": transmission_delay,
            "total_delay": total_delay,
            "reserved_energy": reserved_energy,
            "remaining_energy": remaining_energy,
        }

    except Exception as error:
        return False, str(error)


# Explicit fallback assumptions already used by this main file.
DEFAULT_IOT_PROCESSING_RATE = 500_000_000  # cycles/s
DEFAULT_IOT_ENERGY_J = 1000.0
DEFAULT_IOT_MEMORY_MB = 2048.0  # explicit model assumption; IoT users have no native RAM field


def format_execution_summary(task_id, result):
    """Distinguish intra-cluster offloading, remote offloading and source execution."""
    source_id = result.get("source_user_id")
    if source_id is None:
        source_id = next((t.source_user_id for t in tasks if t.task_id == task_id), "UNKNOWN")
    selected_node = result.get("selected_server_id")
    if result.get("status") == "REJECTED":
        kind = "REJECTED"
        device = f"Reason={result.get('rejection_reason', 'unspecified')}"
    elif selected_node is None:
        kind = "SOURCE_IOT"
        device = f"Source IoT={result.get('execution_device_id', source_id)}"
    else:
        scope = str(result.get("offloading_scope", "UNKNOWN")).upper()
        kind = {"LOCAL": "LOCAL_OFFLOADING", "GLOBAL": "GLOBAL_OFFLOADING",
                "REMOTE": "GLOBAL_OFFLOADING"}.get(scope, "UNKNOWN_OFFLOADING_SCOPE")
        device = f"Edge Node={selected_node}"
    return (
        f"Task {task_id} | Type={kind} | Source User={source_id} | {device} | "
        f"Status={result['status']} | Total Delay={result['total_delay']:.4f} s | "
        f"Computation Energy={result['reserved_energy_j']:.6f} J"
    )


def execute_source_iot_fallback(task, source_user):
    """Execute the task locally when no Alpha/Beta/Delta node is feasible."""
    processing_capability = float(
        getattr(
            source_user,
            "local_processing_capability",
            DEFAULT_IOT_PROCESSING_RATE,
        )
    )

    remaining_energy = float(
        getattr(
            source_user,
            "local_remaining_energy_j",
            DEFAULT_IOT_ENERGY_J,
        )
    )

    source_user.local_processing_capability = processing_capability
    source_user.local_remaining_energy_j = remaining_energy

    result = execute_task_locally(
        task=task,
        user=source_user,
        processing_capability=processing_capability,
        remaining_energy_j=remaining_energy,
    )

    source_user.local_remaining_energy_j = result[
        "remaining_energy_j"
    ]

    result["source_user_id"] = source_user.id
    result["execution_device_id"] = source_user.id
    result["selected_server_id"] = None
    result["execution_mode"] = "LOCAL_EXECUTION"
    return result


def evaluate_source_iot(task, source_user, parameters):
    """Check source capacity and currently unreserved resources before scheduling."""
    rate = float(getattr(source_user, "local_processing_capability", DEFAULT_IOT_PROCESSING_RATE))
    energy = float(getattr(source_user, "local_remaining_energy_j", DEFAULT_IOT_ENERGY_J))
    memory = float(getattr(source_user, "local_memory_mb", DEFAULT_IOT_MEMORY_MB))
    memory_demand = float(getattr(source_user, "local_memory_demand_mb", 0.0))
    cpu_demand = float(getattr(source_user, "local_cpu_demand", 0.0))
    occupied = cpu_demand > 0 or any(
        entry["source_user_id"] == source_user.id
        for entry in parameters.get("active_source_tasks", [])
    )
    available_memory = memory - memory_demand
    workload = float(task.workload)
    required_memory = float(task.memory_required)
    deadline = float(task.max_delay)
    valid_rate = math.isfinite(rate) and rate > 0
    valid_task = (math.isfinite(workload) and workload >= 0
                  and math.isfinite(required_memory) and required_memory >= 0
                  and math.isfinite(deadline) and deadline >= 0)
    valid_resources = (math.isfinite(energy) and energy >= 0
                       and math.isfinite(memory) and memory >= 0
                       and math.isfinite(memory_demand) and 0 <= memory_demand <= memory
                       and math.isfinite(cpu_demand) and 0 <= cpu_demand <= 1)
    comp = workload / rate if valid_rate and valid_task else float("inf")
    ticks = max(1, math.ceil(comp)) if math.isfinite(comp) else float("inf")
    required_energy = calculate_reserved_energy(task, rate) if valid_rate and valid_task else float("inf")
    if not valid_rate:
        reason = "SOURCE_INVALID_RATE"
    elif not valid_task:
        reason = "SOURCE_INVALID_TASK"
    elif not valid_resources:
        reason = "SOURCE_INVALID_RESOURCES"
    elif occupied:
        reason = "SOURCE_BUSY"
    elif available_memory < required_memory:
        reason = "SOURCE_MEMORY"
    elif not math.isfinite(required_energy) or energy < required_energy:
        reason = "SOURCE_ENERGY"
    elif ticks > deadline:
        reason = "SOURCE_DEADLINE"
    else:
        reason = "PASS"
    print(f"SOURCE FEASIBILITY | Task {task.task_id} | IoT={source_user.id} | "
          f"CPU={rate:.0f} cycles/s | RAM={available_memory:.0f} MB | "
          f"RAM_required={required_memory} MB | energy={energy:.6f} J | "
          f"energy_required={required_energy:.6f} J | analytical={comp:.4f}s | "
          f"tick={ticks:.1f}s | deadline={deadline}s | {reason}")
    return {"feasible": reason == "PASS", "tick_delay_s": float(ticks), "reason": reason}


def schedule_source_iot(task, source_user, parameters, current_step, evaluation):
    """Recheck and reserve the source's single CPU slot, RAM, and computation energy."""
    if not evaluation["feasible"]:
        raise ValueError(f"Source IoT is not feasible: {evaluation['reason']}")
    # A previously successful check can become stale before resource reservation.
    current_evaluation = evaluate_source_iot(task, source_user, parameters)
    if not current_evaluation["feasible"]:
        raise ValueError(f"Source IoT is no longer feasible: {current_evaluation['reason']}")
    if task.task_id in parameters["offloaded_tasks"]:
        raise ValueError(f"Task {task.task_id} already has an allocation record")
    memory_before = float(getattr(source_user, "local_memory_demand_mb", 0.0))
    cpu_before = float(getattr(source_user, "local_cpu_demand", 0.0))
    energy_before = float(getattr(source_user, "local_remaining_energy_j", DEFAULT_IOT_ENERGY_J))
    reserved_memory = float(task.memory_required)
    completion_step = current_step + int(current_evaluation["tick_delay_s"])
    # Compute and debit energy before committing reusable-resource reservations.
    result = execute_source_iot_fallback(task, source_user)
    source_user.local_memory_demand_mb = memory_before + reserved_memory
    source_user.local_cpu_demand = cpu_before + 1
    result.update({
        "status": "PROCESSING_LOCALLY",
        "arrival_step": current_step,
        "estimated_tick_delay_uncontended_s": current_evaluation["tick_delay_s"],
        "source_processing_rate_cycles_s": source_user.local_processing_capability,
        "source_memory_assumption_mb": float(getattr(source_user, "local_memory_mb", DEFAULT_IOT_MEMORY_MB)),
        "source_reserved_cpu_units": 1,
        "source_reserved_memory_mb": reserved_memory,
        "source_cpu_demand_before": cpu_before,
        "source_cpu_demand_after_reservation": source_user.local_cpu_demand,
        "source_memory_demand_before_mb": memory_before,
        "source_memory_demand_after_reservation_mb": source_user.local_memory_demand_mb,
        "source_energy_before_j": energy_before,
        "source_energy_after_reservation_j": source_user.local_remaining_energy_j,
        "source_resources_released": False,
    })
    parameters["offloaded_tasks"][task.task_id] = result
    parameters["active_source_tasks"].append({
        "task_id": task.task_id, "source_user_id": source_user.id,
        "source_user": source_user,
        "reserved_cpu_units": 1, "reserved_memory_mb": reserved_memory,
        "arrival_step": current_step, "completion_step": completion_step,
        "deadline": float(task.max_delay),
    })
    print(f"SOURCE RESOURCE RESERVED | Task {task.task_id} | IoT={source_user.id} | "
          f"CPU=1 | RAM={reserved_memory:.0f} MB | "
          f"energy_before={energy_before:.6f} J | "
          f"energy_after={source_user.local_remaining_energy_j:.6f} J")
    print(f"SOURCE IOT SCHEDULED | Task {task.task_id} | completion step={completion_step}")


def reject_task(task, parameters, reason):
    """Explicit rejection instead of pretending an infeasible task completed."""
    parameters["offloaded_tasks"][task.task_id] = {
        "task_id": task.task_id, "source_user_id": task.source_user_id,
        "selected_server_id": None, "status": "REJECTED",
        "total_delay": float("inf"), "reserved_energy_j": 0.0,
        "rejection_reason": reason, "deadline_met_observed": False,
    }
    print(f"TASK REJECTED | Task {task.task_id} | {reason}")

def task_arrival(parameters):

    current_step = parameters[
        "current_step"
    ]

    task_list = parameters[
        "tasks"
    ]

    next_task_index = parameters[
        "next_task_index"
    ]

    # ------------------------------------------------------
    # Complete finished Edge tasks and release their CPU/memory.
    # Do this before handling a new arrival, so new decisions see
    # the resources that are actually available at this simulation step.
    # ------------------------------------------------------
    still_active = []
    # Complete source-IoT tasks at their modeled discrete processing step.
    remaining_source_tasks = []
    for entry in parameters.get("active_source_tasks", []):
        if current_step >= entry["completion_step"]:
            result = parameters["offloaded_tasks"][entry["task_id"]]
            source_user = entry["source_user"]
            if not result.get("source_resources_released", False):
                source_user.local_cpu_demand -= entry["reserved_cpu_units"]
                source_user.local_memory_demand_mb -= entry["reserved_memory_mb"]
                result["source_resources_released"] = True
                result["source_resource_release_step"] = current_step
                result["source_cpu_demand_after_release"] = source_user.local_cpu_demand
                result["source_memory_demand_after_release_mb"] = source_user.local_memory_demand_mb
                result["source_energy_at_completion_j"] = source_user.local_remaining_energy_j
                print(f"SOURCE RESOURCE RELEASED | Task {entry['task_id']} | "
                      f"IoT={source_user.id} | CPU={entry['reserved_cpu_units']} | "
                      f"RAM={entry['reserved_memory_mb']:.0f} MB | "
                      f"CPU_demand={source_user.local_cpu_demand} | "
                      f"RAM_demand={source_user.local_memory_demand_mb:.0f} MB")
            result["status"] = "COMPLETED_LOCALLY"
            result["completion_step"] = current_step
            result["observed_total_delay"] = float(current_step - entry["arrival_step"])
            result["deadline_met_observed"] = result["observed_total_delay"] <= entry["deadline"]
            result["analytical_vs_observed_gap_s"] = result["observed_total_delay"] - result["total_delay"]
            print(f"SOURCE TIMING AUDIT | Task {entry['task_id']} | "
                  f"arrival={entry['arrival_step']} | completion={current_step} | "
                  f"observed={result['observed_total_delay']:.3f}s | "
                  f"analytical={result['total_delay']:.3f}s | "
                  f"deadline={entry['deadline']}s | met={result['deadline_met_observed']}")
            print(f"Task {entry['task_id']} | SOURCE IOT COMPLETED at step {current_step}")
        else:
            remaining_source_tasks.append(entry)
    parameters["active_source_tasks"] = remaining_source_tasks
    # Time audit: all observed timestamps are simulator ticks, not sub-second events.
    for active_task in parameters.get("active_edge_tasks", []):
        result = parameters["offloaded_tasks"][active_task["task_id"]]
        flow = active_task["network_flow"]

        if active_task["phase"] == "TRANSFERRING":
            if flow is not None and getattr(flow, "status", None) != "finished":
                still_active.append(active_task)
                continue
            # EdgeSimPy NetworkFlow tracks bandwidth transfer. The analytical
            # communication/propagation delay is added separately here.
            # EdgeSimPy records flow.end when NetworkFlow.step() finishes.
            # The resource-management callback runs before schedule.step(), so
            # the completion is observed on the following callback.
            flow_end_step = getattr(flow, "end", None) if flow is not None else None
            result["network_flow_end_step"] = flow_end_step
            if flow is not None:
                flow_start_step = getattr(flow, "start", None)
                valid_order = (
                    isinstance(flow_start_step, (int, float))
                    and isinstance(flow_end_step, (int, float))
                    and flow_start_step <= flow_end_step
                )
                print(
                    f"FLOW AUDIT END | Task {active_task['task_id']} | "
                    f"start={flow_start_step} | end={flow_end_step} | "
                    f"order={'PASS' if valid_order else 'CHECK'} | "
                    f"status={getattr(flow, 'status', None)}"
                )
            result["flow_end_vs_detection_steps"] = (
                current_step - flow_end_step if isinstance(flow_end_step, (int, float)) else None
            )
            # The scheduler observes flow completion at this callback tick.
            # This is the earliest point where the processing phase can begin.
            # Keep flow.end (network event) separate from handoff (callback).
            result["transfer_handoff_step"] = current_step
            result["flow_end_to_handoff_ticks"] = result["flow_end_vs_detection_steps"]
            active_task["transfer_observed_step"] = current_step
            active_task["execution_start_step"] = current_step
            result["transfer_observed_duration_s"] = float(current_step - active_task["arrival_step"])
            result["transfer_detection_lag_vs_analytical_s"] = (
                result["transfer_observed_duration_s"] - float(result["transmission_delay"])
            )
            active_task["phase"] = "PROCESSING"
            active_task["completion_step"] = current_step + max(
                1,
                math.ceil(active_task["communication_delay"] + active_task["computation_delay"]),
            )
            result["status"] = "PROCESSING"
            result["transfer_observed_step"] = current_step
            result["execution_start_step"] = current_step
            result["processing_completion_step"] = active_task["completion_step"]
            print(
                f"Task {active_task['task_id']} | Network transfer finished "
                f"at observed step {current_step}; scheduled modeled "
                f"processing completion at step {active_task['completion_step']}"
            )

        if active_task["phase"] == "PROCESSING" and current_step >= active_task["completion_step"]:
            server = active_task["server"]
            server.cpu_demand = max(
                0, server.cpu_demand - active_task["cpu_units_allocated"]
            )
            server.memory_demand = max(
                0, server.memory_demand - active_task["memory_allocated_mb"]
            )
            # Energy is intentionally NOT restored: it was consumed by execution.
            server.available = (
                server.cpu_demand < server.cpu
                and server.memory_demand < server.memory
            )
            result = parameters["offloaded_tasks"].get(active_task["task_id"], {})
            result["status"] = "COMPLETED"
            result["completion_step"] = current_step
            result["observed_total_delay"] = (current_step - active_task["arrival_step"]) * 1.0  # Configured simulation tick: 1 second
            result["deadline_met_observed"] = result["observed_total_delay"] <= active_task["deadline"]
            result["analytical_vs_observed_gap_s"] = (
                result["observed_total_delay"] - float(result["total_delay"])
            )
            result["observed_processing_window_s"] = float(
                current_step - active_task["execution_start_step"]
            )
            result["observed_transfer_handoff_window_s"] = float(
                active_task["execution_start_step"] - active_task["arrival_step"]
            )
            result["timing_audit_sum_s"] = (
                result["observed_transfer_handoff_window_s"]
                + result["observed_processing_window_s"]
            )
            assert abs(result["timing_audit_sum_s"] - result["observed_total_delay"]) < 1e-9, "Delay breakdown mismatch"
            print(f"TIMING AUDIT | Task {active_task['task_id']} | "
                  f"transfer_handoff={result['observed_transfer_handoff_window_s']:.3f}s "
                  f"+ processing={result['observed_processing_window_s']:.3f}s "
                  f"= observed={result['observed_total_delay']:.3f}s "
                  f"| analytical={result['total_delay']:.3f}s "
                  f"| flow_end={result.get('network_flow_end_step')} "
                  f"| handoff={result.get('transfer_handoff_step')}")
            result["resources_released"] = True
            result["parallel_execution_model"] = "DEDICATED_CPU_UNITS"
            print(f"Task {active_task['task_id']} | COMPLETED on Edge Node {server.id} "
                  f"at step {current_step}; released "
                  f"{active_task['cpu_units_allocated']} CPU units and "
                  f"{active_task['memory_allocated_mb']} MB")
            # Record the event now; display it separately after simulation.
            parameters.setdefault("edge_completion_events", []).append({
                "task_id": active_task["task_id"],
                "server_id": server.id,
                "completion_step": current_step,
            })
        else:
            still_active.append(active_task)
    parameters["active_edge_tasks"] = still_active

    # ------------------------------------------------------
    # All tasks have arrived: continue ticking until active tasks finish.
    # ------------------------------------------------------
    if next_task_index >= len(task_list):
        return

    # ------------------------------------------------------
    # Bernoulli arrival check: at most one task per 1-second tick.
    # This approximates Poisson arrivals; it is not an exact Poisson process.
    # ------------------------------------------------------

    if arrival_rng.random() >= arrival_probability:

        return

    # ------------------------------------------------------
    # Get next task
    # ------------------------------------------------------

    task = task_list[
        next_task_index
    ]

    # ======================================================
    # Task Arrival
    # ======================================================

    print()
    print("=" * 110)
    print(
        f"TASK {task.task_id} ARRIVAL"
    )
    print("=" * 110)

    parameters["arrival_trace"].append({"task_id": task.task_id, "arrival_step": current_step})

    print(
        f"Arrival Time : {current_step} s"
    )

    print(
        f"Source User  : {task.source_user_id}"
    )

    # ======================================================
    # Resolve source cluster
    # ======================================================

    source_user = User.find_by_id(task.source_user_id)
    if source_user is None:
        raise ValueError(f"Source User {task.source_user_id} was not found.")

    source_cluster_id = get_source_cluster_id(source_user)

    print(f"Source Cluster       : {source_cluster_id}")

    # ======================================================
    # LOCAL-FIRST, GLOBAL-ON-FAILURE POLICY (DGWO)
    # Search local cluster first. Search other clusters ONLY when
    # no local candidate passes filtering/checking/final verification.
    # Other baselines keep their original network-wide behavior.
    # ======================================================
    if RUN_ARGS.method == "local_iot":
        source_check = evaluate_source_iot(task, source_user, parameters)
        if source_check["feasible"]:
            schedule_source_iot(task, source_user, parameters, current_step, source_check)
        else:
            reject_task(task, parameters, "Local IoT execution infeasible: " + source_check["reason"])
        parameters["next_task_index"] += 1
        return

    _edge_nodes_before = sum(getattr(s, "role", None) == "edge_node" for s in EdgeServer.all())
    _timing_entry = {
        "task_id": task.task_id, "method": RUN_ARGS.method,
        "edge_nodes_before": _edge_nodes_before,
        "candidates_after_preselection": 0,
        "candidates_after_checking": 0,
        "preselection_ms": 0.0, "preselection_revalidation_ms": 0.0,
        "selection_ms": 0.0, "final_verification_ms": 0.0,
        "search_stages": [], "global_expansion_used": False,
        "total_decision_ms": 0.0,
    }
    parameters.setdefault("full_decision_metrics", []).append(_timing_entry)
    selected_server = None
    selected_verification = None
    selected_candidates = []
    network_candidates = []

    if RUN_ARGS.method == "dgwo":
        stages = [("LOCAL", source_cluster_id), ("GLOBAL", None)]
    else:
        stages = [("NETWORK_WIDE", None)]

    for stage_name, cluster_scope in stages:
        pre_start = perf_counter()
        if RUN_ARGS.method == "gwo_no_filter":
            network_candidates = [s for s in EdgeServer.all() if getattr(s, "role", None) == "edge_node"]
        else:
            network_candidates = filter_candidates(task, cluster_id=cluster_scope, verbose=False)
            if stage_name == "GLOBAL":
                # The local cluster has already been checked. Avoid checking it twice.
                network_candidates = [s for s in network_candidates
                                      if getattr(s, "cluster_id", None) != source_cluster_id]
        selected_candidates = (network_candidates if RUN_ARGS.method == "gwo_no_filter"
                               else check_candidates(task=task, candidate_nodes=network_candidates, verbose=False))
        _timing_entry["preselection_ms"] += (perf_counter() - pre_start) * 1000.0
        _timing_entry["candidates_after_preselection"] += len(network_candidates)
        _timing_entry["candidates_after_checking"] += len(selected_candidates)
        _timing_entry["search_stages"].append(stage_name)
        if stage_name == "GLOBAL":
            _timing_entry["global_expansion_used"] = True
        parameters["candidate_sets"][task.task_id] = selected_candidates
        parameters["validated_candidate_sets"][task.task_id] = selected_candidates
        print(f"SEARCH STAGE | task={task.task_id} | stage={stage_name} "
              f"| candidates={[s.id for s in selected_candidates]}")

        if not selected_candidates:
            continue
        if len(selected_candidates) <= 3 and RUN_ARGS.method == "dgwo":
            # Exhaustive direct comparison for 1..3 feasible peers.
            # The same fitness objective is used; no DGWO iterations.
            decision_start = perf_counter()
            if len(selected_candidates) == 1:
                candidate_order = [selected_candidates[0]]
            else:
                candidate_order, decision_metrics = choose(
                    task, selected_candidates, "greedy",
                    seed=RUN_ARGS.optimizer_seed + task.task_id * 1009,
                    wolves=RUN_ARGS.wolves, iterations=RUN_ARGS.iterations)
                parameters.setdefault("decision_metrics", []).append(decision_metrics)
            _timing_entry["selection_ms"] += (perf_counter() - decision_start) * 1000.0
            print(f"DIRECT FITNESS SELECTION | task={task.task_id} | candidates={len(selected_candidates)}")
        elif len(selected_candidates) == 1:
            candidate_order = [selected_candidates[0]]
        else:
            decision_start = perf_counter()
            candidate_order, decision_metrics = choose(
                task, selected_candidates, RUN_ARGS.method,
                seed=RUN_ARGS.optimizer_seed + task.task_id * 1009,
                wolves=RUN_ARGS.wolves, iterations=RUN_ARGS.iterations)
            _timing_entry["selection_ms"] += (perf_counter() - decision_start) * 1000.0
            parameters.setdefault("decision_metrics", []).append(decision_metrics)
            print(f"SELECTION METRICS | Task {task.task_id} | {decision_metrics}")

        verification_messages = []
        verification_start = perf_counter()
        for candidate in candidate_order:
            feasible, verification = verify_candidate_for_execution(task, candidate)
            if feasible:
                selected_server = candidate
                selected_verification = verification
                verification_messages.append(f"FINAL VERIFICATION PASSED | node={candidate.id} | stage={stage_name}")
                break
            verification_messages.append(f"FINAL VERIFICATION FAILED | node={candidate.id} | reason={verification}")
        _timing_entry["final_verification_ms"] += (perf_counter() - verification_start) * 1000.0
        for verification_message in verification_messages:
            print(verification_message)
        if selected_server is not None:
            break

    _timing_entry["total_decision_ms"] = sum(_timing_entry[k] for k in (
        "preselection_ms", "preselection_revalidation_ms", "selection_ms", "final_verification_ms"))
    print(f"FULL DECISION METRICS | {_timing_entry}")

    if selected_server is None:
        print("NO FEASIBLE EDGE AFTER LOCAL/GLOBAL SEARCH | SOURCE IOT FALLBACK")
        source_check = evaluate_source_iot(task, source_user, parameters)
        if source_check["feasible"]:
            schedule_source_iot(task, source_user, parameters, current_step, source_check)
        else:
            reject_task(task, parameters, "No feasible Edge or source IoT: " + source_check["reason"])
        parameters["next_task_index"] += 1
        return

    offloading_scope = (
        "LOCAL" if getattr(selected_server, "cluster_id", None) == source_cluster_id
        else "GLOBAL"
    )
    if selected_server is not None:
        # ==================================================
        # FINAL OFFLOADING
        # ==================================================

        print()
        print("-" * 110)
        print(f"{offloading_scope} OFFLOADING")
        print("-" * 110)
        print(
            f"Task {task.task_id} | Source User = {task.source_user_id} | "
            f"Source Cluster = {source_cluster_id} | "
            f"Selected Edge Node = {selected_server.id} | "
            f"Target Cluster = {getattr(selected_server, 'cluster_id', None)}"
        )

        try:
            # Make the scope explicit for the offloading module.
            source_user.cluster_id = source_cluster_id

            offload_result = offload_task(
                task=task,
                selected_server=selected_server,
                current_step=current_step,
            )

            offload_result["source_user_id"] = task.source_user_id
            offload_result["selected_server_id"] = selected_server.id
            offload_result["offloading_scope"] = offloading_scope
            parameters["offloaded_tasks"][task.task_id] = offload_result

            # Preserve the original analytical estimate for comparison, but
            # never release resources until actual transfer has finished.
            estimated_completion_step = current_step + max(
                1, math.ceil(float(offload_result["total_delay"]))
            )
            flow = offload_result.pop("_network_flow", None)
            # Analytical ceil(total_delay) is only a reference, not the
            # expected completion of a two-stage discrete-event workflow.
            # For an uncontended flow, EdgeSimPy transfers data in whole ticks;
            # processing is scheduled in separate whole ticks after the flow.
            # Contention can extend the real transfer, so this is a baseline
            # estimate, NOT a guaranteed completion time or admission test.
            estimated_tick_duration = 1.0
            predicted_transfer_ticks = (
                max(1, math.ceil(float(offload_result["transmission_delay"]) / estimated_tick_duration))
                if offload_result.get("flow_id") is not None else 0
            )
            predicted_processing_ticks = max(
                1, math.ceil((float(offload_result["communication_delay"]) +
                              float(offload_result["computation_delay"])) / estimated_tick_duration)
            )
            offload_result["estimated_tick_completion_step_uncontended"] = (
                current_step + predicted_transfer_ticks + predicted_processing_ticks
            )
            offload_result["estimated_tick_delay_uncontended_s"] = estimated_uncontended_delay(
                offload_result["transmission_delay"], offload_result["communication_delay"],
                offload_result["computation_delay"], has_network_flow=flow is not None
            )
            offload_result["estimated_completion_step"] = estimated_completion_step
            offload_result["completion_step"] = None
            offload_result["resources_released"] = False
            offload_result["arrival_step"] = current_step
            offload_result["network_flow_start_step"] = (
                getattr(flow, "start", None) if flow is not None else None
            )
            if flow is not None:
                print(
                    f"FLOW AUDIT CREATE | Task {task.task_id} | "
                    f"current_step={current_step} | "
                    f"start={getattr(flow, 'start', None)}"
                )
            offload_result["simulation_tick_seconds"] = 1.0
            offload_result["status"] = "TRANSFERRING" if flow is not None else "PROCESSING"
            initial_completion = (
                None if flow is not None else current_step + max(
                    1, math.ceil(float(offload_result["communication_delay"]) +
                                 float(offload_result["computation_delay"]))
                )
            )
            if flow is None:
                offload_result["processing_completion_step"] = initial_completion
                offload_result["execution_start_step"] = current_step
            # Each active task holds its own CPU/memory reservation. Tasks on
            # the same Edge Node may overlap, without sharing reserved units.
            same_node_tasks = [entry for entry in parameters["active_edge_tasks"]
                               if entry["server"].id == selected_server.id]
            offload_result["parallel_execution_model"] = "DEDICATED_CPU_UNITS"
            offload_result["concurrent_tasks_on_node_at_admission"] = len(same_node_tasks) + 1
            offload_result["other_active_task_ids_on_node"] = [entry["task_id"] for entry in same_node_tasks]
            assert selected_server.cpu_demand <= selected_server.cpu, "CPU overcommit"
            assert selected_server.memory_demand <= selected_server.memory, "Memory overcommit"
            parameters["active_edge_tasks"].append({
                "task_id": task.task_id,
                "server": selected_server,
                "cpu_units_allocated": offload_result["cpu_units_allocated"],
                "memory_allocated_mb": offload_result["memory_allocated_mb"],
                "network_flow": flow,
                "phase": offload_result["status"],
                "arrival_step": current_step,
                "execution_start_step": current_step if flow is None else None,
                "deadline": float(task.max_delay),
                "communication_delay": float(offload_result["communication_delay"]),
                "computation_delay": float(offload_result["computation_delay"]),
                "completion_step": initial_completion,
            })

            print(f"Task {task.task_id} | {offload_result['offloading_scope']} | "
                  f"Edge Node {selected_server.id} | Status={offload_result['status']} | "
                  f"Delay={offload_result['total_delay']:.4f}s | "
                  f"Energy={offload_result['reserved_energy_j']:.6f}J | "
                  f"CPU={offload_result['cpu_units_allocated']} | "
                  f"Memory={offload_result['memory_allocated_mb']}MB | "
                  f"Path={offload_result['path']}")

        except Exception as error:
            print(f"OFFLOADING FAILED: {error}")
            # Do not report a failed reservation as completed.
            source_check = evaluate_source_iot(task, source_user, parameters)
            if source_check["feasible"]:
                print("EDGE COMMIT FAILED | SOURCE IOT LAST RESORT")
                schedule_source_iot(task, source_user, parameters, current_step, source_check)
            else:
                reject_task(task, parameters, "Edge commit failed; source infeasible: " + str(error))

    else:
        print()
        print("-" * 110)
        print(f"Task {task.task_id} | NO FEASIBLE NETWORK-WIDE EDGE CANDIDATE")
        print("SOURCE IOT LOCAL-EXECUTION FALLBACK")
        print("-" * 110)

        source_check = evaluate_source_iot(task, source_user, parameters)
        if source_check["feasible"]:
            schedule_source_iot(task, source_user, parameters, current_step, source_check)
        else:
            reject_task(task, parameters, "No feasible Edge or source IoT: " + source_check["reason"])

    parameters["next_task_index"] += 1


# ==========================================================
# Step 6: Simulation Stopping Criterion
# ==========================================================

MAX_SIMULATION_STEPS = 200

def stopping_criterion(simulator):
    # Stop after every generated task has arrived and been processed.
    # The maximum-step guard prevents an accidental endless simulation.
    all_tasks_processed = (
        simulation_parameters["next_task_index"] >= len(tasks)
        and len(simulation_parameters.get("active_edge_tasks", [])) == 0
        and len(simulation_parameters.get("active_source_tasks", [])) == 0
    )
    reached_safety_limit = (
        simulator.schedule.steps >= MAX_SIMULATION_STEPS
    )
    return all_tasks_processed or reached_safety_limit


# ==========================================================
# Step 7: Initialize Simulator
# ==========================================================

simulator = Simulator(

    resource_management_algorithm=
        task_arrival,

    resource_management_algorithm_parameters=
        simulation_parameters,

    stopping_criterion=
        stopping_criterion,

    tick_duration=
        1,

    tick_unit=
        "seconds",
)


# ==========================================================
# Step 8: Load Dataset
# ==========================================================

parser = argparse.ArgumentParser(description="Run a clustered MEC simulation dataset")
parser.add_argument("--dataset", default="C50.json")
parser.add_argument("--method", choices=["dgwo","greedy","random","local_iot","gwo_no_filter","pso"], default="dgwo")
parser.add_argument("--optimizer-seed", type=int, default=42)
parser.add_argument("--arrival-seed", type=int, default=42)
parser.add_argument("--wolves", type=int, default=10)
parser.add_argument("--iterations", type=int, default=5)
parser.add_argument("--weights", default="0.4,0.4,0.2")
parser.add_argument("--summary-json", default=None)
run_args = parser.parse_args()
RUN_ARGS = run_args
ARRIVAL_SEED = run_args.arrival_seed
arrival_rng.seed(ARRIVAL_SEED)
import fitness as fitness_module
weights = [float(x) for x in run_args.weights.split(",")]
if len(weights) != 3 or any(w < 0 for w in weights) or abs(sum(weights)-1)>1e-9:
    raise ValueError("Weights must be three nonnegative numbers summing to 1")
fitness_module.W_DELAY, fitness_module.W_ENERGY, fitness_module.W_RESOURCE = weights
# Resolve the dataset relative to this script, independent of PowerShell's working directory.
from pathlib import Path
DATASET_PATH = Path(run_args.dataset)
if not DATASET_PATH.is_absolute():
    DATASET_PATH = Path(__file__).resolve().parent / DATASET_PATH
if not DATASET_PATH.is_file():
    raise FileNotFoundError(
        f"Dataset not found: {DATASET_PATH}. "
        "Place the requested C10/C20/C50/C100/C200 JSON beside this script or pass an absolute path."
    )
try:
    import json
    with DATASET_PATH.open("r", encoding="utf-8-sig") as dataset_file:
        json.load(dataset_file)
except (ValueError, UnicodeError) as exc:
    raise ValueError(f"Dataset JSON is invalid: {DATASET_PATH}: {exc}") from exc
print(f"DATASET PATH VERIFIED: {DATASET_PATH}")
simulator.initialize(input_file=str(DATASET_PATH))

# Balanced workload-source distribution across all available clusters.
# Task 10 is deliberately sourced from cluster 1 for the global-feasibility
# validation scenario in C20/C50/C100. Task 11 remains the IoT fallback case.
valid_source_ids = sorted(user.id for user in User.all())
if not valid_source_ids:
    raise ValueError("Dataset contains no IoT users.")
edge_nodes = [s for s in EdgeServer.all() if getattr(s, "role", None) == "edge_node"]
edge_count = len(edge_nodes)
cluster_count = edge_count // 10
if edge_count not in (10, 20, 50, 100, 200) or cluster_count * 10 != edge_count:
    raise ValueError(f"Unexpected edge network size: {edge_count}")
source_clusters = [(i % cluster_count) + 1 for i in range(20)]
if cluster_count > 1:
    # Swap sources of Tasks 1 and 10 to retain exact per-cluster balance.
    source_clusters[0], source_clusters[9] = source_clusters[9], source_clusters[0]
for index, task in enumerate(tasks):
    cluster_id = source_clusters[index]
    # Pick distinct users where possible within each cluster.
    user_offset = index // cluster_count
    desired_user = (cluster_id - 1) * 10 + (user_offset % 10) + 1
    if desired_user not in valid_source_ids:
        raise ValueError(f"Missing source IoT user {desired_user} in cluster {cluster_id}")
    task.source_user_id = desired_user
    print(f"TASK SOURCE | task={task.task_id} | user={desired_user} | cluster={cluster_id}")
# C10 has no global cluster; preserve Task 10's original memory there.
# For multi-cluster validation, Task 10 needs 18 GB; only a remote
# validation peer has 20 GB, so LOCAL fails and GLOBAL can succeed.
if cluster_count > 1:
    tasks[9].memory_required = 18000
    print("GLOBAL VALIDATION INPUT | Task 10 memory_required=18000 MB")

assert len(tasks) == 20, "Expected exactly 20 tasks."
assert all(t.source_user_id in valid_source_ids for t in tasks)



# ==========================================================
# Step 9: Assign Static Mobility
# ==========================================================

for user in User.all():

    user.mobility_model = static_mobility

    # EdgeSimPy User.step() indexes coordinates_trace at every simulator tick,
    # even when the selected mobility model keeps the user stationary.
    # The supplied C20 dataset has only 31 positions (indices 0..30),
    # while 22 task arrivals and completions can run beyond step 30.
    # Extend the static trace to cover the simulation safety limit.
    trace = list(getattr(user, "coordinates_trace", None) or [])
    fixed_position = list(user.coordinates)
    required_trace_length = MAX_SIMULATION_STEPS + 2
    if len(trace) < required_trace_length:
        trace.extend([list(fixed_position) for _ in range(required_trace_length - len(trace))])
    user.coordinates_trace = trace

print(f"MOBILITY TRACE VERIFIED: {len(User.all())} users; "
      f"minimum length={min(len(u.coordinates_trace) for u in User.all())}; "
      f"simulation max steps={MAX_SIMULATION_STEPS}")


# ==========================================================
# Step 10: Initialization Confirmation
# ==========================================================

print("=" * 110)

print(
    "EDGE SIMPY DATASET "
    "INITIALIZED SUCCESSFULLY"
)

print("=" * 110)


# ==========================================================
# Step 11: Infrastructure Summary
# ==========================================================

print("\nInfrastructure Summary")
print("-" * 110)

print(
    f"EdgeServer objects : "
    f"{len(EdgeServer.all())}"
)

print(
    f"Users              : "
    f"{len(User.all())}"
)

print(
    f"Base Stations      : "
    f"{len(BaseStation.all())}"
)

print(
    f"Network Switches   : "
    f"{len(NetworkSwitch.all())}"
)

print(
    f"Network Links      : "
    f"{len(NetworkLink.all())}"
)


# ==========================================================
# Step 12: Users
# ==========================================================

print("\nUsers")
print("-" * 110)

for user in User.all():

    print(

        f"User {user.id} | "

        f"Base Station="
        f"{user.base_station.id}"
    )


print("\nSOURCE IOT DEVICE SPECIFICATIONS")
print("-" * 110)
for user in User.all():
    rate = getattr(user, "local_processing_capability", DEFAULT_IOT_PROCESSING_RATE)
    energy = getattr(user, "local_remaining_energy_j", DEFAULT_IOT_ENERGY_J)
    print(
        f"IoT {user.id} | Local CPU Rate={rate} cycles/s | "
        f"Remaining Energy={energy} J"
    )
print(f"Arrival seed={ARRIVAL_SEED} | Nominal rate={arrival_rate}/s | "
      f"Arrival probability={arrival_probability:.6f}/tick | "
      "At most one arrival per 1-second tick (Poisson approximation)")


# ==========================================================
# Step 13: User Network Mapping
# ==========================================================

print("\nUser Network Mapping")
print("-" * 120)

for user in User.all():

    base_station = (
        user.base_station
    )

    network_switch = (
        base_station.network_switch
    )

    print(

        f"User {user.id} | "

        f"Base Station="
        f"{base_station.id} | "

        f"Network Switch="
        f"{network_switch.id}"
    )


# ==========================================================
# Step 14: Network Links
# ==========================================================

print("\nNetwork Links")
print("-" * 130)

for link in NetworkLink.all():

    nodes = link.nodes

    if len(nodes) >= 2:

        node_1 = nodes[0].id
        node_2 = nodes[1].id

    else:

        node_1 = "?"
        node_2 = "?"

    print(

        f"Link {link.id} | "

        f"{node_1} <-> {node_2} | "

        f"Bandwidth="
        f"{link.bandwidth} Mbps | "

        f"Demand="
        f"{link.bandwidth_demand} Mbps | "

        f"Delay="
        f"{link.delay} s"
    )


# ==========================================================
# Step 15: Resource Table
# ==========================================================

resource_table = (
    build_resource_table()
)

print("\nResource Table")
print("-" * 240)

for row in resource_table:

    print(

        f"Edge Node "
        f"{row['server_id']} | "

        f"Cluster="
        f"{row['cluster_id']} | "

        # --------------------------------------------------
        # CPU Resource
        # --------------------------------------------------

        f"CPU="
        f"{row['cpu']} | "

        f"CPU Demand="
        f"{row['cpu_demand']} | "

        f"Available CPU="
        f"{row['available_cpu']} | "

        f"CPU Utilization="
        f"{row['cpu_utilization']:.2f} | "

        # --------------------------------------------------
        # Processing Capability
        # --------------------------------------------------

        f"Processing Capability="
        f"{row['processing_capability_ghz']:.1f} GHz | "

        f"f_j="
        f"{row['processing_capability']:.0f} cycles/s | "

        f"F_j_avail="
        f"{row['available_processing_capability']:.0f} cycles/s | "

        # --------------------------------------------------
        # Memory
        # --------------------------------------------------

        f"Memory="
        f"{row['memory']} MB | "

        f"Memory Demand="
        f"{row['memory_demand']} MB | "

        f"Available Memory="
        f"{row['available_memory']} MB | "

        f"Memory Utilization="
        f"{row['memory_utilization']:.2f} | "

        # --------------------------------------------------
        # Disk
        # --------------------------------------------------

        f"Disk="
        f"{row['disk']} MB | "

        f"Disk Demand="
        f"{row['disk_demand']} MB | "

        f"Available Disk="
        f"{row['available_disk']} MB | "

        # --------------------------------------------------
        # Energy
        # --------------------------------------------------

        f"Initial Energy="
        f"{row['initial_energy_j']} J | "

        f"Remaining Energy="
        f"{row['remaining_energy_j']} J | "

        # --------------------------------------------------
        # Availability
        # --------------------------------------------------

        f"Available="
        f"{row['available']}"
    )


# ==========================================================
# Step 16: Resource Table Summary
# ==========================================================

print("\nResource Table Summary")
print("-" * 110)

print(
    f"Candidate Edge Nodes : "
    f"{len(resource_table)}"
)

available_nodes = [

    row

    for row in resource_table

    if row["available"]
]

print(
    f"Available Edge Nodes : "
    f"{len(available_nodes)}"
)


# ==========================================================
# Step 17: Task Configuration
# ==========================================================

print("\nTask Configuration")
print("-" * 130)

print(
    f"Total Tasks : "
    f"{len(tasks)}"
)

for task in tasks:

    if task.max_delay > 0:

        required_processing_rate = (
            task.workload
            / task.max_delay
        )

        required_communication_rate = (
            task.data_size
            * 8
            / task.max_delay
        )

    else:

        required_processing_rate = float(
            "inf"
        )

        required_communication_rate = float(
            "inf"
        )

    print(

        f"Task {task.task_id} | "

        f"Source User="
        f"{task.source_user_id} | "

        f"Workload="
        f"{task.workload} cycles | "

        f"Memory="
        f"{task.memory_required} MB | "

        f"Data="
        f"{task.data_size} MB | "

        f"F_req="
        f"{required_processing_rate:.0f} cycles/s | "

        f"R_th="
        f"{required_communication_rate:.4f} Mbps | "

        f"Deadline="
        f"{task.max_delay} s"
    )


# ==========================================================
# Step 18: Start Simulation
# ==========================================================

print()
print(
    "Starting simulation..."
)
print("-" * 110)

simulator.run_model()

# ==========================================================
# Step 19: Final Filtering / Offloading Summary
# ==========================================================

print()
print("=" * 110)
print("FINAL FILTERING / OFFLOADING SUMMARY")
print("=" * 110)

candidate_sets = simulation_parameters["candidate_sets"]

if candidate_sets:
    for task_id in sorted(candidate_sets):
        candidate_ids = [server.id for server in candidate_sets[task_id]]
        print(f"Task {task_id} | Feasible Candidates = {candidate_ids}")
else:
    print("No candidate sets were generated.")

print()
print("OFFLOADING SUMMARY")
print("-" * 110)

offloaded_tasks = simulation_parameters["offloaded_tasks"]

if offloaded_tasks:
    for task_id in sorted(offloaded_tasks):
        result = offloaded_tasks[task_id]
        print(format_execution_summary(task_id, result))
        if "observed_total_delay" in result:
            print(
                f"  Timing: analytical={result.get('total_delay', 'N/A')}s | "
                f"observed={result.get('observed_total_delay', 'N/A')}s | "
                f"deadline met={result.get('deadline_met_observed', 'N/A')} | "
                f"completed at step={result.get('completion_step', 'N/A')}"
            )
else:
    print("No tasks were offloaded.")

print()
# Execution-mode counts are computed from results, never from task IDs.
mode_counts = {"LOCAL_OFFLOADING": 0, "GLOBAL_OFFLOADING": 0,
               "SOURCE_IOT": 0, "REJECTED": 0}
for _r in offloaded_tasks.values():
    if _r.get("status") == "REJECTED":
        mode_counts["REJECTED"] += 1
    elif _r.get("selected_server_id") is None:
        mode_counts["SOURCE_IOT"] += 1
    elif str(_r.get("offloading_scope", "")).upper() in ("GLOBAL", "REMOTE"):
        mode_counts["GLOBAL_OFFLOADING"] += 1
    else:
        mode_counts["LOCAL_OFFLOADING"] += 1
print("\nAUTOMATIC EXECUTION COUNTS | " + " | ".join(
    f"{_k}={_v}" for _k, _v in mode_counts.items()))

print("TASK ARRIVAL / EXECUTION ACCOUNTING")
print("-" * 110)
print(f"Tasks generated         : {len(tasks)}")
print(f"Tasks processed         : {simulation_parameters['next_task_index']}")
print(f"Tasks with recorded result: {len(simulation_parameters['offloaded_tasks'])}")
not_processed = len(tasks) - simulation_parameters["next_task_index"]
if not_processed > 0:
    print(f"WARNING: {not_processed} generated task(s) did not arrive before the simulation stopped.")
else:
    print("All generated tasks reached the processing pipeline.")

print()
print("FINAL RESOURCE STATE AFTER OFFLOADING")
print("-" * 110)

for server in EdgeServer.all():
    if getattr(server, "role", None) != "edge_node":
        continue

    available_cpu = server.cpu - server.cpu_demand
    available_memory = server.memory - server.memory_demand
    remaining_energy = getattr(server, "remaining_energy_j", 0.0)

    print(
        f"Node {server.id} | "
        f"CPU Demand={server.cpu_demand} | "
        f"Available CPU={available_cpu} | "
        f"Memory Demand={server.memory_demand} MB | "
        f"Available Memory={available_memory} MB | "
        f"Remaining Energy={remaining_energy:.6f} J | "
        f"Available={server.available}"
    )

# ==========================================================
# Step 20: Completed
# ==========================================================

print()
print("=" * 110)
print("SIMULATION COMPLETED")
if simulation_parameters.get("active_edge_tasks") or simulation_parameters.get("active_source_tasks"):
    print("WARNING: Simulation stopped with active tasks still pending.")
print("=" * 110)

# Machine-readable experiment output: do not derive statistics from printed traces.
if RUN_ARGS.summary_json:
    import json as _json
    from pathlib import Path as _Path
    records=[]
    for _id,_r in sorted(simulation_parameters["offloaded_tasks"].items()):
        records.append({"task_id":_id,"status":_r.get("status"),
            "scope":_r.get("offloading_scope"),"server":_r.get("selected_server_id"),
            "analytical_delay_s":_r.get("total_delay"),
            "observed_delay_s":_r.get("observed_total_delay"),
            "computation_energy_j":_r.get("reserved_energy_j"),
            "transmission_energy_j":_r.get("transmission_energy_j",0.0),
            "source_energy_j":_r.get("source_energy_j",_r.get("reserved_energy_j",0.0) if _r.get("selected_server_id") is None else 0.0),
            "total_system_energy_j":_r.get("total_system_energy_j",_r.get("reserved_energy_j",0.0)),
            "deadline_met":_r.get("deadline_met_observed"),
            "rejection_reason":_r.get("rejection_reason"),
            "source_resource_audit":{key:value for key,value in _r.items()
                if key.startswith("source_") and key not in {"source_user_id", "source_energy_j"}}})
    metrics=simulation_parameters.get("decision_metrics",[])
    payload={"method":RUN_ARGS.method,"seed":RUN_ARGS.optimizer_seed,
        "wolves":RUN_ARGS.wolves,"iterations":RUN_ARGS.iterations,
        "weights":weights,"tasks":records,"decisions":metrics,
        "full_decisions":simulation_parameters.get("full_decision_metrics", []),
        "task_count":len(tasks),"processed":simulation_parameters["next_task_index"],
        "arrival_seed":ARRIVAL_SEED,"arrival_trace":simulation_parameters["arrival_trace"]}
    out=_Path(RUN_ARGS.summary_json)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(_json.dumps(payload,indent=2,default=str),encoding="utf-8")
    print(f"EXPERIMENT JSON: {out}")
