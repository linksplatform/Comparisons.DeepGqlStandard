"""Validated comparison tables and linear/log charts, following sibling repositories."""

import argparse
import json
import math
from pathlib import Path
import statistics

from scripts.workload import BACKENDS, OPERATIONS

START = "<!-- results:start -->"
END = "<!-- results:end -->"


def ratio(baseline, value, baseline_sd, sd):
    if (
        abs(baseline - value) / max(baseline, value) < 0.05
        or abs(baseline - value) <= baseline_sd + sd
    ):
        return "≈ same"
    return (
        f"{baseline / value:.3g}× faster"
        if value < baseline
        else f"{value / baseline:.3g}× slower"
    )


def validate(records):
    if not records:
        raise ValueError("No result files")
    seen = set()
    groups = {}
    for record in records:
        backend, size = record.get("backend"), record.get("background_links")
        if (
            backend not in BACKENDS
            or not isinstance(size, int)
            or not 1 <= size <= 1_000_000
        ):
            raise ValueError("Unknown backend or invalid size")
        key = (size, backend)
        if key in seen:
            raise ValueError(f"Duplicate result {key}")
        seen.add(key)
        parameters = tuple(record.get(k) for k in ["links", "samples", "warmup"])
        n, samples, warmup = parameters
        if not all(isinstance(v, int) for v in parameters) or not (
            1 <= n <= size and 2 <= samples <= 1000 and 0 <= warmup <= 100
        ):
            raise ValueError("Invalid measurement settings")
        if size in groups and groups[size] != parameters:
            raise ValueError(f"Different workload settings for size {size}")
        groups[size] = parameters
        metadata = record.get("metadata", {})
        if any(
            not metadata.get(k) for k in ["cpu", "date", "run", "commit", "versions"]
        ):
            raise ValueError("Incomplete provenance")
        if not isinstance(metadata["versions"], dict) or not all(
            isinstance(v, str) for v in metadata["versions"].values()
        ):
            raise ValueError("Invalid versions")
        operations = record.get("operations", {})
        if set(operations) != set(OPERATIONS):
            raise ValueError(f"Missing or unknown operations for {key}")
        for name, data in operations.items():
            values = data.get("samples_ns", [])
            median, sd = data.get("median_ns"), data.get("stdev_ns")
            if len(values) != samples or any(
                not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
                for v in values
            ):
                raise ValueError(f"Invalid samples for {key} {name}")
            if (
                not isinstance(median, (int, float))
                or not math.isfinite(median)
                or median <= 0
                or not isinstance(sd, (int, float))
                or not math.isfinite(sd)
                or sd < 0
            ):
                raise ValueError(f"Invalid statistics for {key} {name}")
            if not math.isclose(median, statistics.median(values)) or not math.isclose(
                sd, statistics.stdev(values), abs_tol=1e-6
            ):
                raise ValueError(f"Statistics do not match samples for {key} {name}")
    for size in groups:
        if any((size, backend) not in seen for backend in BACKENDS):
            raise ValueError(f"Missing backend for size {size}")
    return groups


def load_results(directory):
    records = [
        json.loads(p.read_text()) for p in sorted(Path(directory).rglob("*.json"))
    ]
    validate(records)
    return records


def duration(ns):
    if ns < 1000:
        return f"{ns:.3g} ns"
    if ns < 1_000_000:
        return f"{ns / 1000:.3g} µs"
    if ns < 1_000_000_000:
        return f"{ns / 1_000_000:.3g} ms"
    return f"{ns / 1_000_000_000:.3g} s"


def render(records, charts="Docs"):
    groups = validate(records)
    text = [
        "Times are medians per HTTP request (including client encoding/decoding, network, GraphQL and storage); ± values are sample standard deviations. Ratios compare each Doublets server with Deep. Differences below 5% or overlapping ±1 SD ranges are shown as ≈ same.",
        "",
    ]
    for size, (n, samples, warmup) in sorted(groups.items()):
        by_backend = {r["backend"]: r for r in records if r["background_links"] == size}
        text += [
            f"### B = {size:,}, N = {n:,}",
            "",
            f"{samples} samples after {warmup} warm-up requests per operation. Create/update/delete act on N links; Each All returns B links; the other queries return one link.",
            "",
        ]
        for backend in BACKENDS:
            m = by_backend[backend]["metadata"]
            versions = ", ".join(
                f"{key} {value}" for key, value in sorted(m["versions"].items())
            )
            run = f"[run]({m['run']})" if m["run"] != "local" else "local run"
            text.append(
                f"- **{backend}**: {versions}; CPU: {m['cpu']}; {m['date']}; {run}; commit `{m['commit']}`."
            )
        text += [
            "",
            "| Operation | Deep (PostgreSQL + Hasura) | Doublets Rust | Doublets C# |",
            "|---|---:|---:|---:|",
        ]
        for operation in OPERATIONS:
            baseline = by_backend["deep"]["operations"][operation]
            cells = []
            for backend in BACKENDS:
                data = by_backend[backend]["operations"][operation]
                cell = f"{duration(data['median_ns'])} ± {duration(data['stdev_ns'])}"
                if backend != "deep":
                    cell += f" (**{ratio(baseline['median_ns'], data['median_ns'], baseline['stdev_ns'], data['stdev_ns'])}**)"
                cells.append(cell)
            text.append(f"| {operation} | " + " | ".join(cells) + " |")
        text += [
            "",
            f"![Linear chart B={size}]({charts}/bench_{size}.png)",
            "",
            f"![Log chart B={size}]({charts}/bench_log_{size}.png)",
            "",
        ]
    return "\n".join(text).strip()


def replace_section(readme, section):
    if (
        readme.count(START) != 1
        or readme.count(END) != 1
        or readme.index(START) >= readme.index(END)
    ):
        raise ValueError("README must have exactly one ordered pair of result markers")
    before, rest = readme.split(START)
    _, after = rest.split(END)
    return before + START + "\n" + section + "\n" + END + after


def plot(records, directory):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    groups = validate(records)
    directory.mkdir(parents=True, exist_ok=True)
    for size, (n, _, _) in sorted(groups.items()):
        by_backend = {r["backend"]: r for r in records if r["background_links"] == size}
        for log in (False, True):
            fig, ax = plt.subplots(figsize=(12, 5))
            for index, backend in enumerate(BACKENDS):
                values = [
                    by_backend[backend]["operations"][op]["median_ns"] / 1e6
                    for op in OPERATIONS
                ]
                ax.bar(
                    [i + (index - 1) * 0.26 for i in range(len(OPERATIONS))],
                    values,
                    width=0.26,
                    label=backend,
                )
            ax.set_xticks(range(len(OPERATIONS)), OPERATIONS, rotation=20, ha="right")
            ax.set_ylabel("Median HTTP request time (ms)")
            ax.set_title(f"GraphQL comparison · B={size:,}, N={n:,}")
            if log:
                ax.set_yscale("log")
            else:
                ax.set_ylim(bottom=0)
            ax.legend()
            fig.tight_layout()
            fig.savefig(directory / f"bench_{'log_' if log else ''}{size}.png", dpi=150)
            plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--charts", type=Path, default=Path("Docs"))
    args = parser.parse_args()
    records = load_results(args.results)
    section = render(records, args.charts.as_posix())
    updated = replace_section(args.readme.read_text(), section)
    plot(records, args.charts)
    args.readme.write_text(updated)
    # Retain the raw samples/provenance alongside the generated figures.
    output = args.charts / "results"
    output.mkdir(parents=True, exist_ok=True)
    for record in records:
        (output / f"{record['backend']}-{record['background_links']}.json").write_text(
            json.dumps(record, indent=2, allow_nan=False) + "\n"
        )
    print(section)


if __name__ == "__main__":
    main()
