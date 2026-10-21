"""Casos de borda complementares do Core; não substituem os AT independentes."""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from udh.domain import CoreError, require_text
from udh.service import CoreService
from udh.store import SQLiteStore


ROOT = Path(__file__).resolve().parents[1]


class CoreStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="udh-it-", dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "core.sqlite"
        self.service = CoreService(SQLiteStore(self.path))

    def snapshot(self, path=None):
        with closing(sqlite3.connect(path or self.path)) as connection, connection:
            return tuple(connection.iterdump())

    def create(self, **overrides):
        values = dict(spec_id="slice1", spec_version="v1", request_id="req-1")
        values.update(overrides)
        return self.service.create(**values)

    def test_event_failure_rolls_back_new_run_and_preserves_previous_data(self):
        self.service.initialize()
        previous = self.create()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("""CREATE TRIGGER reject_new_event BEFORE INSERT ON events
                BEGIN SELECT RAISE(ABORT, 'fixture: evento rejeitado'); END""")
        before = self.snapshot()
        with self.assertRaises(CoreError):
            self.create(request_id="req-2")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.service.show(previous.run_id).to_dict(), previous.to_dict())
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM runs").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM events").fetchone()[0], 1)

    def test_literal_filename_and_missing_database_do_not_initialize_implicitly(self):
        path = Path(self.directory.name) / "execução ?# espaço.sqlite"
        service = CoreService(SQLiteStore(path))
        with self.assertRaises(CoreError):
            service.create(spec_id="s", spec_version="v", request_id="r")
        with self.assertRaises(CoreError):
            service.show("00000000-0000-0000-0000-000000000000")
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])
        self.assertEqual(service.initialize(), 1)
        run = service.create(spec_id="s", spec_version="v", request_id="r")
        self.assertEqual(service.show(run.run_id).to_dict(), run.to_dict())
        self.assertEqual(list(Path(self.directory.name).iterdir()), [path])

    def test_unicode_references_and_edges_are_preserved_without_normalization(self):
        self.service.initialize()
        expected = dict(spec_id="  decisão e\u0301 漢字  ", spec_version="\tvβ\n", request_id=" requisição 🐍 ")
        run = self.create(**expected)
        result = self.service.show(run.run_id).to_dict()
        for field, value in expected.items():
            self.assertEqual(result[field], value)
        self.assertEqual(require_text(expected["spec_id"], "spec_id"), expected["spec_id"])

    def test_invalid_whitespace_does_not_mutate_service_state(self):
        self.service.initialize()
        self.create()
        before = self.snapshot()
        for field in ("spec_id", "spec_version", "request_id"):
            for value in ("", " \t\r\n", "\u2003\u00a0"):
                with self.subTest(field=field, value=repr(value)):
                    with self.assertRaises(CoreError):
                        self.create(**{field: value})
                    self.assertEqual(self.snapshot(), before)

    def test_service_idempotency_preserves_full_record_and_rejects_conflict(self):
        self.service.initialize()
        first = self.create()
        before = self.snapshot()
        reopened = CoreService(SQLiteStore(self.path))
        repeated = reopened.create(spec_id="slice1", spec_version="v1", request_id="req-1")
        self.assertEqual(repeated.to_dict(), first.to_dict())
        with self.assertRaises(CoreError):
            reopened.create(spec_id="slice1", spec_version="v2", request_id="req-1")
        self.assertEqual(self.snapshot(), before)

    def test_unknown_or_forged_schema_is_rejected_without_mutation(self):
        fixtures = (
            ("empty-v1", "PRAGMA user_version=1;"),
            ("future", "PRAGMA user_version=2;"),
            ("forged", """CREATE TABLE runs (
                id TEXT PRIMARY KEY, request_id TEXT, spec_id TEXT, spec_version TEXT,
                state TEXT, created_at TEXT);
                CREATE TABLE events (
                sequence INTEGER PRIMARY KEY, run_id TEXT, kind TEXT, created_at TEXT);
                PRAGMA user_version=1;"""),
        )
        for name, sql in fixtures:
            with self.subTest(fixture=name):
                path = Path(self.directory.name) / (name + ".sqlite")
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.executescript(sql)
                before = self.snapshot(path)
                service = CoreService(SQLiteStore(path))
                with self.assertRaises(CoreError):
                    service.initialize()
                self.assertEqual(self.snapshot(path), before)

    def test_foreign_table_with_similar_sqlite_prefix_is_not_ignored(self):
        self.service.initialize()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('CREATE TABLE sqliteXforeign(value TEXT)')
            connection.execute('INSERT INTO sqliteXforeign VALUES (?)', ('preservar',))
        before = self.snapshot()
        with self.assertRaises(CoreError):
            self.service.initialize()
        self.assertEqual(self.snapshot(), before)

    def test_schema_comparison_preserves_whitespace_inside_sql_literals(self):
        self.service.initialize()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('PRAGMA writable_schema=ON')
            connection.execute(
                "UPDATE sqlite_master SET sql=replace(sql, ?, ?) WHERE name='runs'",
                ("||' '", "||'\t'"),
            )
            connection.execute('PRAGMA writable_schema=OFF')
        before = self.snapshot()
        with self.assertRaises(CoreError):
            self.service.initialize()
        self.assertEqual(self.snapshot(), before)

    def test_cli_errors_keep_stdout_empty_and_stderr_readable(self):
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        for arguments in (
            ["show", "--db", str(self.path), "--run-id", "desconhecido"],
            ["create", "--db", str(self.path), "--spec-id", "s", "--spec-version", "v"],
            ["promote", "--db", str(self.path)],
        ):
            with self.subTest(command=arguments[0]):
                result = subprocess.run(
                    [sys.executable, "-m", "udh", *arguments], cwd=ROOT,
                    env=environment, capture_output=True, text=True, timeout=10,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertTrue(result.stderr.strip())
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
