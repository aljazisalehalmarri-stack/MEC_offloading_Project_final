"""Run eight C200 communication checks inside EdgeSimPy; does not alter project files."""
import json
import math
from pathlib import Path
from types import SimpleNamespace

class Checks:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.records = []
    def check(self, group, condition, description):
        self.passed += int(bool(condition))
        self.failed += int(not condition)
        self.records.append({"description": description, "passed": bool(condition)})
        print(f"{'PASS' if condition else 'FAIL'} | {description}")

def probe_runtime(n, path, checks):
    from edge_sim_py import EdgeServer, Simulator, User
    from network_resources import get_network_resources

    simulator = Simulator()
    simulator.initialize(input_file=str(path))
    checks.check("EdgeSimPy runtime", len(User.all()) == n, "EdgeSimPy loaded expected user count")
    checks.check("EdgeSimPy runtime", len(EdgeServer.all()) == n + n // 10, "EdgeSimPy loaded expected server count")
    task = SimpleNamespace(source_user_id=1, data_size=1)
    for node, delay in ((1, 0.005), (11, 0.015)) if n > 10 else ((1, 0.005),):
        result = get_network_resources(task, EdgeServer.find_by_id(node))
        path_ids = [n + 1, node] if node == 1 else [n + 1, n + 2, node]
        checks.check("EdgeSimPy runtime", result["path"] == path_ids, f"User 1 to Edge Node {node}: switch path")
        checks.check("EdgeSimPy runtime", math.isclose(result["communication_delay"], delay), f"User 1 to Edge Node {node}: link delay")
        checks.check("EdgeSimPy runtime", math.isclose(result["transmission_delay"], 8 / 12.5), f"User 1 to Edge Node {node}: transmission delay")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parent / "C200.json")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "c200_runtime_validation.json")
    args = parser.parse_args()
    checks = Checks()
    probe_runtime(200, args.dataset, checks)
    result = {"configuration": "C200", "runtime_checks": checks.passed + checks.failed,
              "passed": checks.passed, "failed": checks.failed, "records": checks.records}
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"TOTAL={checks.passed + checks.failed} | PASS={checks.passed} | FAIL={checks.failed}")
    raise SystemExit(1 if checks.failed else 0)
