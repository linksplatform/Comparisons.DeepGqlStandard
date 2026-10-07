"""Execute identical documents and compare every response with explicit expected rows."""

import argparse
from pathlib import Path

from graphql import build_schema, get_introspection_query, print_type
from scripts.client import Client
from scripts.workload import DELETE, INSERT, UPDATE, QUERY


def normalized(value, sort_rows=False):
    if isinstance(value, list):
        rows = [normalized(item) for item in value]
        return (
            sorted(rows, key=lambda r: r["id"])
            if sort_rows and rows and isinstance(rows[0], dict) and "id" in rows[0]
            else rows
        )
    if isinstance(value, dict):
        return {
            key: normalized(item, sort_rows=(key == "returning"))
            for key, item in value.items()
        }
    # Hasura may serialize bigint as a JSON string; compare numeric scalar values.
    return (
        int(value) if isinstance(value, str) and value.lstrip("-").isdigit() else value
    )


def verify_schema(client):
    """Check all contract fields and types; Hasura may expose extra fields."""
    contract = build_schema(
        (Path(__file__).resolve().parents[1] / "schema/links.graphql").read_text()
    )
    remote = client.execute(get_introspection_query(descriptions=False))
    from graphql import build_client_schema

    actual = build_client_schema(remote)
    for name, expected_type in contract.type_map.items():
        if name.startswith("__") or name in ("String", "Boolean", "Int"):
            continue
        actual_type = actual.get_type(name)
        if actual_type is None:
            raise AssertionError(f"Missing contract type {name}")
        if hasattr(expected_type, "fields"):
            for field_name, expected_field in expected_type.fields.items():
                actual_field = actual_type.fields.get(field_name)
                if actual_field is None or str(actual_field.type) != str(
                    expected_field.type
                ):
                    raise AssertionError(
                        f"Different contract field {name}.{field_name}"
                    )
                for arg_name, expected_arg in getattr(
                    expected_field, "args", {}
                ).items():
                    actual_arg = actual_field.args.get(arg_name)
                    if actual_arg is None or str(actual_arg.type) != str(
                        expected_arg.type
                    ):
                        raise AssertionError(
                            f"Different contract argument {name}.{field_name}({arg_name})"
                        )
        elif hasattr(expected_type, "values"):
            if not set(expected_type.values) <= set(actual_type.values):
                raise AssertionError(f"Missing enum values for {name}")
        elif print_type(expected_type) != print_type(actual_type):
            raise AssertionError(f"Different scalar {name}")


def verify(url):
    client = Client(url)
    responses = []

    def check(query, variables, expected):
        actual = normalized(client.execute(query, variables))
        if actual != expected:
            raise AssertionError(
                f"{url}: {query}\nexpected {expected}\nactual {actual}"
            )
        responses.append(actual)

    rows = [
        {"id": i + 1, "from_id": source, "to_id": target}
        for i, (source, target) in enumerate([(1, 1), (1, 2), (2, 1), (2, 3), (3, 2)])
    ]
    try:
        verify_schema(client)
        check(QUERY, {"where": {}}, {"links": []})
        check(
            INSERT,
            {"objects": [{k: v for k, v in row.items() if k != "id"} for row in rows]},
            {"insert_links": {"affected_rows": 5, "returning": rows}},
        )
        for where, indices in [
            ({}, range(5)),
            ({"id": {"_eq": 3}}, [2]),
            ({"from_id": {"_eq": 1}, "to_id": {"_eq": 2}}, [1]),
            ({"from_id": {"_eq": 1}}, [0, 1]),
            ({"to_id": {"_eq": 1}}, [0, 2]),
            ({"to_id": {"_eq": 2}}, [1, 4]),
            ({"id": {"_in": []}}, []),
            ({"id": {"_in": [1, 5]}}, [0, 4]),
            ({"id": {"_in": [1, 1, 5, -1, 999]}}, [0, 4]),
            ({"id": {"_gte": 2, "_lte": 4}}, [1, 2, 3]),
            ({"_or": [{"id": {"_eq": 1}}, {"id": {"_eq": 5}}]}, [0, 4]),
            ({"_and": [{"from_id": {"_eq": 1}}, {"_not": {"id": {"_eq": 1}}}]}, [1]),
            ({"id": {"_eq": -1}}, []),
            ({"_or": []}, []),
            ({"_and": []}, range(5)),
        ]:
            check(QUERY, {"where": where}, {"links": [rows[i] for i in indices]})
        check(
            "query { links(order_by: {id: desc}, limit: 2, offset: 1) { id from_id to_id } }",
            {},
            {"links": [rows[3], rows[2]]},
        )
        changed = {**rows[3], "to_id": 2}
        check(
            UPDATE,
            {"where": {"id": {"_eq": 4}}, "set": {"to_id": 2}},
            {"update_links": {"affected_rows": 1, "returning": [changed]}},
        )
        rows[3] = changed
        check(QUERY, {"where": {}}, {"links": rows})
        check(
            UPDATE,
            {"where": {"id": {"_eq": 999}}, "set": {"from_id": 1}},
            {"update_links": {"affected_rows": 0, "returning": []}},
        )
        check(
            DELETE,
            {"where": {"id": {"_in": [2, 4]}}},
            {"delete_links": {"affected_rows": 2, "returning": [rows[1], rows[3]]}},
        )
        check(QUERY, {"where": {}}, {"links": [rows[i] for i in [0, 2, 4]]})
        check(
            DELETE,
            {"where": {"id": {"_eq": 999}}},
            {"delete_links": {"affected_rows": 0, "returning": []}},
        )
        check(
            DELETE,
            {"where": {}},
            {
                "delete_links": {
                    "affected_rows": 3,
                    "returning": [rows[i] for i in [0, 2, 4]],
                }
            },
        )
        check(QUERY, {"where": {}}, {"links": []})
        return responses
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "urls", nargs="+", help="Fresh, dedicated GraphQL endpoints (all three in CI)"
    )
    args = parser.parse_args()
    baseline = None
    for url in args.urls:
        responses = verify(url)
        if baseline is not None and responses != baseline:
            raise AssertionError(f"Server {url} differs from baseline")
        baseline = responses
        print(f"{url}: {len(responses)} conformance responses passed")


if __name__ == "__main__":
    main()
