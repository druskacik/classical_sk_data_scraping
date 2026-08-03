import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from seeds.import_crawler_sources import import_seed


class FakeCursor:
    def __init__(self):
        self.query = ""

    def execute(self, query, params=None):
        self.query = query

    def fetchone(self):
        if "crawler_source_seed WHERE" in self.query:
            return None
        if "RETURNING status" in self.query:
            return {"status": "pending"}
        return None


class FakeConnection:
    def __init__(self):
        self.committed = False

    def commit(self):
        self.committed = True

    def rollback(self):
        raise AssertionError("import unexpectedly rolled back")


class FakeRegistry:
    def __init__(self):
        self.connection = FakeConnection()
        self.ingested = []

    @contextmanager
    def cursor(self):
        yield FakeCursor()

    def ingest_source(self, url, country_code, **kwargs):
        self.ingested.append((url, country_code, kwargs))
        return {
            "id": len(self.ingested),
            "crawler_path": kwargs["crawler_path"],
            "status": "pending",
        }


class ImportCrawlerSourcesTests(unittest.TestCase):
    def test_country_and_path_may_be_omitted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / "0001_nullable.csv"
            seed.write_text("url,country_code,crawler_path\nhttps://example.com/,,\n")
            registry = FakeRegistry()

            result = import_seed(seed, root, registry)

        self.assertEqual(result["pending"], 1)
        _, country, kwargs = registry.ingested[0]
        self.assertIsNone(country)
        self.assertIsNone(kwargs["crawler_path"])
        self.assertEqual(kwargs["geographic_scope"], "unknown")
        self.assertTrue(registry.connection.committed)

    def test_multi_country_hint_gets_common_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / "0002_common.csv"
            seed.write_text(
                "url,country_code,scope_hint,crawler_path\n"
                "https://tickets.example/,,multi_country,\n"
            )
            registry = FakeRegistry()

            import_seed(seed, root, registry)

        _, country, kwargs = registry.ingested[0]
        self.assertIsNone(country)
        self.assertEqual(kwargs["crawler_path"], "crawlers/common/tickets_example")
        self.assertEqual(kwargs["geographic_scope"], "unknown")


if __name__ == "__main__":
    unittest.main()
