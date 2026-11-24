import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from udh.spec_handoff import SnapshotError, snapshot_digest, validate_spec_snapshot


CHANGE = 'sample-discovery'
REVISION = 'draft.1'
SPEC = b'''## ADDED Requirements

### Requirement: Restore last view
The app SHALL restore the last view after restart.

#### Scenario: Restart after opening a view
- **WHEN** the user restarts the app
- **THEN** the app restores the last view
'''


def artifacts():
    return {'proposal': b'## Why\nKeep the last view.\n',
            'tasks': b'## 1. Restore the view\n- [ ] 1.1 Store the last view\n',
            'specs': {'view': SPEC}}


class SpecHandoffTests(unittest.TestCase):
    def validate(self, cli, docs=None, *, approval=None, revision=REVISION, node_dir=Path(sys.executable).parent):
        docs = artifacts() if docs is None else docs
        if approval is None:
            approval = (CHANGE, revision, snapshot_digest(CHANGE, docs))
        return validate_spec_snapshot(CHANGE, revision, docs, approval=approval, cli_path=cli, node_dir=node_dir)

    def fake_cli(self, validation='', *, version='2.1.1', strict=True, preamble=''):
        tmp = tempfile.TemporaryDirectory(prefix='fake-openspec-')
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'openspec'
        path.write_text(f'#!{sys.executable}\n'
                        'import json, os, sys, time\nfrom pathlib import Path\n'
                        'assert os.environ["OPENSPEC_TELEMETRY"] == "0"\n'
                        'args = sys.argv[1:]\n' + preamble + '\n'
                        f'if args == ["--version"]:\n    print({version!r}); sys.exit()\n'
                        f'if args == ["validate", "--help"]:\n    print({"  --strict  Validates strictly" if strict else "  --strict-mode"!r}); sys.exit()\n'
                        f'assert args == ["validate", {CHANGE!r}, "--type", "change", "--strict", "--no-interactive", "--json"]\n'
                        f'path = Path("openspec/changes/{CHANGE}")\n'
                        'assert (path / "proposal.md").is_file()\n'
                        f'item = {{"id": {CHANGE!r}, "type": "change", "valid": True}}\n'
                        + validation + '\nprint(json.dumps({"items": [item]}))\n', encoding='utf-8')
        path.chmod(0o700)
        return path

    def test_snapshot_identity_and_input_limits(self):
        docs = artifacts()
        docs['specs']['notes'] = SPEC
        files = {'proposal.md': docs['proposal'], 'tasks.md': docs['tasks'],
                 'specs/view/spec.md': SPEC, 'specs/notes/spec.md': SPEC}
        expected = hashlib.sha256(json.dumps(
            [[p, hashlib.sha256(files[p]).hexdigest()] for p in sorted(files)], separators=(',', ':'),
        ).encode('utf-8')).hexdigest()
        self.assertEqual(snapshot_digest(CHANGE, docs), expected)
        reordered = dict(reversed(list(docs.items())))
        reordered['specs'] = dict(reversed(list(docs['specs'].items())))
        self.assertEqual(snapshot_digest(CHANGE, reordered), expected)
        self.assertNotEqual(snapshot_digest(CHANGE, {**docs, 'design': b'Design\n'}), expected)

        for change, payload in (
            ('--all', docs), ('a/../b', docs), (CHANGE, {'specs': docs['specs'], 'tasks': docs['tasks']}),
            (CHANGE, {**docs, 'skip_specs': b'true'}), (CHANGE, {**docs, 'specs': {'../oops': SPEC}}),
            (CHANGE, {**docs, 'specs': {}}), (CHANGE, {**docs, 'proposal': b''}),
            (CHANGE, {**docs, 'proposal': b'\xff'}), (CHANGE, {**docs, 'proposal': b'x' * 65537}),
            (CHANGE, {**docs, 'specs': {f'cap-{i}': SPEC for i in range(17)}}),
            (CHANGE, {**docs, 'specs': {f'cap-{i}': SPEC.ljust(65536) for i in range(4)}}),
            (CHANGE, {**docs, 'specs': {'view': SPEC.replace(b'ADDED Requirements', b'MODIFIED Requirements')}}),
        ):
            with self.subTest(change=change, fields=tuple(payload)), self.assertRaises(SnapshotError):
                snapshot_digest(change, payload)

    def test_rejects_unapproved_snapshot_before_cli(self):
        docs = artifacts()
        digest = snapshot_digest(CHANGE, docs)
        for approval in (None, ('other', REVISION, digest), (CHANGE, 'draft.2', digest), (CHANGE, REVISION, '0' * 64)):
            with self.subTest(approval=approval), patch('udh.spec_handoff.subprocess.Popen') as spawn:
                result = validate_spec_snapshot(CHANGE, REVISION, docs, approval=approval,
                                                cli_path=Path('/missing'), node_dir=Path('/usr/bin'))
                self.assertEqual(result.code, 'APPROVAL_MISMATCH')
                spawn.assert_not_called()

    def test_cli_success_is_bound_to_snapshot(self):
        result = self.validate(self.fake_cli())
        self.assertEqual(result.code, 'VALIDATED')
        self.assertEqual(result.cli_version, '2.1.1')
        self.assertEqual(result.snapshot_sha256, snapshot_digest(CHANGE, artifacts()))
        self.assertEqual((result.change_id, result.revision), (CHANGE, REVISION))

    def test_cli_failures_are_closed(self):
        cases = (
            (Path('openspec'), 'CLI_INCOMPATIBLE'), (Path('/no-udh-openspec'), 'CLI_MISSING'),
            (self.fake_cli(version='1.0.0'), 'CLI_INCOMPATIBLE'),
            (self.fake_cli(strict=False), 'CLI_INCOMPATIBLE'),
            (self.fake_cli('item["valid"] = False'), 'VALIDATION_REJECTED'),
            (self.fake_cli('print(json.dumps({"items": [item]})); sys.exit(1)'), 'VALIDATION_REJECTED'),
            (self.fake_cli('item["id"] = "other"'), 'CLI_OUTPUT_INVALID'),
            (self.fake_cli('item["type"] = "spec"'), 'CLI_OUTPUT_INVALID'),
            (self.fake_cli('item["valid"] = 1'), 'CLI_OUTPUT_INVALID'),
            (self.fake_cli('print("invalid"); sys.exit()'), 'CLI_OUTPUT_INVALID'),
            (self.fake_cli('print(json.dumps({"items": [item, item]})); sys.exit()'), 'CLI_OUTPUT_INVALID'),
            (self.fake_cli('print("x" * 170000); print("y" * 100000, file=sys.stderr)'), 'CLI_OUTPUT_LIMIT'),
        )
        for cli, code in cases:
            with self.subTest(code=code):
                self.assertEqual(self.validate(cli).code, code)
        with patch('udh.spec_handoff._CLI_TIMEOUT_SECONDS', 0.3):
            self.assertEqual(self.validate(self.fake_cli('time.sleep(30)')).code, 'CLI_TIMEOUT')

    def test_rejects_snapshot_mutation_during_validation(self):
        for operation in ('(path / "proposal.md").write_bytes(b"changed")',
                          '(path / "extra.md").touch()', '(path / "tasks.md").unlink()',
                          '(path / "tasks.md").unlink(); (path / "tasks.md").symlink_to("proposal.md")'):
            with self.subTest(operation=operation):
                self.assertEqual(self.validate(self.fake_cli(operation)).code, 'SNAPSHOT_CHANGED')

    @unittest.skipUnless(os.environ.get('UDH_TEST_OPENSPEC'), 'CLI real não configurada para o ensaio complementar')
    def test_real_openspec_accepts_scenario_and_rejects_missing_scenario(self):
        cli = Path(os.environ['UDH_TEST_OPENSPEC'])
        self.assertEqual(self.validate(cli, node_dir=cli.parent).code, 'VALIDATED')
        invalid = artifacts()
        invalid['specs']['view'] = SPEC.split(b'#### Scenario:')[0]
        self.assertEqual(self.validate(cli, invalid, node_dir=cli.parent).code, 'VALIDATION_REJECTED')
