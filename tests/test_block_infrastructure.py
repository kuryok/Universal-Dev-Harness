"""Complementares da fronteira administrativa; fixtures sintéticas, não AT do Core."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1] / 'bootstrap/slice1'
sys.path.insert(0, str(BASE))
import block_seal
from integrate_block import canonical, digest
from operator_process import OperatorProcess


class BlockInfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='block-integration-', dir=BASE)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def synthetic(self):
        core, state, control = (self.root / name for name in ('core','state','control'))
        for path in (core,state,control):
            path.mkdir()
        (core/'udh').mkdir()
        (core/'udh/__init__.py').write_text('')
        (core/'udh/operator.py').write_text('''import sqlite3

def main(argv=None, *, context=None):
    connection = sqlite3.connect(context['db_path'])
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('INSERT INTO marks VALUES (1)')
        connection.execute('INSERT INTO marks VALUES (2)')
        connection.commit()
    finally:
        connection.close()
    return 0
''')
        hashes = {str(path.relative_to(core)): digest(path.read_bytes()) for path in core.rglob('*.py')}
        manifest = canonical({'schema':1,'files':hashes})
        manifest_pin = digest(manifest)
        config = canonical(dict(schema=1,db_path='/state/udh.sqlite',
            allowed_operations=['MIGRATE_V2','BLOCK_PENDING'],core_manifest_sha256=manifest_pin))
        config_pin = digest(config)
        launcher = (BASE/'block_fixtures/operator_launcher.py').read_bytes().replace(
            b'__CORE_MANIFEST_SHA256__', manifest_pin.encode()).replace(b'__CONTROL_SHA256__',config_pin.encode())
        for name,raw in (('manifest.json',manifest),('config.json',config),('operator_launcher.py',launcher)):
            (control/name).write_bytes(raw)
        with closing(sqlite3.connect(state/'udh.sqlite')) as connection, connection:
            connection.execute('CREATE TABLE marks(value INTEGER)')
        pins=dict(core_manifest_sha256=manifest_pin,control_sha256=config_pin,launcher_sha256=digest(launcher))
        return core,state,control,pins

    def test_configuration_and_snapshot_tampering_rejected(self):
        core,state,control,pins=self.synthetic()
        OperatorProcess(core,state,control,**pins)
        original=(control/'config.json').read_bytes()
        (control/'config.json').write_text('{}')
        with self.assertRaises(ValueError):
            OperatorProcess(core,state,control,**pins)
        (control/'config.json').write_bytes(original)
        (core/'udh/__init__.py').write_text('# bytes diferentes\n')
        with self.assertRaises(ValueError):
            OperatorProcess(core,state,control,**pins)

    def test_observed_interruption_recovers_transaction_and_commit(self):
        core,state,control,pins=self.synthetic()
        runtime=OperatorProcess(core,state,control,**pins)
        rows=lambda: self.read_marks(state)
        discovery=runtime.run_fault(['migrate','--to','2'],timeout=15)
        self.assertEqual(discovery['outcome'],'EXIT',discovery)
        self.assertEqual(discovery['returncode'],0)
        self.assertEqual([x['checkpoint'] for x in discovery['checkpoints']],
                         ['BEFORE_WRITE:1','BEFORE_WRITE:2','AFTER_COMMIT:1'])
        self.assertEqual(rows(),[(1,),(2,)])
        with closing(sqlite3.connect(state/'udh.sqlite')) as connection, connection:
            connection.execute('DELETE FROM marks')
        observations = [discovery]
        for checkpoint,expected in (('BEFORE_WRITE:2',[]),('AFTER_COMMIT:1',[(1,),(2,)])):
            with self.subTest(checkpoint=checkpoint):
                result=runtime.run_fault(['migrate','--to','2'],checkpoint=checkpoint,timeout=15)
                self.assertEqual(result['outcome'],'OBSERVED_INTERRUPT',result)
                self.assertTrue(result['marker_received'] and result['process_killed'] and result['removed'])
                self.assertTrue(result['observation_proof']['inspected_container_id'])
                self.assertEqual(rows(),expected)
                observations.append(result)

        (BASE/'evidence/block-instrumentation.json').write_text(json.dumps(dict(
            result='PASS', experiment='synthetic-sqlite-writer', core_is_product=False,
            observations=observations),ensure_ascii=False,indent=2)+'\n')

    def read_marks(self,state):
        # Leitor confiável abre RW para recuperar eventual hot journal do teste.
        with closing(sqlite3.connect(state/'udh.sqlite')) as connection:
            return connection.execute('SELECT value FROM marks ORDER BY value').fetchall()

    def test_draft_identity_and_tampering(self):
        path=self.root/'seal'
        manifest=block_seal.create_seal(BASE.parents[1],path)
        self.assertEqual(block_seal.verify_seal(path,manifest['at_version']),manifest)
        self.assertEqual(block_seal.create_seal(BASE.parents[1],path),manifest)
        for variant in ('bytes','extra','symlink','missing'):
            with self.subTest(variant=variant):
                copy=self.root/variant
                shutil.copytree(path,copy)
                target=copy/'tests/coverage.json'
                if variant=='bytes':
                    target.write_text('{}')
                elif variant=='extra':
                    (copy/'extra').write_text('extra')
                elif variant=='missing':
                    target.unlink()
                else:
                    target.unlink();target.symlink_to(path/'tests/coverage.json')
                with self.assertRaises(block_seal.SealError):
                    block_seal.verify_seal(copy,manifest['at_version'])

    def test_publication_rejects_missing_independent_review(self):
        with self.assertRaises(block_seal.SealError):
            block_seal.create_seal(BASE.parents[1],BASE/'seals/v3',review={})
