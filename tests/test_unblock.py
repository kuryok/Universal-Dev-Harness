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
