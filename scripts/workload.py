"""One workload for all servers; only the endpoint varies."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import time

from scripts.client import Client

OPERATIONS = (
    "Create",
    "Update",
    "Delete",
    "Each All",
    "Each Identity",
    "Each Concrete",
    "Each Outgoing",
    "Each Incoming",
)
BACKENDS = ("deep", "rust", "csharp")
FIELDS = "id from_id to_id"
INSERT = f"mutation($objects: [links_insert_input!]!) {{ insert_links(objects: $objects) {{ affected_rows returning {{ {FIELDS} }} }} }}"
DELETE = f"mutation($where: links_bool_exp!) {{ delete_links(where: $where) {{ affected_rows returning {{ {FIELDS} }} }} }}"
UPDATE = f"mutation($where: links_bool_exp!, $set: links_set_input!) {{ update_links(where: $where, _set: $set) {{ affected_rows returning {{ {FIELDS} }} }} }}"
QUERY = f"query($where: links_bool_exp) {{ links(where: $where, order_by: {{id: asc}}) {{ {FIELDS} }} }}"


@dataclass(frozen=True)
class Settings:
    links: int = 100
    background: int = 1000
    samples: int = 10
    warmup: int = 2

    @classmethod
    def from_env(cls):
        values = [
            int(os.environ.get(name, default))
            for name, default in [
                ("BENCHMARK_LINKS", 100),
                ("BENCHMARK_BACKGROUND_LINKS", 1000),
                ("BENCHMARK_SAMPLES", 10),
                ("BENCHMARK_WARMUP", 2),
            ]
        ]
        n, b, samples, warmup = values
        if not (
            1 <= n <= b <= 1_000_000
            and b + n < 1_040_384
            and 2 <= samples <= 1000
            and 0 <= warmup <= 100
        ):
            raise ValueError(
                "Require 1 <= N <= B <= 1000000, B+N < 1040384, 2..1000 samples, 0..100 warmups"
            )
        return cls(*values)


@dataclass
class Scenario:
    query: str
    variables: dict
    prepare: object = None
    undo: object = None


def objects(start, count):
    # Distinct pairs, so the Doublets pair uniqueness rule does not change the work.
    return [{"from_id": i, "to_id": i + 1} for i in range(start, start + count)]


def scenario(operation, n, ids):
    where = {"id": {"_in": ids[:n]}}
    if operation == "Create":

        def undo(client, data):
            created = data["insert_links"]["returning"]
            client.execute(
                DELETE,
                {"where": {"id": {"_in": [int(link["id"]) for link in created]}}},
            )

        return Scenario(
            INSERT,
            {"objects": [{"from_id": i, "to_id": i} for i in range(1, n + 1)]},
            undo=undo,
        )
    if operation == "Update":

        def undo(client, data):
            for link in data["update_links"]["returning"]:
                # Seeding assigns pair (i, i+1) to the i-th background link.
                index = ids.index(int(link["id"])) + 1
                client.execute(
                    UPDATE,
                    {
                        "where": {"id": {"_eq": int(link["id"])}},
                        "set": {"from_id": index, "to_id": index + 1},
                    },
                )

        return Scenario(
            UPDATE,
            {"where": where, "set": {"to_id": 1}},
            prepare=lambda c: None,
            undo=undo,
        )
    if operation == "Delete":
        # Fresh temporary links each iteration keep the background intact; allocator ids may differ.
        current = {}

        def prepare(client):
            inserted = client.execute(
                INSERT,
                {"objects": [{"from_id": i, "to_id": i} for i in range(1, n + 1)]},
            )["insert_links"]["returning"]
            current.update(
                where={"id": {"_in": [int(link["id"]) for link in inserted]}}
            )

        return Scenario(DELETE, current, prepare=prepare, undo=lambda c, d: None)
    filters = {
        "Each All": {},
        "Each Identity": {"id": {"_eq": ids[0]}},
        "Each Concrete": {"from_id": {"_eq": 1}, "to_id": {"_eq": 2}},
        "Each Outgoing": {"from_id": {"_eq": 1}},
        "Each Incoming": {"to_id": {"_eq": 2}},
    }
    if operation not in filters:
        raise ValueError(f"Unknown operation {operation}")
    # Named operation makes documents distinct, including outgoing and concrete variants.
    query = QUERY.replace("query(", f"query {operation.replace(' ', '')}(")
    return Scenario(query, {"where": filters[operation]})


def seed(client, settings):
    # Refuse to destroy arbitrary data on a supplied endpoint.
    if client.execute("query { links(limit: 1) { id } }")["links"]:
        raise ValueError(
            "Benchmark endpoint must be empty; start a fresh dedicated server/database"
        )
    ids = []
    for start in range(1, settings.background + 1, 1000):
        batch = objects(start, min(1000, settings.background - start + 1))
        data = client.execute(INSERT, {"objects": batch})["insert_links"]
        if data["affected_rows"] != len(batch):
            raise ValueError("Seeding did not create every link")
        ids.extend(int(link["id"]) for link in data["returning"])
    return ids


def verify_result(operation, data, settings, ids, variables):
    """Validate outside timing so successful no-ops cannot become benchmark results."""
    if operation in OPERATIONS[:3]:
        field = {
            "Create": "insert_links",
            "Update": "update_links",
            "Delete": "delete_links",
        }[operation]
        response = data[field]
        rows = response["returning"]
        if response["affected_rows"] != settings.links or len(rows) != settings.links:
            raise ValueError(f"{operation} did not affect N links")
        actual = sorted(
            (int(row["id"]), int(row["from_id"]), int(row["to_id"])) for row in rows
        )
        if operation == "Update":
            expected = sorted(
                (identity, index + 1, 1)
                for index, identity in enumerate(ids[: settings.links])
            )
            valid = actual == expected
        else:
            valid = len({row[0] for row in actual}) == settings.links and sorted(
                row[1:] for row in actual
            ) == [(i, i) for i in range(1, settings.links + 1)]
            if operation == "Delete":
                valid = valid and {row[0] for row in actual} == set(
                    variables["where"]["id"]["_in"]
                )
        if not valid:
            raise ValueError(f"{operation} returned incorrect links")
    else:
        rows = data["links"]
        count = settings.background if operation == "Each All" else 1
        if len(rows) != count:
            raise ValueError(f"{operation} returned an incorrect row count")
        for index, row in enumerate(rows):
            if (int(row["id"]), int(row["from_id"]), int(row["to_id"])) != (
                ids[index],
                index + 1,
                index + 2,
            ):
                raise ValueError(f"{operation} returned incorrect links")


def measure(client, operation, settings, ids):
    case = scenario(operation, settings.links, ids)
    samples = []
    for index in range(settings.warmup + settings.samples):
        if case.prepare:
            case.prepare(client)
        start = time.perf_counter_ns()
        data = client.execute(case.query, case.variables)
        elapsed = time.perf_counter_ns() - start
        verify_result(operation, data, settings, ids, case.variables)
        if case.undo:
            case.undo(client, data)
        if index >= settings.warmup:
            samples.append(elapsed)
    return {
        "median_ns": statistics.median(samples),
        "stdev_ns": statistics.stdev(samples),
        "samples_ns": samples,
    }


def metadata(backend):
    import tomllib

    root = Path(__file__).resolve().parents[1]
    lock = tomllib.loads((root / "rust/Cargo.lock").read_text())
    versions = {
        p["name"]: p["version"]
        for p in lock["package"]
        if p["name"] in ("doublets", "async-graphql", "axum")
    }
    import xml.etree.ElementTree as ET

    # Pinned source manifest in this checkout; no external XML is accepted.
    project = ET.parse(root / "csharp/Doublets.Gql.csproj")  # nosec B314
    versions.update(
        {
            p.attrib["Include"]: p.attrib["Version"]
            for p in project.iter("PackageReference")
        }
    )
    versions["postgres"] = "16.13-alpine"
    versions["hasura"] = "v2.48.13"
    versions["deep-core-schema"] = "381207dda313ab3c1fed0a4bfc2ddbe631750411"
    versions["rust"] = (
        (root / "rust-toolchain.toml").read_text().split('channel = "')[1].split('"')[0]
    )
    versions["dotnet-sdk"] = json.loads((root / "global.json").read_text())["sdk"][
        "version"
    ]
    versions["dotnet-runtime"] = project.findtext(".//RuntimeFrameworkVersion")
    if backend == "deep":
        import re

        versions.update(
            {
                "image-" + image.split(":", 1)[0]: image
                for image in re.findall(
                    r"image: (\S+)", (root / "compose.yml").read_text()
                )
            }
        )
    selected = {
        "deep": {"postgres", "hasura", "deep-core-schema"},
        "rust": {"doublets", "async-graphql", "axum", "rust"},
        "csharp": {
            "Platform.Data.Doublets",
            "GraphQL",
            "GraphQL.SystemTextJson",
            "dotnet-sdk",
            "dotnet-runtime",
        },
    }
    versions = {
        key: value
        for key, value in versions.items()
        if key in selected[backend] or key.startswith("image-")
    }
    versions["python"] = platform.python_version()
    cpu = platform.processor() or platform.machine()
    if Path("/proc/cpuinfo").exists():
        cpu = next(
            (
                line.split(":", 1)[1].strip()
                for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")
            ),
            cpu,
        )
    repo = os.environ.get(
        "GITHUB_REPOSITORY", "linksplatform/Comparisons.DeepGqlStandard"
    )
    run_id = os.environ.get("GITHUB_RUN_ID")
    return {
        "cpu": cpu,
        "date": datetime.now(timezone.utc).isoformat(),
        "run": f"https://github.com/{repo}/actions/runs/{run_id}"
        if run_id
        else "local",
        "commit": os.environ.get("GITHUB_SHA")
        or subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "versions": versions,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("backend", choices=BACKENDS)
    parser.add_argument("url")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    settings = Settings.from_env()
    client = Client(args.url)
    try:
        ids = seed(client, settings)
        operations = {}
        for operation in OPERATIONS:
            operations[operation] = measure(client, operation, settings, ids)
            print(
                f"{args.backend} B={settings.background} {operation}: {operations[operation]['median_ns'] / 1e6:.3f} ms",
                flush=True,
            )
        # Ensure all mutation undo steps preserved the original dataset.
        expected = [
            {"id": identity, **pair}
            for identity, pair in zip(ids, objects(1, settings.background))
        ]
        actual = client.execute(QUERY, {"where": {}})["links"]
        actual = [{key: int(value) for key, value in link.items()} for link in actual]
        if actual != expected:
            raise ValueError("Benchmark mutations changed the background dataset")
        record = {
            "backend": args.backend,
            "background_links": settings.background,
            "links": settings.links,
            "samples": settings.samples,
            "warmup": settings.warmup,
            "metadata": metadata(args.backend),
            "operations": operations,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")
    finally:
        client.close()


if __name__ == "__main__":
    main()
