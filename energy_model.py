"""Source transmission energy, based on estimated bottleneck transfer airtime.

Assumptions: fixed source transmit power, no radio power control, no RX or
intermediate switch energy, no result-return traffic. Same-switch transfer
is treated as zero-airtime under the existing network model.
"""
import math

SOURCE_TX_POWER_W = 0.5  # W; explicit scenario assumption, not measured device data

def transmission_energy_j(network_info, power_w=SOURCE_TX_POWER_W):
    delay = float(network_info["transmission_delay"])
    if not math.isfinite(delay) or delay < 0 or power_w < 0:
        return float("inf")
    return power_w * delay

def total_offload_energy_j(computation_energy_j, network_info):
    return float(computation_energy_j) + transmission_energy_j(network_info)

def source_energy_available(task, network_info):
    from edge_sim_py import User
    user = User.find_by_id(task.source_user_id)
    if user is None:
        return False
    # Same default as the main simulation; source state is updated on commit.
    available = float(getattr(user, "local_remaining_energy_j", 1000.0))
    return available >= transmission_energy_j(network_info)
