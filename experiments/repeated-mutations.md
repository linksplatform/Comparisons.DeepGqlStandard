# Repeated mutation regression

The first Rust adapter used `doublets` 0.5.0's split store. Seeding 1,000
`(i, i+1)` links, inserting/deleting temporary `(i, i)` pairs, and then updating
background targets to `1` made its native index traversal hang. The HTTP client
timed out after 30 seconds. A smaller native test with 20 background links and
three changed links reproduces the hang within a five-second bound.

`rust/src/main.rs::tests::repeated_batch_mutations_preserve_indexes` exercises
the sequence without HTTP. With a split store its receiver timed out after five
seconds; with the united store the test completes, including its indexed lookup.
Both benchmark adapters therefore use united volatile stores.

Run the retained bounded HTTP workload against the current Rust server:

```bash
cargo build --manifest-path rust/Cargo.toml --release --locked
python3 -m experiments.repeat_workload
```

The script uses N=10, B=1,000, three samples, one warm-up request, a two-GiB
address-space limit, a 60-second CPU limit and a three-second HTTP timeout.
Tracing is enabled to identify the last successful operation. It completes all
eight operations with the united store. Trace runs are diagnostic rather than
published performance samples.

To reproduce the original native failure in a disposable checkout, change the
`RawStore` alias to
`split::Store<usize, Global<split::DataPart<usize>>, Global<split::IndexPart<usize>>>`,
import `doublets::split` instead of `doublets::unit`, and replace the two
`RawStore::new(Global::new())` calls with `RawStore::new(Global::new(), Global::new())`.
Then run only the regression test:

```bash
cargo test --manifest-path rust/Cargo.toml --release --locked repeated_batch_mutations_preserve_indexes
```

Keep the five-second receiver bound. Do not run an unbounded mutation probe.

The workload driver has a second correctness regression:
`test_successful_http_noop_is_not_a_measurement` supplies a successful update
response containing an unchanged target. Before response validation, it was
accepted as a sample; the driver now rejects it before emitting results. Both
regressions were observed failing before their respective fixes.
