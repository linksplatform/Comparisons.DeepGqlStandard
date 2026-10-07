"""Bounded reproduction: batch updates after repeated create/delete on 1,000 links."""

import os
from pathlib import Path
import resource

from scripts.run import server
from scripts.workload import Client, OPERATIONS, Settings, measure, seed

resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
settings = Settings(10, 1000, 3, 1)
os.environ["BENCHMARK_TRACE"] = "1"
with server("rust", Path("results/reproduction")) as url:
    client = Client(url)
    try:
        client.connection.timeout = 3
        ids = seed(client, settings)
        for operation in OPERATIONS:
            print(operation, flush=True)
            print(measure(client, operation, settings, ids), flush=True)
    finally:
        client.close()
