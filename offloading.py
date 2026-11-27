"""
offloading.py

Local task-offloading stage for the current EdgeSimPy MEC project.

Flow implemented here:

    Selected Edge Node
            |
            v
    Feasibility re-check
            |
            v
    Resource reservation
            |
            +--> CPU demand update
            +--> Memory demand update
            +--> Energy reservation
            +--> NetworkFlow for non-local transmission
            |
            v
       Offloaded task record

Important:
- This module does NOT modify the JSON dataset.
- Resource changes are made only to the current simulation objects.
- The current stage reserves the execution resources for the task.
- Releasing resources after task completion should be handled by a
  later execution/completion stage.
"""

from math import ceil
import math
from timing_model import estimated_uncontended_delay

import networkx as nx

from edge_sim_py import EdgeServer, NetworkFlow, User
from network_resources import get_network_resources
from energy_model import transmission_energy_j


# Same project-level energy coefficient currently used by filtering/fitness.
KAPPA = 1e-28


# ======================================================================
# Helper: Current processing rate per CPU resource unit
# ======================================================================

def get_processing_rate_per_unit(server):
    """Return the processing rate represented by one CPU resource unit."""
    rate = getattr(server, "cpu_rate_per_unit", None)

    if rate is None:
        frequency_ghz = getattr(server, "processing_capability_ghz", None)
        if frequency_ghz is None:
            raise ValueError(
                f"Edge Node {server.id} is missing processing capability information."
            )
        rate = frequency_ghz * 1_000_000_000

    rate = float(rate)

    if rate <= 0:
        raise ValueError(
            f"Edge Node {server.id} has an invalid CPU processing rate: {rate}."
        )

    return rate


# ======================================================================
# Helper: CPU allocation required by the task
# ======================================================================

def calculate_cpu_units_required(task, server):
    """
    Calculate the minimum integer CPU resource units required by a task.

    The task does not have an explicit cpu_cores_required field, so the
    allocation is derived from:

        F_req = workload / deadline
        CPU_units = ceil(F_req / rate_per_unit)

    At least one CPU resource unit is reserved for a task.
    """
    if task.max_delay <= 0:
        raise ValueError("Task maximum delay must be greater than zero.")

    required_processing_rate = (
        float(task.workload) / float(task.max_delay)
    )

    rate_per_unit = get_processing_rate_per_unit(server)

    cpu_units = ceil(
        required_processing_rate / rate_per_unit
    )

    return max(1, int(cpu_units))


# ======================================================================
# Helper: Energy reservation
# ======================================================================

def calculate_reserved_energy(task, allocated_processing_capability):
    """
    Estimate energy reserved for the task's computation.

        E = KAPPA * W * f^2

    Here f is the processing capability allocated to this task rather than
    the full current node capability.
    """
    return float(
        KAPPA
        * task.workload
        * (allocated_processing_capability ** 2)
    )


# ======================================================================
# Helper: Network path as EdgeSimPy NetworkSwitch objects
# ======================================================================

def get_task_path(task, server):
    """Return the shortest-delay path from the task source user to server."""
    user = User.find_by_id(task.source_user_id)

    if user is None:
        raise ValueError(
            f"Source User {task.source_user_id} was not found."
        )

    source_switch = user.base_station.network_switch
    target_switch = server.base_station.network_switch

    topology = user.model.topology

    path = nx.shortest_path(
        G=topology,
        source=source_switch,
        target=target_switch,
        weight="delay",
        method="dijkstra",
    )

    return user, path


# ======================================================================
# Helper: Validate resources before changing anything
# ======================================================================

def validate_offloading_capacity(task, server, cpu_units, network_info):
    """Re-check all directly consumed resources immediately before commit."""
    if not isinstance(server, EdgeServer):
        raise TypeError("Selected target must be an EdgeServer object.")

    if getattr(server, "role", None) != "edge_node":
        raise ValueError(
            f"Edge Server {server.id} is not an Edge Node candidate."
        )

    if not getattr(server, "available", False):
        raise ValueError(
            f"Edge Node {server.id} is currently unavailable."
        )

    # Dedicated CPU units: existing task reservations are not reused.
    available_cpu = server.cpu - server.cpu_demand
    available_memory = server.memory - server.memory_demand

    if available_cpu < cpu_units:
        raise ValueError(
            f"Edge Node {server.id} does not have enough CPU. "
            f"Required={cpu_units}, Available={available_cpu}."
        )

    if available_memory < task.memory_required:
        raise ValueError(
            f"Edge Node {server.id} does not have enough memory. "
            f"Required={task.memory_required} MB, "
            f"Available={available_memory} MB."
        )

    remaining_energy = getattr(
        server,
        "remaining_energy_j",
        0.0,
    )

    if network_info["transmission_delay"] == float("inf"):
        raise ValueError(
            f"Task {task.task_id} has no feasible transmission path to Edge Node {server.id}."
        )

    return True


# ======================================================================
# IoT Local Execution / Fallback
# ======================================================================

def execute_task_locally(task, user, processing_capability, remaining_energy_j):
    """Prepare local execution; the source scheduler records completion later."""
    if user is None:
        raise ValueError("Source IoT User was not found.")

    processing_capability = float(processing_capability)
    if not math.isfinite(processing_capability) or processing_capability <= 0:
        raise ValueError(
            f"Source IoT User {user.id} has an invalid processing capability."
        )

    remaining_energy_j = float(remaining_energy_j)
    if not math.isfinite(remaining_energy_j) or remaining_energy_j < 0:
        raise ValueError("Source IoT User has an invalid remaining energy value.")

    computation_delay = float(task.workload) / processing_capability
    reserved_energy = calculate_reserved_energy(task, processing_capability)

    if reserved_energy > remaining_energy_j:
        raise ValueError(
            f"Source IoT User {user.id} does not have enough remaining energy. "
            f"Required={reserved_energy:.6f} J, Remaining={remaining_energy_j:.6f} J."
        )

    updated_remaining_energy = remaining_energy_j - reserved_energy

    task.selected_server_id = None
    task.offloaded = False
    task.offloading_status = "local_execution"

    return {
        "task_id": task.task_id,
        "source_user_id": task.source_user_id,
        "execution_device_id": user.id,
        "execution_scope": "SOURCE_IOT",
        "execution_mode": "LOCAL_EXECUTION",
        "processing_capability": processing_capability,
        "computation_delay": computation_delay,
        "communication_delay": 0.0,
        "transmission_delay": 0.0,
        "total_delay": computation_delay,
        "reserved_energy_j": reserved_energy,
        "transmission_energy_j": 0.0,
        "source_energy_j": reserved_energy,
        "total_system_energy_j": reserved_energy,
        "remaining_energy_j": updated_remaining_energy,
        "path": [],
        "flow_id": None,
        "status": "PROCESSING_LOCALLY",
    }


# ======================================================================
# Main Offloading Function
# ======================================================================

def offload_task(task, selected_server, current_step=None):
    """
    Reserve resources and offload a task to the selected Edge Node.

    For a remote target, an EdgeSimPy NetworkFlow is created to represent
    the input-data transmission from the source user to the selected node.

    Returns a dictionary containing the offloading decision and the resource
    reservation details. The main simulation tracks the returned reservation
    and releases CPU/memory when the modeled completion step is reached.
    """
    # ------------------------------------------------------------------
    # Network information is calculated before committing resources.
    # ------------------------------------------------------------------
    network_info = get_network_resources(
        task=task,
        server=selected_server,
    )

    cpu_units = calculate_cpu_units_required(
        task,
        selected_server,
    )

    rate_per_unit = get_processing_rate_per_unit(
        selected_server,
    )

    allocated_processing_capability = (
        cpu_units * rate_per_unit
    )

    computation_delay = (
        float(task.workload)
        / allocated_processing_capability
    )

    reserved_energy = calculate_reserved_energy(
        task,
        allocated_processing_capability,
    )

    remaining_energy = float(
        getattr(
            selected_server,
            "remaining_energy_j",
            0.0,
        )
    )

    if reserved_energy > remaining_energy:
        raise ValueError(
            f"Edge Node {selected_server.id} does not have enough remaining energy. "
            f"Required={reserved_energy:.6f} J, "
            f"Remaining={remaining_energy:.6f} J."
        )

    validate_offloading_capacity(
        task,
        selected_server,
        cpu_units,
        network_info,
    )

    # Resolve source and confirm its battery can pay for the input transfer.
    user, path = get_task_path(task, selected_server)
    tx_energy = transmission_energy_j(network_info)
    source_energy_before = float(getattr(user, "local_remaining_energy_j", 1000.0))
    if source_energy_before < tx_energy:
        raise ValueError(f"Source IoT {user.id} cannot transmit task {task.task_id}: "
                         f"requires {tx_energy:.6f} J, available {source_energy_before:.6f} J")
    if not path:
        raise ValueError(
            f"No network path exists for task {task.task_id} to Edge Node {selected_server.id}."
        )

    total_delay = (
        computation_delay
        + float(network_info["communication_delay"])
        + float(network_info["transmission_delay"])
    )
    predicted_tick_delay = estimated_uncontended_delay(
        network_info["transmission_delay"], network_info["communication_delay"], computation_delay
    )
    if (not math.isfinite(total_delay) or total_delay > float(task.max_delay)
            or predicted_tick_delay > float(task.max_delay)):
        raise ValueError(
            f"Task {task.task_id} cannot meet its deadline on Edge Node {selected_server.id}: "
            f"analytical={total_delay:.4f}s, tick estimate={predicted_tick_delay:.4f}s, "
            f"deadline={task.max_delay}s."
        )

    # ------------------------------------------------------------------
    # Commit resource reservation.
    # ------------------------------------------------------------------
    old_cpu_demand = selected_server.cpu_demand
    old_memory_demand = selected_server.memory_demand
    old_remaining_energy = selected_server.remaining_energy_j
    old_available = selected_server.available

    selected_server.cpu_demand += cpu_units
    selected_server.memory_demand += task.memory_required
    selected_server.remaining_energy_j = (
        remaining_energy - reserved_energy
    )
    user.local_remaining_energy_j = source_energy_before - tx_energy

    # Mark the server unavailable only when it has no CPU or memory left.
    if (
        selected_server.cpu_demand >= selected_server.cpu
        or selected_server.memory_demand >= selected_server.memory
    ):
        selected_server.available = False

    # ------------------------------------------------------------------
    # Create the transmission flow for non-local execution.
    # ------------------------------------------------------------------
    flow = None

    # In this project, LOCAL/REMOTE refers to the Cluster scope,
    # not whether the source user and target share the same switch.
    # Same-switch execution may have zero network delay, while a
    # different node in the same cluster can still require an
    # intra-cluster network transfer.
    source_cluster_id = getattr(user, "cluster_id", None)
    target_cluster_id = getattr(selected_server, "cluster_id", None)

    is_local = (
        source_cluster_id is not None
        and target_cluster_id == source_cluster_id
    )

    # A NetworkFlow is needed whenever data actually traverses a
    # network path, including intra-cluster transfers.
    has_network_transfer = len(path) > 1

    try:
        if has_network_transfer:
            if current_step is None:
                current_step = user.model.schedule.steps

            flow = NetworkFlow(
                topology=user.model.topology,
                source=user,
                target=selected_server,
                # Use the same tick convention as NetworkFlow.end.
                start=current_step,
                path=path,
                data_to_transfer=task.data_size * 8,
                metadata={
                    "type": "task_data",
                    "object": task,
                    "task_id": task.task_id,
                },
            )

            user.model.initialize_agent(agent=flow)
    except Exception:
        # Keep the reservation atomic if flow construction/registration fails.
        selected_server.cpu_demand = old_cpu_demand
        selected_server.memory_demand = old_memory_demand
        selected_server.remaining_energy_j = old_remaining_energy
        selected_server.available = old_available
        user.local_remaining_energy_j = source_energy_before
        raise

    # ------------------------------------------------------------------
    # Record execution/offloading information on the task object.
    # ------------------------------------------------------------------
    task.selected_server_id = selected_server.id
    task.offloaded = True
    task.offloading_status = "offloaded"

    result = {
        "task_id": task.task_id,
        "source_user_id": task.source_user_id,
        "selected_server_id": selected_server.id,
        "source_cluster_id": source_cluster_id,
        "cluster_id": target_cluster_id,
        "offloading_scope": "LOCAL" if is_local else "REMOTE",
        "cpu_units_allocated": cpu_units,
        "memory_allocated_mb": task.memory_required,
        "allocated_processing_capability": allocated_processing_capability,
        "computation_delay": computation_delay,
        "communication_delay": network_info["communication_delay"],
        "transmission_delay": network_info["transmission_delay"],
        "total_delay": (
            computation_delay
            + network_info["communication_delay"]
            + network_info["transmission_delay"]
        ),
        "reserved_energy_j": reserved_energy,
        "transmission_energy_j": tx_energy,
        "source_energy_j": tx_energy,
        "total_system_energy_j": reserved_energy + tx_energy,
        "remaining_energy_j": selected_server.remaining_energy_j,
        "path": [node.id for node in path],
        "local_execution": is_local,
        "flow_id": flow.id if flow is not None else None,
        "network_flow_start_step": getattr(flow, "start", None) if flow is not None else None,
        "_network_flow": flow,  # Runtime-only reference; never serialize this object.
        "status": "OFFLOADED",
    }

    return result
