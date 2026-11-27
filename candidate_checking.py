from edge_sim_py import EdgeServer

from network_resources import get_network_resources


def check_candidates(task, candidate_nodes, verbose=True):
    """
    Validate the candidate Edge Server objects produced
    by the resource-aware filtering stage.

    This stage does NOT:
        - select the best node
        - run DGWO
        - calculate optimization fitness
        - perform task offloading

    Parameters
    ----------
    task : Task
        Current task.

    candidate_nodes : list
        EdgeServer objects returned by filter_candidates().

    Returns
    -------
    list
        Validated EdgeServer objects.
    """

    validated_candidates = []

    if verbose:
        print()
    if verbose:
        print("=" * 110)
    if verbose:
        print("CANDIDATE CHECKING")
    if verbose:
        print("=" * 110)

    if verbose:
        print(
            f"Task ID           : {task.task_id}"
        )

    if verbose:
        print(
            f"Initial Candidates: "
            f"{[server.id for server in candidate_nodes]}"
        )

    if verbose:
        print("-" * 110)

    # ==========================================================
    # Check each candidate
    # ==========================================================

    for server in candidate_nodes:

        # ------------------------------------------------------
        # 1. Check object type
        # ------------------------------------------------------

        if not isinstance(
            server,
            EdgeServer
        ):

            if verbose:
                print(
                    "Result: REJECTED | "
                    "Candidate is not an EdgeServer object"
                )

            continue

        node_id = server.id

        if verbose:
            print()
        if verbose:
            print(
                f"Checking Edge Node {node_id}"
            )

        # ------------------------------------------------------
        # 2. Check node role
        # ------------------------------------------------------

        role = getattr(
            server,
            "role",
            None
        )

        if role != "edge_node":

            if verbose:
                print(
                    "Result: REJECTED | "
                    f"Invalid role = {role}"
                )

            continue

        # ------------------------------------------------------
        # 3. Check current availability
        # ------------------------------------------------------

        available = bool(
            getattr(
                server,
                "available",
                False
            )
        )

        if not available:

            if verbose:
                print(
                    "Result: REJECTED | "
                    "Node is unavailable"
                )

            continue

        # ------------------------------------------------------
        # 4. Check CPU availability
        # ------------------------------------------------------

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

        available_cpu = (
            cpu - cpu_demand
        )

        if available_cpu <= 0:

            if verbose:
                print(
                    "Result: REJECTED | "
                    "No available CPU resource"
                )

            continue

        # ------------------------------------------------------
        # 5. Check memory availability
        # ------------------------------------------------------

        memory = getattr(
            server,
            "memory",
            0
        )

        memory_demand = getattr(
            server,
            "memory_demand",
            0
        )

        available_memory = (
            memory - memory_demand
        )

        if available_memory < task.memory_required:

            if verbose:
                print(
                    "Result: REJECTED | "
                    "Insufficient memory"
                )

            continue

        # ------------------------------------------------------
        # 6. Check processing capability
        # ------------------------------------------------------

        processing_capability = getattr(
            server,
            "processing_capability",
            None
        )

        if processing_capability is None:

            if verbose:
                print(
                    "Result: REJECTED | "
                    "Processing capability is missing"
                )

            continue

        if processing_capability <= 0:

            if verbose:
                print(
                    "Result: REJECTED | "
                    "Invalid processing capability"
                )

            continue

        # ------------------------------------------------------
        # 7. Re-check network resources
        # ------------------------------------------------------

        try:

            network_result = get_network_resources(
                task,
                server
            )

        except Exception as e:

            if verbose:
                print(
                    "Result: REJECTED | "
                    f"Network check failed: {e}"
                )

            continue

        # ------------------------------------------------------
        # 8. Check network path
        # ------------------------------------------------------

        path = network_result.get(
            "path",
            []
        )

        if not path:

            if verbose:
                print(
                    "Result: REJECTED | "
                    "No network path"
                )

            continue

        # ------------------------------------------------------
        # 9. Check communication delay
        # ------------------------------------------------------

        communication_delay = network_result.get(
            "communication_delay",
            float("inf")
        )

        if communication_delay == float("inf"):

            if verbose:
                print(
                    "Result: REJECTED | "
                    "Infinite communication delay"
                )

            continue

        # ------------------------------------------------------
        # 10. Check transmission delay
        # ------------------------------------------------------

        transmission_delay = network_result.get(
            "transmission_delay",
            float("inf")
        )

        if transmission_delay == float("inf"):

            if verbose:
                print(
                    "Result: REJECTED | "
                    "Transmission unavailable"
                )

            continue

        # ------------------------------------------------------
        # Candidate passed
        # ------------------------------------------------------

        validated_candidates.append(
            server
        )

        cluster_id = getattr(
            server,
            "cluster_id",
            None
        )

        if verbose:
            print(
                f"Cluster               : {cluster_id}"
            )

        if verbose:
            print(
                f"Available CPU         : {available_cpu}"
            )

        if verbose:
            print(
                f"Available Memory      : "
                f"{available_memory} MB"
            )

        if verbose:
            print(
                f"Processing Capability : "
                f"{processing_capability:.0f} cycles/s"
            )

        if verbose:
            print(
                f"Communication Delay   : "
                f"{communication_delay:.3f} s"
            )

        if verbose:
            print(
                f"Transmission Delay    : "
                f"{transmission_delay:.3f} s"
            )

        if verbose:
            print(
                "Result: VALIDATED"
            )

    # ==========================================================
    # Final candidate set
    # ==========================================================

    if verbose:
        print()
    if verbose:
        print("-" * 110)

    if verbose:
        print(
            "Validated Candidate Set: "
            f"{[server.id for server in validated_candidates]}"
        )

    if verbose:
        print(
            "Number of Validated Candidates: "
            f"{len(validated_candidates)}"
        )

    if verbose:
        print("=" * 110)

    return validated_candidates