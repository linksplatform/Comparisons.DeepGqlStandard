"""Persistent HTTP GraphQL client shared by conformance tests and measurements."""

import http.client
import json
import os
from urllib.parse import urlsplit


class Client:
    def __init__(self, url):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"Invalid GraphQL URL: {url}")
        connection = (
            http.client.HTTPSConnection
            if parts.scheme == "https"
            else http.client.HTTPConnection
        )
        self.connection = connection(parts.hostname, parts.port, timeout=30)
        self.path = parts.path or "/"
        self.headers = {"Content-Type": "application/json"}
        if os.environ.get("HASURA_ADMIN_SECRET"):
            self.headers["x-hasura-admin-secret"] = os.environ["HASURA_ADMIN_SECRET"]
        self.trace = os.environ.get("BENCHMARK_TRACE") == "1"

    def execute(self, query, variables=None):
        body = json.dumps(
            {"query": query, "variables": variables or {}}, separators=(",", ":")
        )
        self.connection.request("POST", self.path, body, self.headers)
        response = self.connection.getresponse()
        data = response.read()
        if self.trace:
            import sys

            print(
                f"GraphQL HTTP {response.status}: {query[:200]} ({len(data)} bytes)",
                file=sys.stderr,
            )
        if response.status != 200:
            raise RuntimeError(f"GraphQL HTTP {response.status}: {data[:1000]!r}")
        result = json.loads(data)
        if result.get("errors") or "data" not in result:
            raise RuntimeError(f"GraphQL failed: {result}")
        return result["data"]

    def close(self):
        self.connection.close()
