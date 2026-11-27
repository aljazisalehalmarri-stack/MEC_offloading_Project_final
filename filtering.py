from edge_sim_py import EdgeServer
from timing_model import estimated_uncontended_delay

from resource_table import build_resource_table
from network_resources import get_network_resources
from energy_model import source_energy_available
from offloading import (
    calculate_cpu_units_required,
    get_processing_rate_per_unit,
    calculate_reserved_energy,
)


# ==========================================================
# PROJECT / SIMULATION PARAMETERS
# ==========================================================

# ==========================================================
# CPU ENERGY COEFFICIENT
# ==========================================================
#
# Adopted from the selected MEC energy model used in
# the project.
#
#     E_comp = kappa × W_i × f_j^2
#
# where:
#
#     kappa = energy coefficient
#     W_i   = task workload in CPU cycles
#     f_j   = node processing capability in cycles/s
#
# This is a model/simulation parameter.
# ==========================================================

KAPPA = 1e-28


# ==========================================================
# Helper: Get Candidate Resource Row
# ==========================================================

def get_resource_row(
    resource_table,
    server_id
):
    """
    Find the resource-table row corresponding to a server.
    """

    for row in resource_table:

        if row["server_id"] == server_id:
            return row

    return None


# ==========================================================
# Helper: Required Processing Rate
# ==========================================================

def calculate_required_processing_rate(task):
    """
    Calculate the minimum processing rate required by
    the task to complete its workload within its deadline.

        F_i_req = W_i / D_i

    where:

        W_i = task workload in CPU cycles
        D_i = maximum tolerable delay in seconds

    Returns:
        float:
            Required processing rate in cycles/s.
    """

    if task.max_delay <= 0:

        return float("inf")

    return (
        task.workload
        / task.max_delay
    )


# ==========================================================
# Helper: Required Communication Rate
# ==========================================================

def calculate_required_rate(task):
    """
    Calculate the conservative communication-rate
    threshold used during preliminary filtering.

        R_th = (S_i × 8) / D_i

    where:

        S_i = input data size in MB
        D_i = maximum tolerable task delay in seconds

    Returns:
        float:
            Required communication rate in Mbps.
    """

    if task.max_delay <= 0:

        return float("inf")

    data_size_megabits = (
        task.data_size * 8
    )

    return (
        data_size_megabits
        / task.max_delay
    )


# ==========================================================
# Helper: Calculate Computation Delay
# ==========================================================

def calculate_computation_delay(
    task,
    processing_capability
):
    """
    Calculate task computation delay.

        T_comp = W_i / f_j

    where:

        W_i = task workload in CPU cycles
        f_j = processing capability of the candidate node

    Returns:
        float:
            Computation delay in seconds.
    """

    if processing_capability <= 0:

        return float("inf")

    return (
        task.workload
        / processing_capability
    )


# ==========================================================
# Helper: Calculate Computation Energy
# ==========================================================

def calculate_computation_energy(
    task,
    processing_capability
):
    """
    Estimate computation energy using the selected
    CPU energy model.

        E_comp = kappa × W_i × f_j^2

    Returns:
        float:
            Estimated computation energy.
    """

    if processing_capability <= 0:

        return float("inf")

    return (
        KAPPA
        * task.workload
        * (processing_capability ** 2)
    )


# ==========================================================
# Main Filtering Function
# ==========================================================

def filter_candidates(task, cluster_id=None, verbose=True):
    """
    Apply resource-aware filtering to an incoming task.

    If cluster_id is provided, only Edge Nodes belonging to that
    cluster are considered. This supports the project's Local stage.
    If cluster_id is None, all Edge Nodes are considered (Global stage).

    Candidate Edge Nodes are filtered before DGWO.

    Hard / preliminary checks include:

        1. Device availability
        2. Available CPU processing capacity
        3. Node processing capability
        4. Available memory
        5. Communication-rate pre-filter
        6. Communication delay
        7. Transmission delay
        8. Computation delay
        9. Total execution deadline
       10. Remaining energy

    CPU feasibility is evaluated by comparing:

        Required processing rate:
            F_i_req = W_i / D_i

    with:

        Currently available processing capability:
            F_j_avail

    The resulting feasible nodes form candidate set P_i.

    Returns:
        list:
            Feasible EdgeServer objects.
    """

    # ======================================================
    # Build current resource table
    # ======================================================

    resource_table = (
        build_resource_table()
    )

    # ======================================================
    # Candidate containers
    # ======================================================

    candidate_nodes = []

    filtering_results = []

    # ======================================================
    # Task-level thresholds
    # ======================================================

    required_processing_rate = (
        calculate_required_processing_rate(
            task
        )
    )

    required_communication_rate = (
        calculate_required_rate(
            task
        )
    )

    # ======================================================
    # Display task information
    # ======================================================

    if verbose:
        print()
    if verbose:
        print("=" * 120)
    if verbose:
        print("RESOURCE-AWARE FILTERING")
    if verbose:
        print("=" * 120)

    if verbose:
        print(
            f"Task ID                         : "
            f"{task.task_id}"
        )

    if verbose:
        print(
            f"Workload                        : "
            f"{task.workload} cycles"
        )

    if verbose:
        print(
            f"Required Memory                 : "
            f"{task.memory_required} MB"
        )

    if verbose:
        print(
            f"Input Data Size                 : "
            f"{task.data_size} MB"
        )

    if verbose:
        print(
            f"Deadline                        : "
            f"{task.max_delay} s"
        )

    if verbose:
        print(
            f"Required Processing Rate F_req  : "
            f"{required_processing_rate:.2f} cycles/s"
        )

    if verbose:
        print(
            f"Required Communication Rate R_th: "
            f"{required_communication_rate:.4f} Mbps"
        )

    if verbose:
        print(
            f"Energy Coefficient kappa        : "
            f"{KAPPA:.2e}"
        )

    # ======================================================
    # Filter every Edge Node
    # ======================================================

    for server in EdgeServer.all():

        # --------------------------------------------------
        # Only Edge Nodes are candidate execution devices
        # --------------------------------------------------

        role = getattr(
            server,
            "role",
            None
        )

        if role != "edge_node":
            continue

        # --------------------------------------------------
        # Local / Global cluster scope
        # --------------------------------------------------
        # Local stage: only nodes managed by the source user's
        # Cluster Server are considered. Global stage: pass None
        # and all Edge Nodes are considered by the caller.
        server_cluster_id = getattr(server, "cluster_id", None)

        if cluster_id is not None and server_cluster_id != cluster_id:
            continue

        # --------------------------------------------------
        # Resource-table information
        # --------------------------------------------------

        resource_row = get_resource_row(
            resource_table,
            server.id
        )

        if resource_row is None:

            filtering_results.append({

                "server_id":
                    server.id,

                "feasible":
                    False,

                "reason":
                    "Resource information not found."
            })

            continue

        # ==================================================
        # 1. Device Availability
        # ==================================================

        available = bool(
            resource_row["available"]
        )

        # ==================================================
        # 2. CPU Resource Availability
        # ==================================================
        #
        # CPU resource and computational capability
        # remain separate concepts.
        #
        # CPU_avail:
        #     currently unused CPU resource.
        #
        # f_j:
        #     node processing capability.
        #
        # f_j_avail:
        #     processing capability associated with
        #     currently available CPU resource.
        # ==================================================

        available_cpu = resource_row["available_cpu"]
        available_processing_capability = resource_row["available_processing_capability"]

        rate_per_cpu_unit = get_processing_rate_per_unit(server)
        required_cpu_units = calculate_cpu_units_required(task, server)
        allocated_processing_capability = required_cpu_units * rate_per_cpu_unit

        # Match the integer CPU reservation that offloading will commit.
        cpu_resource_available = available_cpu >= required_cpu_units

        # ==================================================
        # 3. CPU Processing Requirement
        # ==================================================
        #
        # Compare the task's minimum required processing
        # rate with the currently available processing
        # capability of the node.
        #
        #     F_j_avail >= F_i_req
        # ==================================================

        cpu_capacity_feasible = (
            available_processing_capability
            >= required_processing_rate
        )

        # ==================================================
        # 4. Node Processing Capability
        # ==================================================

        processing_capability = (
            resource_row[
                "processing_capability"
            ]
        )

        processing_feasible = (
            processing_capability > 0
        )

        # ==================================================
        # 5. Memory Constraint
        # ==================================================

        available_memory = (
            resource_row[
                "available_memory"
            ]
        )

        memory_feasible = (
            available_memory
            >= task.memory_required
        )

        # ==================================================
        # 6. Network Resources
        # ==================================================

        network_info = (
            get_network_resources(
                task=task,
                server=server
            )
        )

        available_bandwidth = (
            network_info[
                "available_bandwidth"
            ]
        )

        # ==================================================
        # 7. Communication-Rate Pre-filter
        # ==================================================
        #
        #     R_ij >= R_th
        #
        # R_th is only a preliminary conservative
        # communication-rate check.
        # ==================================================

        if available_bandwidth == float(
            "inf"
        ):

            bandwidth_feasible = True

        else:

            bandwidth_feasible = (
                available_bandwidth
                >= required_communication_rate
            )

        # ==================================================
        # 8. Computation Delay
        # ==================================================
        #
        # During filtering, the required integer CPU units
        # are calculated using the same allocation rule
        # applied during offloading.
        #
        # F_ij_alloc = CPU_ij_alloc * f_j
        #
        # T_comp = W_i / F_ij_alloc
        #
        # This ensures consistency between filtering,
        # fitness evaluation, and resource reservation.
        # ==================================================

        computation_delay = calculate_computation_delay(
            task,
            allocated_processing_capability,
        )

        # ==================================================
        # 9. Communication Delay
        # ==================================================

        communication_delay = (
            network_info[
                "communication_delay"
            ]
        )

        # ==================================================
        # 10. Transmission Delay
        # ==================================================

        transmission_delay = (
            network_info[
                "transmission_delay"
            ]
        )

        # ==================================================
        # 11. Total Execution Delay
        # ==================================================

        total_delay = (
            communication_delay
            + transmission_delay
            + computation_delay
        )

        estimated_tick_delay = estimated_uncontended_delay(
            transmission_delay, communication_delay, computation_delay
        )
        delay_feasible = (
            total_delay <= task.max_delay
            and estimated_tick_delay <= task.max_delay
        )

        # ==================================================
        # 12. Computation Energy
        # ==================================================

        required_energy = calculate_reserved_energy(
            task,
            allocated_processing_capability,
        )

        remaining_energy = (
            resource_row[
                "remaining_energy_j"
            ]
        )

        if remaining_energy == float(
            "inf"
        ):

            energy_feasible = True

        else:

            energy_feasible = (
                remaining_energy
                >= required_energy
            )

        # ==================================================
        # 13. Overall Feasibility
        # ==================================================

        feasible = (

            available

            and cpu_resource_available

            and cpu_capacity_feasible

            and processing_feasible

            and memory_feasible

            and bandwidth_feasible

            and delay_feasible

            and energy_feasible
            and source_energy_available(task, network_info)
        )

        # ==================================================
        # Rejection Reasons
        # ==================================================

        rejection_reasons = []

        if not available:

            rejection_reasons.append(
                "availability"
            )

        if not cpu_resource_available:

            rejection_reasons.append(
                "no available CPU resource"
            )

        if not cpu_capacity_feasible:

            rejection_reasons.append(
                "insufficient available processing capacity"
            )

        if not processing_feasible:

            rejection_reasons.append(
                "processing capability"
            )

        if not memory_feasible:

            rejection_reasons.append(
                "memory"
            )

        if not bandwidth_feasible:

            rejection_reasons.append(
                "communication rate"
            )

        if not delay_feasible:

            rejection_reasons.append(
                "deadline"
            )

        if not energy_feasible:

            rejection_reasons.append(
                "energy"
            )
        if not source_energy_available(task, network_info):
            rejection_reasons.append("source transmission energy")

        # ==================================================
        # Candidate / Rejection Decision
        # ==================================================

        if feasible:

            reason = "PASS"

            candidate_nodes.append(
                server
            )

        else:

            reason = (
                ", ".join(
                    rejection_reasons
                )
                if rejection_reasons
                else "Rejected"
            )

        # ==================================================
        # Store Detailed Result
        # ==================================================

        filtering_results.append({

            "server_id":
                server.id,

            "cluster_id":
                resource_row[
                    "cluster_id"
                ],

            # --------------------------------------------------
            # CPU Resource
            # --------------------------------------------------

            "available_cpu":
                available_cpu,

            "required_cpu_units":
                required_cpu_units,

            "allocated_processing_capability":
                allocated_processing_capability,

            "required_processing_rate":
                required_processing_rate,

            "available_processing_capability":
                available_processing_capability,

            "cpu_resource_available":
                cpu_resource_available,

            "cpu_capacity_feasible":
                cpu_capacity_feasible,

            # --------------------------------------------------
            # Processing Capability
            # --------------------------------------------------

            "processing_capability":
                processing_capability,

            "processing_feasible":
                processing_feasible,

            # --------------------------------------------------
            # Memory
            # --------------------------------------------------

            "available_memory":
                available_memory,

            "required_memory":
                task.memory_required,

            "memory_feasible":
                memory_feasible,

            # --------------------------------------------------
            # Network
            # --------------------------------------------------

            "available_bandwidth":
                available_bandwidth,

            "required_rate":
                required_communication_rate,

            "bandwidth_feasible":
                bandwidth_feasible,

            # --------------------------------------------------
            # Delay
            # --------------------------------------------------

            "communication_delay":
                communication_delay,

            "transmission_delay":
                transmission_delay,

            "computation_delay":
                computation_delay,

            "total_delay":
                total_delay,

            "estimated_tick_delay": estimated_tick_delay,

            "deadline":
                task.max_delay,

            "delay_feasible":
                delay_feasible,

            # --------------------------------------------------
            # Energy
            # --------------------------------------------------

            "required_energy":
                required_energy,

            "remaining_energy":
                remaining_energy,

            "energy_feasible":
                energy_feasible,

            # --------------------------------------------------
            # Availability
            # --------------------------------------------------

            "available":
                available,

            # --------------------------------------------------
            # Overall
            # --------------------------------------------------

            "feasible":
                feasible,

            "reason":
                reason,

            "path":
                network_info[
                    "path"
                ]
        })

    # ======================================================
    # Print Detailed Filtering Results
    # ======================================================

    if verbose:
        print()
    if verbose:
        print("Filtering Results")
    if verbose:
        print("-" * 260)

    for result in filtering_results:

        status = (
            "FEASIBLE"
            if result["feasible"]
            else "REJECTED"
        )

        if verbose:
            print(
    
                f"Node={result['server_id']:2d} | "
    
                f"Cluster={result['cluster_id']} | "
    
                f"CPU_avail="
                f"{result['available_cpu']} | "
    
                f"F_avail="
                f"{result['available_processing_capability']:.0f} | "
    
                f"F_req="
                f"{result['required_processing_rate']:.0f} | "
    
                f"f_j="
                f"{result['processing_capability']:.0f} | "
    
                f"R_avail="
                f"{result['available_bandwidth']} Mbps | "
    
                f"R_th="
                f"{result['required_rate']:.4f} Mbps | "
    
                f"T_comm="
                f"{result['communication_delay']:.3f} s | "
    
                f"T_tx="
                f"{result['transmission_delay']:.3f} s | "
    
                f"T_comp="
                f"{result['computation_delay']:.3f} s | "
    
                f"T_total="
                f"{result['total_delay']:.3f} s | "
    
                f"Deadline="
                f"{result['deadline']} s | "
    
                f"E_req="
                f"{result['required_energy']:.6f} J | "
    
                f"E_rem="
                f"{result['remaining_energy']:.6f} J | "
    
                f"{status} | "
    
                f"{result['reason']}"
            )

    # ======================================================
    # Candidate Set P_i
    # ======================================================

    if verbose:
        print()
    if verbose:
        print("Candidate Set")
    if verbose:
        print("-" * 120)

    candidate_ids = [

        server.id

        for server in candidate_nodes
    ]

    if verbose:
        print(
            f"P_i = {candidate_ids}"
        )

    if verbose:
        print(
            f"Number of Candidate Nodes : "
            f"{len(candidate_nodes)}"
        )

    # ======================================================
    # Overall Status
    # ======================================================

    if candidate_nodes:

        if verbose:
            print(
                "Filtering Status          : "
                "FEASIBLE CANDIDATES FOUND"
            )

    else:

        if verbose:
            print(
                "Filtering Status          : "
                "NO FEASIBLE CANDIDATES"
            )

    if verbose:
        print("-" * 120)

    # ======================================================
    # Return
    # ======================================================

    return candidate_nodes