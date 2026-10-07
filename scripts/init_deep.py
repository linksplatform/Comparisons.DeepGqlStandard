"""Wait for Hasura and track the freshly initialized Deep core links table."""

import argparse
import json
import time
from scripts.client import Client


def request(url, path, body=None, timeout=30):
    # The shared client permits only HTTP and HTTPS, including for health/metadata.
    client = Client(url)
    client.connection.timeout = timeout
    try:
        client.connection.request(
            "POST" if body is not None else "GET",
            client.path.rstrip("/") + path,
            json.dumps(body) if body is not None else None,
            client.headers,
        )
        response = client.connection.getresponse()
        data = response.read().decode()
        if response.status != 200:
            raise RuntimeError(f"Hasura HTTP {response.status}: {data[:1000]}")
        return data
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    for attempt in range(60):
        try:
            request(args.url, "/healthz", timeout=2)
            break
        except (OSError, RuntimeError):
            pass
        time.sleep(1)
    else:
        raise RuntimeError("Hasura did not become healthy")
    print(
        request(
            args.url,
            "/v1/metadata",
            {
                "type": "pg_track_table",
                "args": {
                    "source": "default",
                    "table": {"schema": "public", "name": "links"},
                },
            },
        )
    )


if __name__ == "__main__":
    main()
