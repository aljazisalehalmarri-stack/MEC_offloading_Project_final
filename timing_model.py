"""Discrete-tick admission estimate; not a guarantee under network contention."""
import math
TICK_SECONDS = 1.0

def estimated_uncontended_delay(transmission_delay, communication_delay, computation_delay, has_network_flow=True):
    vals = (transmission_delay, communication_delay, computation_delay)
    if any(not math.isfinite(float(v)) or float(v) < 0 for v in vals):
        return float("inf")
    transfer_ticks = max(1, math.ceil(float(transmission_delay) / TICK_SECONDS)) if has_network_flow else 0
    processing_ticks = max(1, math.ceil((float(communication_delay) + float(computation_delay)) / TICK_SECONDS))
    return (transfer_ticks + processing_ticks) * TICK_SECONDS
