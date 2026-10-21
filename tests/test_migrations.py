"""Verificações complementares de migração; não substituem aceite independente."""

from contextlib import closing, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from udh.domain import CoreError
from udh.service import CoreService
from udh.store import SQLiteStore


ROOT = Path(__file__).resolve().parents[1]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="udh-migration-", dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "execução ?#.sqlite"
        self.service = CoreService(SQLiteStore(self.path))

    def snapshot(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return (connection.execute("PRAGMA user_version").fetchone()[0],
                    tuple(connection.iterdump()))

    def create(self, request_id=" criação literal "):
        return self.service.create(spec_id=" decisão e\u0301 ", spec_version="\tvβ\n",
                                   request_id=request_id)

    def test_missing_database_is_not_created_by_migration(self):
        with self.assertRaises(CoreError):
            self.service.migrate()
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_migration_preserves_literal_values_and_sparse_event_sequences(self):
        self.assertEqual(self.service.initialize(), 1)
        first = self.create().to_dict()
        second = self.create("outra").to_dict()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE events SET sequence=41 WHERE run_id=?",
                               (second["run_id"],))
        second["events"][0]["sequence"] = 41
        old_policy = self.service.policy(first["run_id"], "TRANSITION_RUN")
        self.assertNotIn("schema_version", first)
        self.assertNotIn("revision", first)
        self.assertEqual(self.service.migrate(), {"schema_version": 2, "migrated": True})
        for previous in (first, second):
            self.assertEqual(self.service.show(previous["run_id"]).to_dict(),
                             dict(previous, schema_version=2, revision=0))
        self.assertEqual(self.service.policy(first["run_id"], "TRANSITION_RUN"), old_policy)
        before = self.snapshot()
        self.assertEqual(self.service.migrate(), {"schema_version": 2, "migrated": False})
        with self.assertRaises(CoreError):
            self.service.initialize()
        self.assertEqual(self.snapshot(), before)

    def test_v2_create_replay_and_conflict_preserve_database(self):
        self.service.initialize()
        self.service.migrate()
        first = self.create().to_dict()
        self.assertEqual((first["schema_version"], first["revision"], first["state"]),
                         (2, 0, "PENDING"))
        self.assertEqual([event["kind"] for event in first["events"]], ["RUN_CREATED"])
        before = self.snapshot()
        reopened = CoreService(SQLiteStore(self.path))
        self.assertEqual(reopened.create(spec_id=first["spec_id"],
                                        spec_version=first["spec_version"],
                                        request_id=first["request_id"]).to_dict(), first)
        with self.assertRaises(CoreError):
            reopened.create(spec_id="outra", spec_version=first["spec_version"],
                            request_id=first["request_id"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(reopened.policy(first["run_id"], "READ_RUN")["decision"], "ALLOW")
        self.assertEqual(reopened.policy(first["run_id"], "TRANSITION_RUN")["decision"], "DENY")

    def test_incompatible_legacy_data_and_trigger_reject_without_any_migration(self):
        fixtures = {
            "evento órfão": "INSERT INTO events VALUES (99, 'ausente', 'RUN_CREATED', '2026-10-21T00:00:00Z')",
            "UUID inválido": "UPDATE runs SET id='inválido'",
            "data não RFC3339": "UPDATE runs SET created_at='2026-10-21 00:00:00Z'",
            "texto vazio Unicode": "UPDATE runs SET spec_id=char(8195)",
            "evento ausente": "DELETE FROM events",
            "trigger extra": "CREATE TRIGGER extra AFTER INSERT ON events BEGIN SELECT 1; END",
        }
        for name, mutation in fixtures.items():
            with self.subTest(fixture=name):
                self.path = Path(self.directory.name) / (name + ".sqlite")
                self.service = CoreService(SQLiteStore(self.path))
                self.service.initialize()
                self.create()
                with closing(sqlite3.connect(self.path)) as connection, connection:
                    connection.execute(mutation)
                before = self.snapshot()
                with self.assertRaises(CoreError):
                    self.service.migrate()
                self.assertEqual(self.snapshot(), before)

    def test_v2_schema_literal_and_nullability_forgery_are_rejected(self):
        replacements = (("||' '", "||'\t'"),
                        ("id TEXT PRIMARY KEY NOT NULL", "id TEXT PRIMARY KEY"))
        for index, replacement in enumerate(replacements):
            with self.subTest(replacement=replacement):
                self.path = Path(self.directory.name) / (str(index) + ".sqlite")
                self.service = CoreService(SQLiteStore(self.path))
                self.service.initialize()
                run = self.create()
                self.service.migrate()
                with closing(sqlite3.connect(self.path)) as connection, connection:
                    original = connection.execute(
                        "SELECT sql FROM sqlite_master WHERE name='runs'").fetchone()[0]
                    self.assertIn(replacement[0], original)
                    connection.execute("PRAGMA writable_schema=ON")
                    connection.execute("UPDATE sqlite_master SET sql=replace(sql, ?, ?) WHERE name='runs'",
                                       replacement)
                    connection.execute("PRAGMA writable_schema=OFF")
                before = self.snapshot()
                with self.assertRaises(CoreError):
                    self.service.show(run.run_id)
                with self.assertRaises(CoreError):
                    self.service.migrate()
                self.assertEqual(self.snapshot(), before)

    def test_v2_pending_run_rejects_unmatched_block_event(self):
        self.service.initialize()
        run = self.create()
        self.service.migrate()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("INSERT INTO events VALUES (77, ?, 'RUN_BLOCKED', ?)",
                               (run.run_id, run.created_at))
        before = self.snapshot()
        with self.assertRaises(CoreError):
            self.service.show(run.run_id)
        with self.assertRaises(CoreError):
            self.service.migrate()
        self.assertEqual(self.snapshot(), before)

    def test_v2_blocked_fixture_reads_authorization_and_rejects_cross_run_journal(self):
        self.service.initialize()
        run = self.create()
        other = self.create("outra")
        self.service.migrate()
        authorization_id = "d479ad4c-4a0a-4b08-b4a0-f905002ef3c8"
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE runs SET state='BLOCKED', revision=1 WHERE id=?",
                               (run.run_id,))
            connection.execute("INSERT INTO events VALUES (88, ?, 'RUN_BLOCKED', ?)",
                               (run.run_id, run.created_at))
            connection.execute(
                "INSERT INTO block_requests (request_id, run_id, expected_revision, reason, "
                "event_sequence, authorization_id, controller_route, core_manifest_sha256, "
                "control_sha256, authorization_rule_version) VALUES (?, ?, 0, ?, 88, ?, "
                "'OPERATOR_LOCAL', ?, ?, 1)",
                (" pausa ", run.run_id, " impedimento literal ", authorization_id,
                 "a" * 64, "b" * 64))
        result = self.service.show(run.run_id).to_dict()
        self.assertEqual((result["state"], result["revision"], result["schema_version"]),
                         ("BLOCKED", 1, 2))
        self.assertEqual(result["events"][-1], {
            "sequence": 88, "kind": "RUN_BLOCKED", "created_at": run.created_at,
            "from_state": "PENDING", "to_state": "BLOCKED", "reason": " impedimento literal ",
            "request_id": " pausa ", "revision": 1, "authorization_id": authorization_id})
        self.assertEqual(self.service.policy(run.run_id, "READ_RUN")["state"], "BLOCKED")
        self.assertEqual(self.service.migrate(), {"schema_version": 2, "migrated": False})
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE block_requests SET run_id=?", (other.run_id,))
        before = self.snapshot()
        with self.assertRaises(CoreError):
            self.service.show(run.run_id)
        with self.assertRaises(CoreError):
            self.service.migrate()
        self.assertEqual(self.snapshot(), before)

    def test_operator_without_controller_context_denies_before_sqlite_io(self):
        from udh.operator import main

        for arguments in (["migrate", "--to", "2"],
                          ["block", "--run-id", "inválido"]):
            with self.subTest(arguments=arguments):
                stdout, stderr = StringIO(), StringIO()
                with patch("sqlite3.connect", side_effect=AssertionError("I/O indevido")), \
                        redirect_stdout(stdout), redirect_stderr(stderr):
                    status = main(arguments)
                self.assertEqual(status, 3)
                self.assertEqual(stderr.getvalue(), "")
                response = json.loads(stdout.getvalue())
                self.assertEqual(response["decision"], "DENY")
                self.assertEqual(response["reason_code"], "AUTHORIZATION_REQUIRED")
                self.assertTrue(response["message"])
                self.assertTrue(response["next_action"])
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
