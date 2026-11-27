import networkx as nx

from edge_sim_py import (
    User,
    NetworkSwitch,
)


def get_network_resources(task, server):
    """
    Calculate the network resources between the task source
    user and a candidate Edge Node.

    Returns:
        dict:
            server_id
            cluster_id
            path
            available_bandwidth
            communication_delay
            data_rate
            transmission_delay
    """

    # ======================================================
    # 1. Find the task source user
    # ======================================================

    user = User.find_by_id(
        task.source_user_id
    )

    if user is None:
        raise ValueError(
            f"User {task.source_user_id} was not found."
        )

    # ======================================================
    # 2. Get source Base Station and Network Switch
    # ======================================================

    source_base_station = user.base_station

    if source_base_station is None:
        raise ValueError(
            f"User {task.source_user_id} "
            f"has no Base Station."
        )

    source_switch = (
        source_base_station.network_switch
    )

    if source_switch is None:
        raise ValueError(
            f"Base Station {source_base_station.id} "
            f"has no Network Switch."
        )

    # ======================================================
    # 3. Get target Base Station and Network Switch
    # ======================================================

    target_base_station = server.base_station

    if target_base_station is None:
        raise ValueError(
            f"Edge Node {server.id} "
            f"has no Base Station."
        )

    target_switch = (
        target_base_station.network_switch
    )

    if target_switch is None:
        raise ValueError(
            f"Base Station {target_base_station.id} "
            f"has no Network Switch."
        )

    # ======================================================
    # 4. Verify Network Switch objects explicitly
    # ======================================================

    source_switch_id = source_switch.id
    target_switch_id = target_switch.id

    source_switch_explicit = NetworkSwitch.find_by_id(
        source_switch_id
    )

    target_switch_explicit = NetworkSwitch.find_by_id(
        target_switch_id
    )

    if source_switch_explicit is None:
        raise ValueError(
            f"NetworkSwitch {source_switch_id} "
            f"was not found."
        )

    if target_switch_explicit is None:
        raise ValueError(
            f"NetworkSwitch {target_switch_id} "
            f"was not found."
        )

    # ======================================================
    # 5. Get network topology
    # ======================================================

    topology = user.model.topology

    if topology is None:
        raise ValueError(
            "Network topology is not available."
        )

    # ======================================================
    # 6. Find shortest path
    # ======================================================

    if source_switch == target_switch:

        path = [
            source_switch
        ]

    else:

        try:

            path = nx.shortest_path(
                G=topology,
                source=source_switch,
                target=target_switch,
                weight="delay",
                method="dijkstra",
            )

        except (nx.NetworkXNoPath, nx.NodeNotFound):

            return {

                "server_id":
                    server.id,

                "cluster_id":
                    getattr(
                        server,
                        "cluster_id",
                        None
                    ),

                "path":
                    [],

                "available_bandwidth":
                    0.0,

                "communication_delay":
                    float("inf"),

                "data_rate":
                    0.0,

                "transmission_delay":
                    float("inf"),
            }

    # ======================================================
    # 7. Calculate communication delay
    # ======================================================
    #
    # Sum the delay of all links in the selected path.
    #
    #     T_comm = sum(D_l)
    #
    # ======================================================

    communication_delay = 0.0

    for i in range(
        len(path) - 1
    ):

        node_1 = path[i]
        node_2 = path[i + 1]

        link = topology[
            node_1
        ][
            node_2
        ]

        communication_delay += (
            link["delay"]
        )

    # ======================================================
    # 8. Calculate available bandwidth
    # ======================================================
    #
    # The effective path bandwidth is the bottleneck
    # available bandwidth among all links in the path.
    #
    # ======================================================

    if len(path) <= 1:

        available_bandwidth = float(
            "inf"
        )

    else:

        path_bandwidths = []

        for i in range(
            len(path) - 1
        ):

            node_1 = path[i]
            node_2 = path[i + 1]

            link = topology[
                node_1
            ][
                node_2
            ]

            # Remaining bandwidth must not be negative.
            link_available_bandwidth = max(
                0.0,
                link["bandwidth"] - link["bandwidth_demand"]
            )

            path_bandwidths.append(
                link_available_bandwidth
            )

        if path_bandwidths:

            available_bandwidth = min(
                path_bandwidths
            )

        else:

            available_bandwidth = 0.0

    # ======================================================
    # 9. Effective communication rate R_ij
    # ======================================================

    data_rate = (
        available_bandwidth
    )

    # ======================================================
    # 10. Calculate transmission delay
    # ======================================================
    #
    # Task data size is given in MB.
    # Bandwidth is given in Mbps.
    #
    # Therefore:
    #
    #     MB × 8 = Mb
    #
    # and:
    #
    #     T_tx = data size (Mb) / R_ij
    #
    # ======================================================

    if data_rate == float(
        "inf"
    ):

        transmission_delay = 0.0

    elif data_rate <= 0:

        transmission_delay = float(
            "inf"
        )

    else:

        data_size_megabits = (
            task.data_size * 8
        )

        transmission_delay = (
            data_size_megabits
            / data_rate
        )

    # ======================================================
    # 11. Return network information
    # ======================================================

    return {

        "server_id":
            server.id,

        "cluster_id":
            getattr(
                server,
                "cluster_id",
                None
            ),

        "path":
            [
                node.id
                for node in path
            ],

        "available_bandwidth":
            available_bandwidth,

        "communication_delay":
            communication_delay,

        "data_rate":
            data_rate,

        "transmission_delay":
            transmission_delay,
    }