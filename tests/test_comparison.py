import unittest
from unittest.mock import Mock, patch

from scripts.workload import OPERATIONS, Scenario, Settings, measure, scenario


class WorkloadTests(unittest.TestCase):
    def test_hasura_initialization_rejects_non_http_urls(self):
        import tempfile
        from pathlib import Path
        from scripts.init_deep import main

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("sys.argv", ["init_deep", "--url", Path(directory).as_uri()]),
                patch("scripts.init_deep.time.sleep"),
                self.assertRaisesRegex(ValueError, "Invalid GraphQL URL"),
            ):
                main()

    def test_normalization_preserves_query_order(self):
        from scripts.conformance import normalized

        value = {
            "links": [{"id": "2"}, {"id": "1"}],
            "delete_links": {"returning": [{"id": "2"}, {"id": "1"}]},
        }
        actual = normalized(value)
        self.assertEqual(actual["links"], [{"id": 2}, {"id": 1}])
        self.assertEqual(actual["delete_links"]["returning"], [{"id": 1}, {"id": 2}])

    def test_nonempty_endpoint_is_rejected(self):
        from scripts.workload import seed

        client = Mock()
        client.execute.return_value = {"links": [{"id": 1}]}
        with self.assertRaises(ValueError):
            seed(client, Settings())
        self.assertEqual(client.execute.call_count, 1)

    def test_graphql_errors_are_not_measurements(self):
        from scripts.client import Client

        client = Client("http://localhost:8001/v1/graphql")
        response = Mock(status=200)
        response.read.return_value = b'{"errors":[{"message":"failed"}],"data":null}'
        client.connection = Mock()
        client.connection.getresponse.return_value = response
        with self.assertRaises(RuntimeError):
            client.execute("{ links { id } }")

    def test_sizes_are_bounded_and_validated(self):
        for name, value in [
            ("BENCHMARK_LINKS", "0"),
            ("BENCHMARK_BACKGROUND_LINKS", "1040384"),
            ("BENCHMARK_SAMPLES", "1"),
            ("BENCHMARK_LINKS", "oops"),
        ]:
            with (
                self.subTest(name=name),
                patch.dict("os.environ", {name: value}, clear=True),
            ):
                with self.assertRaises(ValueError):
                    Settings.from_env()

    def test_all_variants_use_one_shared_document(self):
        self.assertEqual(len(OPERATIONS), 8)
        docs = [scenario(op, 3, [1, 2, 3]).query for op in OPERATIONS]
        self.assertEqual(len(set(docs)), 8)
        self.assertTrue(all("returning" in doc for doc in docs[:3]))

    def test_setup_and_undo_are_outside_timer(self):
        client = Mock()
        events = []
        case = Scenario(
            "query",
            {},
            prepare=lambda c: events.append("setup"),
            undo=lambda c, data: events.append("undo"),
        )
        client.execute.side_effect = lambda *args: events.append("request") or {
            "links": [{"id": i, "from_id": i, "to_id": i + 1} for i in [1, 2, 3]]
        }
        with (
            patch("scripts.workload.scenario", return_value=case),
            patch(
                "scripts.workload.time.perf_counter_ns",
                side_effect=lambda: events.append("clock") or len(events),
            ),
        ):
            measure(client, "Each All", Settings(3, 3, 2, 0), [1, 2, 3])
        self.assertEqual(
            events,
            ["setup", "clock", "request", "clock", "undo"] * 2,
        )

    def test_successful_http_noop_is_not_a_measurement(self):
        client = Mock()
        client.execute.return_value = {
            "update_links": {
                "affected_rows": 1,
                "returning": [{"id": 1, "from_id": 1, "to_id": 2}],
            }
        }
        with self.assertRaisesRegex(ValueError, "Update"):
            measure(client, "Update", Settings(1, 1, 2, 0), [1])

    def test_mutations_have_undo_and_setup(self):
        for op in OPERATIONS[:3]:
            case = scenario(op, 3, [1, 2, 3])
            self.assertIsNotNone(case.undo)
            self.assertIsNotNone(case.prepare if op != "Create" else case.undo)


if __name__ == "__main__":
    unittest.main()
