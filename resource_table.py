from edge_sim_py import EdgeServer


def build_resource_table():
    """
    Build the current resource summary for candidate Edge Nodes.

    Cluster Servers are excluded because they act as local
    coordinators rather than candidate execution devices.

    The table includes:
        - CPU capacity
        - Current CPU demand
        - Available CPU
        - CPU utilization
        - Processing capability f_j
        - Available processing capability f_j_avail
        - Processing capability per core
        - Memory capacity and demand
        - Available memory
        - Disk capacity and demand
        - Available disk
        - Initial and remaining energy
        - Availability status

    Model distinction:

        CPU_j:
            Configured CPU resource capacity.

        CPU_j_demand:
            Currently occupied CPU resource.

        CPU_j_avail:
            Currently available CPU resource.

        f_j:
            Node processing capability.

        f_j_avail:
            Processing capability corresponding to the
            currently available CPU resource.

    The distinction between f_j and f_j_avail allows the
    filtering stage to compare the task's required
    processing rate with the currently available
    processing capacity.
    """

    table = []

    # ==========================================================
    # Iterate through all EdgeServer objects
    # ==========================================================

    for server in EdgeServer.all():

        # ------------------------------------------------------
        # Select only candidate Edge Nodes
        # ------------------------------------------------------

        role = getattr(
            server,
            "role",
            None
        )

        if role != "edge_node":
            continue

        # ======================================================
        # Current Resource Demand
        # ======================================================

        cpu_demand = getattr(
            server,
            "cpu_demand",
            0
        )

        memory_demand = getattr(
            server,
            "memory_demand",
            0
        )

        disk_demand = getattr(
            server,
            "disk_demand",
            0
        )

        # ======================================================
        # Available Resources
        # ======================================================

        # Clamp availability at zero if a previous reservation or a
        # validation scenario has saturated/overcommitted a resource.
        # Raw demand remains available in the table for diagnosis.
        available_cpu = max(0, server.cpu - cpu_demand)
        available_memory = max(0, server.memory - memory_demand)
        available_disk = max(0, server.disk - disk_demand)

        # ======================================================
        # CPU Processing Model
        # ======================================================
        #
        # processing_capability_ghz:
        #     Processing frequency/capability assigned to
        #     one CPU resource unit for this Edge Node.
        #
        # processing_capability:
        #     Node-level processing capability f_j.
        #
        # available_processing_capability:
        #     Processing capability corresponding to the
        #     currently available CPU resource.
        #
        # Therefore:
        #
        #     f_j       = CPU_j × frequency_per_unit
        #
        #     f_j_avail = CPU_j_avail × frequency_per_unit
        #
        # This allows the filtering stage to distinguish
        # between total node capability and currently
        # available capability.
        # ======================================================

        processing_capability_ghz = getattr(
            server,
            "processing_capability_ghz",
            None
        )

        processing_capability = getattr(
            server,
            "processing_capability",
            None
        )

        # ------------------------------------------------------
        # Validate dataset processing capability
        # ------------------------------------------------------

        if processing_capability_ghz is None:

            raise ValueError(
                f"Edge Node {server.id} is missing "
                f"'processing_capability_ghz'."
            )

        if processing_capability is None:

            raise ValueError(
                f"Edge Node {server.id} is missing "
                f"'processing_capability'."
            )

        # ------------------------------------------------------
        # Convert GHz to cycles/s
        # ------------------------------------------------------

        processing_rate_per_cpu_unit = (
            processing_capability_ghz
            * 1_000_000_000
        )

        # ------------------------------------------------------
        # Calculate currently available processing capacity
        #
        #     f_j_avail =
        #         CPU_j_avail × processing rate
        # ------------------------------------------------------

        available_processing_capability = (
            available_cpu
            * processing_rate_per_cpu_unit
        )

        # ======================================================
        # Resource Utilization
        # ======================================================

        if server.cpu > 0:

            cpu_utilization = (
                cpu_demand
                / server.cpu
            )

        else:

            cpu_utilization = 0.0

        if server.memory > 0:

            memory_utilization = (
                memory_demand
                / server.memory
            )

        else:

            memory_utilization = 0.0

        if server.disk > 0:

            disk_utilization = (
                disk_demand
                / server.disk
            )

        else:

            disk_utilization = 0.0

        # ======================================================
        # Energy State
        # ======================================================

        initial_energy = getattr(
            server,
            "initial_energy_j",
            0.0
        )

        remaining_energy = getattr(
            server,
            "remaining_energy_j",
            initial_energy
        )

        # ======================================================
        # Availability
        # ======================================================

        available = bool(
            getattr(
                server,
                "available",
                False
            )
        )

        # ======================================================
        # Store Resource Information
        # ======================================================

        table.append({

            # --------------------------------------------------
            # Identification
            # --------------------------------------------------

            "server_id":
                server.id,

            "role":
                role,

            "cluster_id":
                getattr(
                    server,
                    "cluster_id",
                    None
                ),

            # --------------------------------------------------
            # CPU Resource State
            # --------------------------------------------------

            "cpu":
                server.cpu,

            "cpu_demand":
                cpu_demand,

            "available_cpu":
                available_cpu,

            "cpu_utilization":
                cpu_utilization,

            # --------------------------------------------------
            # Processing Capability
            # --------------------------------------------------

            # Capability per CPU unit/core.
            "processing_capability_ghz":
                processing_capability_ghz,

            # Capability per CPU unit in cycles/s.
            "processing_rate_per_cpu_unit":
                processing_rate_per_cpu_unit,

            # Total node capability f_j.
            "processing_capability":
                processing_capability,

            # Currently available capability f_j_avail.
            "available_processing_capability":
                available_processing_capability,

            # --------------------------------------------------
            # Memory
            # --------------------------------------------------

            "memory":
                server.memory,

            "memory_demand":
                memory_demand,

            "available_memory":
                available_memory,

            "memory_utilization":
                memory_utilization,

            # --------------------------------------------------
            # Disk
            # --------------------------------------------------

            "disk":
                server.disk,

            "disk_demand":
                disk_demand,

            "available_disk":
                available_disk,

            "disk_utilization":
                disk_utilization,

            # --------------------------------------------------
            # Energy
            # --------------------------------------------------

            "initial_energy_j":
                initial_energy,

            "remaining_energy_j":
                remaining_energy,

            # --------------------------------------------------
            # Availability
            # --------------------------------------------------

            "available":
                available
        })

    return table