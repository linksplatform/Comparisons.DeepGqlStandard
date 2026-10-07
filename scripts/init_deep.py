"""Wait for Hasura and track the freshly initialized Deep core links table."""

import argparse
import json
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    for attempt in range(60):
        try:
            with urlopen(args.url + "/healthz", timeout=2) as response:
                if response.status == 200:
                    break
        except (OSError, URLError, TimeoutError):
            pass
        time.sleep(1)
    else:
        raise RuntimeError("Hasura did not become healthy")
    request = Request(
        args.url + "/v1/metadata",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {
                "type": "pg_track_table",
                "args": {
                    "source": "default",
                    "table": {"schema": "public", "name": "links"},
                },
            }
        ).encode(),
    )
    with urlopen(request, timeout=30) as response:
        print(response.read().decode())


if __name__ == "__main__":
    main()
