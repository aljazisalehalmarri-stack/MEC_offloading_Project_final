"""
fitness.py

Fitness evaluation for candidate Edge Nodes.

Objective:
Minimize a weighted combination of:

    1. Total execution delay
    2. Energy consumption
    3. Resource cost

The function is used by DGWO later to compare
feasible candidate Edge Nodes.

Current weights:

    w_delay = 0.4
    w_energy = 0.4
    w_resource = 0.2
"""

import math
from edge_sim_py import EdgeServer

from network_resources import get_network_resources
from energy_model import total_offload_energy_j, transmission_energy_j, source_energy_available
from timing_model import estimated_uncontended_delay
from offloading import (
    calculate_cpu_units_required,
    get_processing_rate_per_unit,
    calculate_reserved_energy,
)


# ==========================================================
# Fitness Weights
# ==========================================================

W_DELAY = 0.4
W_ENERGY = 0.4
W_RESOURCE = 0.2


# ==========================================================
# Energy Model
# ==========================================================

KAPPA = 1e-28


# ==========================================================
# Calculate Computation Delay
# ==========================================================

def calculate_allocated_processing_capability(task, server):
    """Use the same integer CPU allocation rule as the offloading stage."""
    cpu_units = calculate_cpu_units_required(task, server)
    return cpu_units * get_processing_rate_per_unit(server)


def calculate_computation_delay(task, server):
    """Estimate computation delay using the allocated CPU capability."""
    capability = calculate_allocated_processing_capability(task, server)
    if capability <= 0:
        return float("inf")
    return float(task.workload) / capability


def calculate_energy_consumption(task, server):
    """Estimate computation energy using the offloading allocation model."""
    capability = calculate_allocated_processing_capability(task, server)
    return calculate_reserved_energy(task, capability)


# ==========================================================
# Resource Cost
# ==========================================================

def calculate_resource_cost(
    server,
    task=None,
):
    """
    Estimate CPU utilization after reserving this task's required units.

        Resource Cost = (CPU Demand + Required CPU Units) / CPU Capacity

    Lower projected utilization is preferred.
    """

    cpu = getattr(
        server,
        "cpu",
        0
    )

    cpu_demand = getattr(
        server,
        "cpu_demand",
        0
    )

    if cpu <= 0:
        return 1.0

    projected_demand = cpu_demand
    if task is not None:
        projected_demand += calculate_cpu_units_required(task, server)

    utilization = projected_demand / cpu

    # Keep value inside [0, 1].
    return max(
        0.0,
        min(
            1.0,
            utilization
        )
    )


# ==========================================================
# Normalize Delay
# ==========================================================

def normalize_delay(
    total_delay,
    deadline
):
    """
    Normalize delay with respect to the task deadline.

        normalized_delay = T_total / D_i
    """

    if deadline <= 0:
        return float("inf")

    return (
        total_delay
        / deadline
    )


# ==========================================================
# Normalize Energy
# ==========================================================

def normalize_energy(
    energy_required,
    remaining_energy
):
    """
    Legacy helper (not called by calculate_fitness).
    The active objective uses candidate-set Min-Max normalization below.
    Normalize energy consumption against the
    currently remaining energy.

        normalized_energy = E_req / E_rem
    """

    if remaining_energy <= 0:
        return float("inf")

    return (
        energy_required
        / remaining_energy
    )


# ==========================================================
# Calculate Fitness
# ==========================================================

def calculate_fitness(
    task,
    server,
    energy_reference=None,
    penalize_infeasible=False,
):
    """
    Calculate fitness for one feasible Edge Node.

    Lower fitness is better.

    Returns a dictionary containing all components.
    """

    if not isinstance(
        server,
        EdgeServer
    ):
        raise TypeError(
            "server must be an EdgeServer object."
        )

    # ------------------------------------------------------
    # Network information
    # ------------------------------------------------------

    network = get_network_resources(
        task,
        server
    )

    communication_delay = network[
        "communication_delay"
    ]

    transmission_delay = network[
        "transmission_delay"
    ]

    # ------------------------------------------------------
    # Computation delay
    # ------------------------------------------------------

    computation_delay = (
        calculate_computation_delay(
            task,
            server
        )
    )

    # ------------------------------------------------------
    # Total delay
    # ------------------------------------------------------

    total_delay = (
        communication_delay
        + transmission_delay
        + computation_delay
    )

    # ------------------------------------------------------
    # Energy
    # ------------------------------------------------------

    computation_energy = calculate_energy_consumption(task, server)
    transmission_energy = transmission_energy_j(network)
    energy_required = computation_energy + transmission_energy

    remaining_energy = getattr(
        server,
        "remaining_energy_j",
        0.0
    )

    # ------------------------------------------------------
    # Resource cost
    # ------------------------------------------------------

    resource_cost = (
        calculate_resource_cost(
            server,
            task=task,
        )
    )

    # ------------------------------------------------------
    # Normalized components
    # ------------------------------------------------------

    normalized_delay = (
        normalize_delay(
            total_delay,
            task.max_delay
        )
    )

    # A shared (min_energy, max_energy) reference is calculated once from ALL
    # feasible candidates for this task, before running any selection policy.
    # If all candidates use the same energy, energy cannot distinguish them.
    if energy_reference is None:
        raise ValueError(
            "energy_reference=(min_total_energy, max_total_energy) is required "
            "for consistent Min-Max fitness evaluation."
        )
    energy_min, energy_max = energy_reference
    if energy_max > energy_min:
        normalized_energy = (energy_required - energy_min) / (energy_max - energy_min)
        normalized_energy = max(0.0, min(1.0, normalized_energy))
    elif energy_max == energy_min:
        normalized_energy = 0.0
    else:
        raise ValueError("Invalid energy normalization bounds")

    if penalize_infeasible:
        # No-filter baseline: keep every node in the optimizer's search space.
        # Penalize invalid network, deadline, CPU, memory, disk, or energy states.
        required_cpu = calculate_cpu_units_required(task, server)
        available_cpu = getattr(server, "cpu", 0) - getattr(server, "cpu_demand", 0)
        available_memory = getattr(server, "memory", 0) - getattr(server, "memory_demand", 0)
        available_disk = getattr(server, "disk", 0) - getattr(server, "disk_demand", 0)
        feasible = (
            all(math.isfinite(v) for v in (total_delay, energy_required, computation_delay,
                                            communication_delay, transmission_delay))
            and bool(getattr(server, "available", False))
            and bool(getattr(server, "active", True))
            and source_energy_available(task, network)
            and estimated_uncontended_delay(transmission_delay, communication_delay, computation_delay) <= task.max_delay
            and total_delay <= task.max_delay
            and required_cpu > 0 and required_cpu <= available_cpu
            and getattr(task, "memory_required", 0) <= available_memory
            and getattr(task, "disk", 0) <= available_disk
            and computation_energy <= remaining_energy
            and energy_required >= 0
        )
        if not feasible:
            normalized_delay = 1.0
            normalized_energy = 1.0
            resource_cost = 1.0

    normalized_resource = (
        resource_cost
    )

    # ------------------------------------------------------
    # Weighted fitness
    # ------------------------------------------------------

    fitness = (

        W_DELAY
        * normalized_delay

        +

        W_ENERGY
        * normalized_energy

        +

        W_RESOURCE
        * normalized_resource

    )

    if penalize_infeasible and not feasible:
        fitness = 1e6
    elif not all(math.isfinite(value) for value in (
        normalized_delay, normalized_energy, normalized_resource, fitness
    )):
        fitness = float("inf")

    return {

        "server_id":
            server.id,

        "communication_delay":
            communication_delay,

        "transmission_delay":
            transmission_delay,

        "computation_delay":
            computation_delay,

        "total_delay":
            total_delay,

        "energy_required":
            energy_required,
        "computation_energy_j": computation_energy,
        "transmission_energy_j": transmission_energy,
        "total_system_energy_j": energy_required,

        "remaining_energy":
            remaining_energy,

        "resource_cost":
            resource_cost,

        "normalized_delay":
            normalized_delay,

        "normalized_energy":
            normalized_energy,

        "energy_reference":
            energy_reference,

        "normalized_resource":
            normalized_resource,

        "fitness":
            fitness,
    }