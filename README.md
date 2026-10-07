# GraphQL server comparison

Deep's PostgreSQL + Hasura core links storage versus Doublets Rust and Doublets C#,
through the same GraphQL-over-HTTP workload. This repository now contains runnable
servers, correctness tests, a benchmark driver and generated reporting.

## Scope and schema

[`schema/links.graphql`](schema/links.graphql) defines the executable common subset
of the [Data.Doublets.Gql](https://github.com/linksplatform/Data.Doublets.Gql) /
Hasura API: `links`, `insert_links`, `update_links`, `delete_links`, with `id`,
`from_id`, `to_id`, equality, membership, range and boolean filters, ordering by
id, limit and offset. Rust uses async-graphql + Axum over `doublets` 0.5.0;
C# uses GraphQL.NET over `Platform.Data.Doublets` 0.18.1. Both use united volatile
Doublets stores and push equality and id-membership filters into their native indexes.
The adapters follow Data.Doublets.Gql's field and resolver conventions; they are
maintained in `rust/` and `csharp/` here because its legacy servers require old
runtimes and have unfinished resolvers. All original submodule pointers remain.

The Deep baseline uses PostgreSQL 16.13 and Hasura 2.48.13, both pinned by digest
in [`compose.yml`](compose.yml). [`deep/links.sql`](deep/links.sql) reproduces the
core table and indexes from [Deep's links migration at
381207d](https://github.com/deep-foundation/deeplinks/blob/381207dda313ab3c1fed0a4bfc2ddbe631750411/migrations/1616701513782-links.ts),
with a default `type_id = 0`. This compares core links storage, without Deep's
application triggers, permissions, values, relationships, materialized paths or
subscriptions. Hasura exposes more schema fields than the common subset.

Workloads use nonnegative, non-null endpoints and distinct `(from_id, to_id)`
pairs. They do not benchmark duplicates, explicit ids, nullable endpoints,
transactions or concurrency. Generated ids can be reused by Doublets and are
monotonic in PostgreSQL; tests start with fresh stores and benchmarks use ids
returned by each server. The comparison does not claim equivalence outside this
shared workload domain.

## Running locally

Prerequisites: Docker with Compose, Rust 1.98.0, .NET SDK 10.0.112 (runtime 10.0.12), and Python
3.12 or newer. Versions of libraries are pinned in Cargo.lock and
packages.lock.json; reporting dependencies are in requirements.txt.

```bash
python3 -m pip install -r requirements.txt
cargo build --manifest-path rust/Cargo.toml --release --locked
dotnet restore csharp/Doublets.Gql.csproj --locked-mode
dotnet build csharp/Doublets.Gql.csproj -c Release --no-restore

# Unit tests and the same-response test against all three real HTTP servers.
python3 -m unittest discover -s tests -v
cargo test --manifest-path rust/Cargo.toml --release --locked
python3 -m scripts.run conformance

# Each command starts an empty dedicated backend and cleans up after itself.
export BENCHMARK_BACKGROUND_LINKS=1000 BENCHMARK_LINKS=10
export BENCHMARK_SAMPLES=10 BENCHMARK_WARMUP=2
python3 -m scripts.run benchmark --backend deep
python3 -m scripts.run benchmark --backend rust
python3 -m scripts.run benchmark --backend csharp
python3 -m scripts.report results --readme README.md --charts Docs
```

`python3 -m scripts.run` owns ports 8080, 8001 and 8002, starts only the required
servers, saves server logs under `results/logs/`, and stops its processes and
Compose services on completion or failure. Use a dedicated checkout and database;
Compose teardown removes its database. The low-level driver also accepts an
existing **empty dedicated** endpoint:
`python3 -m scripts.workload rust http://127.0.0.1:8001/v1/graphql results/rust-1000.json`.
It refuses to seed a nonempty store. Set `BENCHMARK_TRACE=1` for optional HTTP
tracing (off by default; trace runs should not be used for published timings).
Set `COMPOSE_OVERRIDE` to an optional local Compose override file if needed.

| Environment variable | Default | Meaning |
|---|---:|---|
| `BENCHMARK_BACKGROUND_LINKS` | 1000 | B, links seeded outside timing |
| `BENCHMARK_LINKS` | 100 | N, links affected by a mutation request |
| `BENCHMARK_SAMPLES` | 10 | Measured requests per operation (2–1000) |
| `BENCHMARK_WARMUP` | 2 | Untimed requests per operation (0–100) |
| `LISTEN_ADDRESS` | Rust: `127.0.0.1:8001`; C#: `http://127.0.0.1:8002` | Adapter bind address |

Require `1 ≤ N ≤ B ≤ 1,000,000` and `B + N < 1,040,384`, the capacity limit of
Rust doublets 0.5.0 described by the [sibling comparison](https://github.com/linksplatform/Comparisons.Neo4jVSDoublets/tree/main/experiments/store-capacity).
Servers are bounded to 4 GiB virtual memory (Rust), 2 GiB managed heap (C#),
and 2 GiB per database container; Rust's stack limit is 32 MiB.

| Operation | Shared request and dataset |
|---|---|
| Create | Insert N temporary pairs `(i, i)`; delete returned ids after timing |
| Update | Set `to_id = 1` for N background ids; restore each original pair after timing |
| Delete | Insert N temporary pairs outside timing, delete their returned ids |
| Each All | Query and order all B background links |
| Each Identity | Query one background id |
| Each Concrete | Query `from_id = 1` and `to_id = 2` |
| Each Outgoing | Query `from_id = 1` |
| Each Incoming | Query `to_id = 2` |

Background pairs are `(i, i+1)`, so each filtered query returns one row.
Create/update/delete each send **one batched HTTP request**, not N requests.
The driver uses one persistent HTTP connection and exactly the same documents,
variables and settings for every server. Each sample includes client JSON
encoding/decoding, HTTP, GraphQL parsing/validation/execution, storage and response
serialization. Setup, seeding, warm-up, response validation and mutation cleanup are untimed. Each
response is checked for the expected affected count, ids and endpoints before it
can become a sample. The complete background dataset is checked again at the end. These are end-to-end
server comparisons, not isolated storage or language-speed measurements; Hasura's
query-plan caching and PostgreSQL durability differ from volatile Doublets.

Rust uses the united store because the split store in doublets 0.5.0 hangs during
repeated batched mutations on non-point links. A bounded regression test
(`repeated_batch_mutations_preserve_indexes`, 5 seconds) reproduces this failure
and verifies the chosen store. `python3 -m experiments.repeat_workload` retains
the small HTTP reproduction (1,000 background links, 3 samples, memory/CPU limits).

The conformance test checks explicit expected responses and compares all three
servers: every query variant, membership/range/boolean filters, pagination,
partial updates, missing ids, batched deletes and final state. The Python tests
check workload bounds, timing boundaries and reporting validation/ratios. Rust
also tests repeated mutations, partial updates and negative identity lookups. Local CI checks:

```bash
ruff check scripts tests
python3 -m unittest discover -s tests -v
cargo fmt --manifest-path rust/Cargo.toml --check
cargo clippy --manifest-path rust/Cargo.toml --release --all-targets --locked -- -D warnings
dotnet format csharp/Doublets.Gql.csproj --verify-no-changes --no-restore
```

## CI and results

[`.github/workflows/benchmarks.yml`](.github/workflows/benchmarks.yml) checks
formatting, lint, builds, unit tests and real three-server conformance first.
Benchmark jobs then use a **server × B** matrix: Deep, Rust and C#, each in its
own runner. PRs use `B = 1,000`, `N = 10`; main and manual main runs use
`B = 10,000 / 100,000 / 1,000,000`, `N = 100`. All use 10 measured samples and
2 warm-up requests per operation. There are path filters, branch concurrency,
explicit job timeouts and pinned actions, toolchains and database images.

Every successful run uploads raw samples, logs and a generated README with
linear and log charts. CI commits README, charts and raw samples under
`Docs/results/` only on main. It changes only the results markers below and
rejects incomplete backends/operations, duplicate results, inconsistent workloads,
missing provenance and invalid statistics. Generated-result-only commits do not
match the workflow's path filters. Times show sample medians and standard
deviations, ratios to Deep (`× faster`, `× slower`, `≈ same`), dependency versions,
CPU, commit, run link and UTC date. Treat overlapping error ranges or differences
under 5% as approximately the same.

<!-- results:start -->
_No published results yet. A successful main run will commit measured tables, provenance and linear/log charts here. PR reports are available as CI artifacts._
<!-- results:end -->
