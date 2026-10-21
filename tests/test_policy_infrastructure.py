"""Verificações complementares da infraestrutura; não são AT do produto."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1] / 'bootstrap/slice1'
sys.path.insert(0, str(BASE))
import policy_seal as seal


class PolicyInfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='policy-integration-', dir=BASE)
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.path = self.folder / 'seal'
        self.manifest = seal.create_seal(BASE.parents[1], self.path)
        self.version = self.manifest['at_version']

    def rewrite(self, path, mutate):
        manifest = json.loads((path / 'manifest.json').read_text())
        mutate(manifest['body'])
        manifest['at_version'] = 'sha256:' + hashlib.sha256(seal._canonical(manifest['body'])).hexdigest()
        (path / 'manifest.json').write_bytes(seal._canonical(manifest) + b'\n')
        return manifest['at_version']

    def test_identity_repeat_and_wrong_pin(self):
        self.assertEqual(self.manifest, seal.create_seal(BASE.parents[1], self.path))
        with self.assertRaises(seal.SealError):
            seal.verify_seal(self.path, 'sha256:' + '0' * 64)

    def test_tampering_missing_extra_symlink_and_chain(self):
        for kind in ('bytes', 'missing', 'extra', 'symlink', 'chain', 'review'):
            with self.subTest(kind=kind):
                path = self.folder / kind
                shutil.copytree(self.path, path)
                target = path / 'tests/coverage.json'
                version = self.version
                if kind == 'bytes':
                    target.write_text('{}')
                elif kind == 'missing':
                    target.unlink()
                elif kind == 'extra':
                    (path / 'extra').write_text('extra')
                elif kind == 'symlink':
                    target.unlink()
                    target.symlink_to(self.path / 'tests/coverage.json')
                elif kind == 'chain':
                    version = self.rewrite(path, lambda body: body.update(previous_at_version=None))
                else:
                    version = self.rewrite(path, lambda body: body.update(status='PUBLISHED', review={}))
                with self.assertRaises(seal.SealError):
                    seal.verify_seal(path, version)

    def test_existing_version_cannot_be_replaced(self):
        staging = self.folder / 'other'
        shutil.copytree(self.path, staging)
        with self.assertRaises(FileExistsError):
            seal._publish_exclusive(staging, self.path)
        self.assertEqual(self.manifest, seal.verify_seal(self.path, self.version))

    def test_publication_requires_review_and_fixed_destination(self):
        with self.assertRaises(seal.SealError):
            seal.create_seal(BASE.parents[1], self.folder / 'published', review={})
        with self.assertRaises(seal.SealError):
            seal.create_seal(BASE.parents[1], BASE / 'seals/v2', review={})

    def test_baseline_must_bind_current_draft_and_exact_outcomes(self):
        body = self.manifest['body']
        report = dict(candidate_ref=seal.BASELINE_COMMIT, mode='BASELINE', result='PASS',
                      errors=[], skipped=[], temporary_removed=True,
                      source_files=body['files'], specs=body['specs'], at_version=self.version,
                      coverage={'tests': {'new': {'baseline_expected': 'NEW_FAILURE'}}},
                      outcomes={'new': 'FAIL'}, tests_run=1,
                      audit=[{'classification': 'FUNCTIONAL_EXIT', 'removed': True, 'outcome': 'EXIT'}])
        def reviewed(value):
            gate = dict(report=value, report_sha256=hashlib.sha256(seal._canonical(value)).hexdigest())
            return dict(body, status='PUBLISHED', review={'baseline_gate': gate})
        seal.validate_baseline(reviewed(report))
        for key, invalid in (('candidate_ref', 'HEAD'), ('at_version', 'sha256:' + '0' * 64),
                             ('outcomes', {'new': 'PASS'}), ('audit', []),
                             ('source_files', {}), ('errors', ['infra'])):
            with self.subTest(key=key), self.assertRaises(seal.SealError):
                seal.validate_baseline(reviewed(dict(report, **{key: invalid})))
