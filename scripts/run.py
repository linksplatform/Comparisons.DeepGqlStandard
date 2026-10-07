"""Start dedicated servers, run tests/benchmarks, and stop every owned process."""

import argparse
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

from scripts.client import Client
from scripts.workload import BACKENDS

ROOT = Path(__file__).resolve().parents[1]
URLS = {
    "deep": "http://127.0.0.1:8080/v1/graphql",
    "rust": "http://127.0.0.1:8001/v1/graphql",
    "csharp": "http://127.0.0.1:8002/v1/graphql",
}


def compose():
    command = ["docker", "compose", "-f", str(ROOT / "compose.yml")]
    if os.environ.get("COMPOSE_OVERRIDE"):
        command += ["-f", os.environ["COMPOSE_OVERRIDE"]]
    return command


def bounded_process():
    # Finite workload settings and per-server virtual-memory/stack limits bound probes.
    for kind, requested in [
        (resource.RLIMIT_AS, 4 * 1024**3),
        (resource.RLIMIT_STACK, 32 * 1024**2),
    ]:
        _, hard = resource.getrlimit(kind)
        soft = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
        resource.setrlimit(kind, (soft, hard))


@contextmanager
def server(backend, log_directory):
    log_directory.mkdir(parents=True, exist_ok=True)
    with (log_directory / f"{backend}-server.log").open("w") as log:
        process = None
        try:
            if backend == "deep":
                subprocess.run(
                    compose() + ["up", "-d"],
                    check=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                subprocess.run(
                    [sys.executable, "-m", "scripts.init_deep"],
                    check=True,
                    cwd=ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            else:
                command = (
                    [str(ROOT / "rust/target/release/deepgql-doublets-rust")]
                    if backend == "rust"
                    else [
                        "dotnet",
                        str(ROOT / "csharp/bin/Release/net10.0/Doublets.Gql.dll"),
                    ]
                )
                # .NET reserves a large virtual address space; bound its managed heap instead.
                environment = {**os.environ, "DOTNET_GCHeapHardLimit": "0x80000000"}
                process = subprocess.Popen(
                    command,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    cwd=ROOT,
                    env=environment,
                    preexec_fn=bounded_process if backend == "rust" else None,
                )
            for attempt in range(60):
                if process is not None and process.poll() is not None:
                    raise RuntimeError(f"{backend} exited; see {log.name}")
                try:
                    client = Client(URLS[backend])
                    try:
                        client.execute("query { links(limit: 0) { id } }")
                    finally:
                        client.close()
                    break
                except (OSError, RuntimeError):
                    time.sleep(1)
            else:
                raise RuntimeError(f"{backend} not ready; see {log.name}")
            yield URLS[backend]
        finally:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if backend == "deep":
                subprocess.run(
                    compose() + ["logs", "--no-color"],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                subprocess.run(
                    compose() + ["down", "-v"],
                    check=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["conformance", "benchmark"])
    parser.add_argument("--backend", choices=BACKENDS)
    parser.add_argument("--output", type=Path, default=Path("results"))
    args = parser.parse_args()
    if args.mode == "conformance":
        from scripts.conformance import verify

        with ExitStack() as stack:
            urls = [
                stack.enter_context(server(b, args.output / "logs")) for b in BACKENDS
            ]
            baseline = None
            for url in urls:
                responses = verify(url)
                if baseline is not None and responses != baseline:
                    raise AssertionError(f"{url} differs from baseline")
                baseline = responses
                print(
                    f"{url}: {len(responses)} identical conformance responses passed",
                    flush=True,
                )
    else:
        if not args.backend:
            parser.error("benchmark requires --backend")
        from scripts.workload import Settings

        size = Settings.from_env().background
        with server(args.backend, args.output / "logs") as url:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.workload",
                    args.backend,
                    url,
                    str(args.output / f"{args.backend}-{size}.json"),
                ],
                cwd=ROOT,
                check=True,
            )


if __name__ == "__main__":
    main()
