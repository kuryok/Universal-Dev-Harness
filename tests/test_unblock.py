"""Complementares do preparo protegido; não substituem os AT independentes."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1] / 'bootstrap/slice1'
sys.path.insert(0, str(BASE))
import block_seal
from block_fixtures.operator_launcher import validate
from integrate_block import canonical, digest, prepare_control


class UnblockPreparationTests(unittest.TestCase):
    def test_profiles_are_exact_and_do_not_import_candidate(self):
        with tempfile.TemporaryDirectory(prefix='block-integration-', dir=BASE) as name:
            root = Path(name)
            core = root / 'core'
            (core / 'udh').mkdir(parents=True)
            source = b'raise RuntimeError("candidato jamais importado no host")\n'
            (core / 'udh/__init__.py').write_bytes(source)
            hashes = {'udh/__init__.py': digest(source)}
            for profile in (1, 2):
                control = root / str(profile)
                control.mkdir()
                pins = prepare_control(control, hashes,
                    BASE / 'block_fixtures/operator_launcher.py', profile)
                config = validate(core, control, pins['core_manifest_sha256'], pins['control_sha256'])
                self.assertEqual(config['schema'], profile)
                self.assertEqual(len(config['allowed_operations']), 2 if profile == 1 else 4)
                variants = [dict(config, schema=True), dict(config, schema=3),
                            dict(config, db_path='/outro.sqlite'), dict(config, approved=True),
                            dict(config, allowed_operations=config['allowed_operations'] + ['EXECUTE'])]
                for changed in variants:
                    with self.subTest(profile=profile, config=changed):
                        raw = canonical(changed)
                        (control / 'config.json').write_bytes(raw)
                        with self.assertRaises(ValueError):
                            validate(core, control, pins['core_manifest_sha256'], digest(raw))

    def test_previous_seals_keep_their_frozen_identity(self):
        from policy_seal import verify_seal as policy_verify
        from seal_version import verify_seal as first_verify
        first_verify(BASE / 'seals/v1', 'sha256:e6056d5bec095d4023b1a30286dc978d000e3d0b3e8ab19f4642da8c84bab704')
        policy_verify(BASE / 'seals/v2', block_seal.PREVIOUS)
        for generation, pin in ((3, block_seal.V3_PIN), (4, block_seal.V4_PIN), (5, block_seal.V5_PIN)):
            manifest = block_seal.verify_seal(BASE / f'seals/v{generation}', pin)
            self.assertEqual(manifest['body']['generation'], generation)
            self.assertEqual(set(manifest['body']['files']), set(block_seal.SOURCE_FILES))

    def test_next_draft_separates_new_acceptance_from_frozen_tests(self):
        with tempfile.TemporaryDirectory(prefix='block-integration-', dir=BASE) as name:
            path = Path(name) / 'seal'
            manifest = block_seal.create_seal(BASE.parents[1], path)
            self.assertEqual(manifest['body']['generation'], 6)
            self.assertEqual(manifest['body']['previous_at_version'], block_seal.V5_PIN)
            self.assertEqual({p.name for p in (path / 'tests').iterdir()}, {'test_unblock.py', 'coverage.json'})
            self.assertEqual(json.loads((path / 'tests/coverage.json').read_text()),
                             json.loads((BASE / 'unblock_acceptance/coverage.json').read_text()))
            body = manifest['body']
            body['generation'] = 5
            (path / 'manifest.json').write_bytes(canonical(manifest))
            with self.assertRaises(block_seal.SealError):
                block_seal.verify_seal(path, manifest['at_version'])


class MigrationV3Tests(unittest.TestCase):
    @staticmethod
    def context():
        return dict(schema=2, db_path='/state/udh.sqlite', controller_route='OPERATOR_LOCAL',
                    authorization_rule_version=1, allowed_operations=('MIGRATE_V2', 'BLOCK_PENDING', 'MIGRATE_V3', 'UNBLOCK_RUN'),
                    core_manifest_sha256='a' * 64, control_sha256='b' * 64)

    def test_migration_preserves_block_and_replays_original_version(self):
        from udh.service import CoreService
        from udh.store import SQLiteStore
        with tempfile.TemporaryDirectory(dir=BASE) as directory:
            core = CoreService(SQLiteStore(Path(directory) / 'state.sqlite'))
            context = self.context()
            core.initialize()
            core.migrate()
            run = core.create(' spec ', '1', 'create')
            before = core.block(run.run_id, 0, 'block', ' motivo ', context=context)
            self.assertEqual(core.migrate(3, context=context), {'schema_version': 3, 'migrated': True})
            self.assertEqual(core.migrate(3, context=context), {'schema_version': 3, 'migrated': False})
            self.assertEqual(core.show(run.run_id).to_dict()['schema_version'], 3)
            self.assertEqual(core.block(run.run_id, 0, 'block', ' motivo ', context=context), before)
            new_run = core.create('s', '1', 'new')
            self.assertEqual(core.block(new_run.run_id, 0, 'new-block', 'reason', context=context)['schema_version'], 3)

    def test_v3_reads_complete_journal_and_rejects_wrong_block_reference(self):
        from contextlib import closing
        from udh.domain import CoreError
        from udh.service import CoreService
        from udh.store import SQLiteStore
        import sqlite3
        with tempfile.TemporaryDirectory(dir=BASE) as directory:
            path = Path(directory) / 'state.sqlite'
            core = CoreService(SQLiteStore(path))
            core.initialize()
            core.migrate()
            run = core.create('s', '1', 'create')
            blocked = core.block(run.run_id, 0, 'block', 'motivo', context=self.context())
            core.migrate(3, context=self.context())
            with closing(sqlite3.connect(path)) as db, db:
                event = db.execute("INSERT INTO events (run_id, kind, created_at) VALUES (?, 'RUN_UNBLOCKED', ?)",
                                   (run.run_id, blocked['created_at'])).lastrowid
                db.execute('INSERT INTO unblock_requests VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, 1)',
                           ('unblock', run.run_id, 'liberado', event, blocked['event_sequence'],
                            '0b30261e-3d31-412f-8a9d-4a9d308f4981', 'OPERATOR_LOCAL', 'a' * 64, 'b' * 64))
                db.execute("UPDATE runs SET state = 'PENDING', revision = 2 WHERE id = ?", (run.run_id,))
            shown = core.show(run.run_id).to_dict()
            self.assertEqual((shown['schema_version'], shown['revision'], shown['events'][2]['kind']), (3, 2, 'RUN_UNBLOCKED'))
            self.assertEqual(core.block(run.run_id, 0, 'block', 'motivo', context=self.context()), blocked)
            with closing(sqlite3.connect(path)) as db, db:
                created = db.execute("SELECT sequence FROM events WHERE kind = 'RUN_CREATED'").fetchone()[0]
                db.execute('UPDATE unblock_requests SET blocked_event_sequence = ?', (created,))
            with self.assertRaises(CoreError):
                core.show(run.run_id)

    def test_migration_requires_authorization_and_rejects_v1_and_downgrade(self):
        from udh.domain import CoreError
        from udh.service import CoreService
        from udh.store import SQLiteStore
        from udh.transitions import AuthorizationError
        with tempfile.TemporaryDirectory(dir=BASE) as directory:
            core = CoreService(SQLiteStore(Path(directory) / 'state.sqlite'))
            with self.assertRaises(AuthorizationError):
                core.migrate(3, context=None)
            self.assertFalse(core.store.path.exists())
            core.initialize()
            with self.assertRaises(CoreError):
                core.migrate(3, context=self.context())
            core.migrate()
            core.migrate(3, context=self.context())
            with self.assertRaises(CoreError):
                core.migrate()
            with self.assertRaises(CoreError):
                core.initialize()
