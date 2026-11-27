"""Validate the final MEC topology datasets and optionally probe EdgeSimPy."""
import argparse
import json
import math
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path
from types import SimpleNamespace

NETWORKS = (20, 50, 100, 200)
ROOT = Path(__file__).resolve().parent


class Checks:
    def __init__(self):
        self.counts = defaultdict(lambda: [0, 0])

    def check(self, group, condition, description):
        self.counts[group][0 if condition else 1] += 1
        print(f"[{'PASS' if condition else 'FAIL'}] {description}")

    def summary(self):
        print("\nCategory | PASS | FAIL")
        for category, (passed, failed) in self.counts.items():
            print(f"{category} | {passed} | {failed}")
        passed = sum(pair[0] for pair in self.counts.values())
        failed = sum(pair[1] for pair in self.counts.values())
        print(f"TOTAL CHECKS = {passed + failed} | PASS = {passed} | FAIL = {failed}")
        return failed


def ref(row, key):
    return row.get("relationships", {}).get(key) or {}


def valid_ref(row, key, kind, identifier):
    return ref(row, key) == {"class": kind, "id": identifier}


def expected_links(n):
    links = {}

    def put(a, b, delay):
        links[tuple(sorted((a, b)))] = delay

    for cluster in range(1, n // 10 + 1):
        peers = list(range(10 * (cluster - 1) + 1, 10 * cluster + 1))
        for peer, next_peer in zip(peers, peers[1:] + peers[:1]):
            put(peer, n + cluster, 0.005)
            put(peer, next_peer, 0.005)
    hubs = list(range(n + 1, n + n // 10 + 1))
    if len(hubs) > 1:
        for first, second in zip(hubs, hubs[1:] + hubs[:1]):
            put(first, second, 0.010)
    return links


def reachable(start, adjacency):
    visited = {start}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for other in adjacency[node] - visited:
            visited.add(other)
            queue.append(other)
    return visited


def validate_dataset(n, path, checks):
    if n == 200:
        import ast
        validator_path = ROOT / "validate_c200_dataset.py"
        tree = ast.parse(validator_path.read_text(encoding="utf-8-sig"))
        class CountAssertions(ast.NodeTransformer):
            def visit_Assert(self, node):
                call = ast.Call(func=ast.Name(id="record_assertion", ctx=ast.Load()), args=[node.test, ast.Constant(node.lineno)], keywords=[])
                return ast.copy_location(ast.Expr(value=call), node)
        tree = CountAssertions().visit(tree)
        ast.fix_missing_locations(tree)
        def record_assertion(condition, line):
            checks.check("JSON structure (C200 assertions)", bool(condition), f"C200 validator assertion at line {line}")
        namespace = {"__name__": "counted_validator", "record_assertion": record_assertion}
        exec(compile(tree, str(validator_path), "exec"), namespace)
        namespace["validate"](json.loads(path.read_text(encoding="utf-8-sig")))
        return

    data = json.loads(path.read_text(encoding="utf-8-sig"))
    total = n + n // 10
    expected = expected_links(n)
    by_type = {}
    for kind, count in (("User", n), ("EdgeServer", total), ("BaseStation", total),
                        ("NetworkSwitch", total), ("NetworkLink", len(expected))):
        rows = data[kind]
        index = {row["attributes"]["id"]: row for row in rows}
        checks.check("Entity counts and IDs", len(rows) == count, f"{kind}: expected {count} records")
        checks.check("Entity counts and IDs", len(index) == len(rows) and set(index) == set(range(1, count + 1)),
                     f"{kind}: unique IDs in the expected range")
        by_type[kind] = index
    stations = by_type["BaseStation"]
    for server in range(1, total + 1):
        row = by_type["EdgeServer"].get(server, {})
        attributes = row.get("attributes", {})
        role = "edge_node" if server <= n else "cluster_server"
        cluster = (server - 1) // 10 + 1 if server <= n else server - n
        checks.check("Server roles and assignments", attributes.get("role") == role, f"Server {server}: role {role}")
        checks.check("Server roles and assignments", attributes.get("cluster_id") == cluster, f"Server {server}: cluster {cluster}")
        checks.check("Server roles and assignments", valid_ref(row, "base_station", "BaseStation", server),
                     f"Server {server}: associated base station")
        checks.check("Server roles and assignments", valid_ref(row, "network_switch", "NetworkSwitch", server),
                     f"Server {server}: associated switch")
        checks.check("Base station and switch mapping", valid_ref(stations.get(server, {}), "network_switch", "NetworkSwitch", server),
                     f"Base station {server}: associated switch")
        checks.check("Base station and switch mapping", valid_ref(by_type["NetworkSwitch"].get(server, {}), "base_station", "BaseStation", server),
                     f"Switch {server}: associated base station")
    for user in range(1, n + 1):
        hub = n + (user - 1) // 10 + 1
        row = by_type["User"].get(user, {})
        checks.check("User access mapping", valid_ref(row, "base_station", "BaseStation", hub),
                     f"User {user}: serving base station {hub}")
        station_id = ref(row, "base_station").get("id")
        checks.check("User access mapping", valid_ref(stations.get(station_id, {}), "network_switch", "NetworkSwitch", hub),
                     f"User {user}: serving switch {hub}")
    actual = set()
    adjacency = defaultdict(set)
    for identifier, row in by_type["NetworkLink"].items():
        ends = ref(row, "nodes")
        nodes = tuple(sorted(x["id"] for x in ends))
        valid = (len(nodes) == 2 and nodes[0] != nodes[1]
                 and all(x.get("class") == "NetworkSwitch" and x["id"] in by_type["NetworkSwitch"] for x in ends))
        checks.check("Link topology", valid and nodes not in actual, f"Link {identifier}: valid and unique endpoints")
        actual.add(nodes)
        if valid and row["attributes"].get("active") is True:
            a, b = nodes
            adjacency[a].add(b)
            adjacency[b].add(a)
        a = row["attributes"]
        checks.check("Link properties", math.isclose(a.get("bandwidth", -1), 12.5), f"Link {identifier}: bandwidth 12.5 Mbps")
        checks.check("Link properties", nodes in expected and math.isclose(a.get("delay", -1), expected[nodes]),
                     f"Link {identifier}: expected intra-cluster or inter-cluster delay")
        checks.check("Link properties", a.get("active") is True, f"Link {identifier}: active")
    checks.check("Link topology", actual == set(expected), "Complete link set matches final topology")
    for cluster in range(1, n // 10 + 1):
        hub = n + cluster
        visited = reachable(hub, adjacency)
        for peer in range(1, n + 1):
            checks.check("Path reachability", peer in visited, f"Cluster switch {hub}: reachable Edge Node {peer}")


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("network", choices=("all", "C20", "C50", "C100", "C200"), nargs="?", default="all")
    parser.add_argument("--runtime", action="store_true", help="also run communication probes in EdgeSimPy")
    args = parser.parse_args()
    if args.runtime and args.network == "all":
        # Use a new simulator process per dataset to isolate entity registries.
        results = [subprocess.run([sys.executable, "-u", __file__, f"C{n}", "--runtime"], cwd=ROOT).returncode
                   for n in NETWORKS]
        return int(any(results))
    combined = 0
    for n in NETWORKS if args.network == "all" else (int(args.network[1:]),):
        path = ROOT / f"C{n}.json"
        print(f"\n=== C{n} ===")
        checks = Checks()
        try:
            validate_dataset(n, path, checks)
        except (ValueError, OSError, KeyError, TypeError) as error:
            checks.check("Dataset load and schema", False, str(error))
        if args.runtime and not any(failed for _, failed in checks.counts.values()):
            try:
                probe_runtime(n, path, checks)
            except Exception as error:
                checks.check("EdgeSimPy runtime", False, f"{type(error).__name__}: {error}")
        combined += checks.summary()
    return int(combined > 0)


if __name__ == "__main__":
    sys.exit(main())
