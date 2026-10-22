"""Publicação exclusiva da geração 4; verifica também o contrato congelado da geração 3."""
import json
from pathlib import Path
import shutil
import tempfile

from seal_version import SealError, _canonical, _hash, _safe, _publish_exclusive, verify_seal as verify_v1

from policy_seal import verify_seal as verify_v2

PREVIOUS = 'sha256:438ce43f70e0137115f2c30920623eabf5bfd042035958951fdaafa151e41f3f'
BASELINE_COMMIT = '966036bb67fd36301ab9f638949ad31d13717bc1'
BASELINE_FILES = {'udh/__init__.py': 'c480e26f289ce2061ce59b3ded500f02b0c475564e1c2ef3b382876935d5cb01', 'udh/__main__.py': '35c2199d6c990d9e0f02f6250929ae6c1088cd9fb340f04cc930f22fa11c9f8e', 'udh/domain.py': 'da32490a19dd0d3d5b3611d3fff4116297de3f0aee3ef652fb944a1d0be9c16a', 'udh/policy.py': 'a9e67014098b84f0fe8ad4269190c77c7d94ff7d84ceac8fbded321342906d1b', 'udh/service.py': '38b5419f7a906e0e4e6300e35ac2cbc627ea7e5e1cf8d91ea5471b2ccfebfcf0', 'udh/store.py': '9e65b7fb9d0efac8a23407d9171c4a7cd19da9a58ba73a7ab0b1c46fc44dae20'}
V3_PIN = 'sha256:d1362b119315a504976adf573f802fd7b3e4b045e03554a1432fc43c76cf3e0a'
V3_APPROVAL = 'Preparo dos AT e infraestrutura de bloqueio aprovado em 21/10/2026'
APPROVAL = 'Correção da publicação IPC sob o contrato instrumentado aprovado; continuação em 22/10/2026'
SOURCE_FILES = {
    'tests/test_migration.py': 'bootstrap/slice1/block_acceptance/test_migration.py',
    'tests/test_block.py': 'bootstrap/slice1/block_acceptance/test_block.py',
    'tests/coverage.json': 'bootstrap/slice1/block_acceptance/coverage.json',
    'controller/block_fixtures/operator_launcher.py': 'bootstrap/slice1/block_fixtures/operator_launcher.py',
    'controller/block_fixtures/fault_launcher.py': 'bootstrap/slice1/block_fixtures/fault_launcher.py',
    'controller/block_fixtures/adversary_probe.py': 'bootstrap/slice1/block_fixtures/adversary_probe.py',
    **{f'controller/{name}.py': f'bootstrap/slice1/{name}.py' for name in
       ('operator_process', 'operator_bridge', 'block_seal', 'integrate_block',
        'policy_seal', 'seal_version', 'isolated_process', 'cli_bridge',
        'integrate_policy', 'integrate_acceptance')},
}
SPEC_FILES = tuple('openspec/changes/slice1-block-run/' + name for name in
                   ('proposal.md', 'design.md', 'acceptance-handoff.md', 'fault-observation.md', 'observation-publication-fix.md'))


def validate_review(review, files):
    if (not isinstance(review, dict) or review.get('status') != 'PASS'
            or not review.get('review_id') or review.get('files') != files
            or review.get('unresolved_findings') != []
            or review.get('baseline_gate', {}).get('status') != 'PASS'
            or review.get('baseline_gate', {}).get('infrastructure_errors') != 0):
        raise SealError('Revisão independente e baseline negativo obrigatórios')


def validate_baseline(body):
    gate = body['review']['baseline_gate']
    report = gate.get('report')
    if (not isinstance(report, dict)
            or gate.get('report_sha256') != _hash(_canonical(report))
            or report.get('candidate_ref') != BASELINE_COMMIT
            or report.get('candidate_hashes') != BASELINE_FILES
            or report.get('baseline_discrimination') != 'PASS'
            or report.get('mode') != 'BASELINE' or report.get('result') != 'PASS'
            or report.get('errors') != [] or report.get('skipped') != []
            or not report.get('temporary_removed')
            or report.get('source_files') != body['files'] or report.get('specs') != body['specs']):
        raise SealError('Baseline não corresponde ao candidato ou às fontes atuais')
    draft = dict(body, review=None, status='DRAFT')
    if report.get('at_version') != 'sha256:' + _hash(_canonical(draft)):
        raise SealError('Baseline executou outro rascunho')
    pending = report.get('coverage', {}).get('pending', {})
    if any(name not in ('R1', 'R2') or item.get('blocks_functional_execution', True)
           for name, item in pending.items()):
        raise SealError('Cobertura incompleta bloqueia publicação')
    if pending and report.get('external_checks', {}).get('R1') != 'PASS':
        raise SealError('Regressão externa obrigatória')
    external = report.get('external_evidence', {})
    if (external.get('result') != 'PASS' or external.get('candidate_ref') != BASELINE_COMMIT
            or external.get('results') != {'v1':'PASS','v2':'PASS','I1_I7_T1_T8':'PASS'}
            or report.get('external_evidence_sha256') != _hash(_canonical(external) + b'\n')):
        raise SealError('Evidência externa não corresponde à regressão')
    tests = report.get('coverage', {}).get('methods', {})
    if not tests or any(v.get('baseline_expected') not in ('NEW_FAILURE', 'EXISTING_PASS') for v in tests.values()):
        raise SealError('Classificação independente ausente')
    expected = {name: ('FAIL' if value['baseline_expected'] == 'NEW_FAILURE' else 'PASS')
                for name, value in tests.items()}
    if (report.get('outcomes') != expected or report.get('tests_run') != len(expected)
            or not any(v == 'FAIL' for v in expected.values())
            or not report.get('audit')
            or any(r.get('classification') not in ('FUNCTIONAL_EXIT','ADVERSARY_CHECK','OBSERVATION')
                   or not r.get('removed') or r.get('outcome') not in ('EXIT','OBSERVED_INTERRUPT')
                   for r in report['audit'])
            or report.get('failed_methods') != sorted(name for name, value in expected.items() if value == 'FAIL')):
        raise SealError('Baseline sem cobertura exata ou com falha de infraestrutura')


def verify_seal(destination, expected_at_version):
    destination = _safe(Path(destination), directory=True)
    try:
        raw = _safe(destination / 'manifest.json').read_bytes()
        manifest = json.loads(raw)
        if set(manifest) != {'at_version', 'body'}:
            raise SealError('Manifesto inválido')
        body = manifest['body']
        if set(body) != {'schema', 'generation', 'previous_at_version', 'status', 'files', 'specs', 'approval', 'provenance', 'review'}:
            raise SealError('Corpo inválido')
        if (type(body['schema']) is not int or body['schema'] != 1
                or type(body['generation']) is not int or body['generation'] not in (3, 4)
                or body['previous_at_version'] != (PREVIOUS if body['generation'] == 3 else V3_PIN)
                or body['approval'] != (V3_APPROVAL if body['generation'] == 3 else APPROVAL)
                or body['provenance'] != 'independent-block-acceptance'):
            raise SealError('Cadeia ou origem inválida')
        if (manifest['at_version'] != expected_at_version
                or expected_at_version != 'sha256:' + _hash(_canonical(body))
                or raw != _canonical(manifest) + b'\n'):
            raise SealError('STALE: identidade divergente')
        for mapping, names in ((body['files'], SOURCE_FILES), (body['specs'], SPEC_FILES[:-1] if body['generation'] == 3 else SPEC_FILES)):
            if not isinstance(mapping, dict) or set(mapping) != set(names):
                raise SealError('Lista de arquivos inválida')
            if any(not isinstance(v, str) or len(v) != 64 or any(c not in '0123456789abcdef' for c in v) for v in mapping.values()):
                raise SealError('Hash inválido')
        if body['status'] == 'PUBLISHED':
            validate_review(body['review'], body['files'])
            validate_baseline(body)
        elif body['status'] != 'DRAFT' or body['review'] is not None:
            raise SealError('Estado inválido')
        seen = set()
        for path in destination.rglob('*'):
            if path.is_symlink():
                raise SealError('Symlink recusado')
            relative = path.relative_to(destination).as_posix()
            if path.is_dir():
                if relative not in ('tests', 'controller', 'controller/block_fixtures'):
                    raise SealError('Diretório extra')
            else:
                _safe(path)
                seen.add(relative)
        if seen != set(SOURCE_FILES) | {'manifest.json'}:
            raise SealError('Arquivos ausentes ou extras')
        for name, digest in body['files'].items():
            if _hash(_safe(destination / name).read_bytes()) != digest:
                raise SealError('STALE: arquivo alterado')
        if body['status'] == 'PUBLISHED' and json.loads((destination / 'tests/coverage.json').read_bytes()) != body['review']['baseline_gate']['report']['coverage']:
            raise SealError('Cobertura do baseline diverge dos AT selados')
        return manifest
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, SealError):
            raise
        raise SealError('Selo inválido') from exc


def create_seal(root, destination, review=None):
    root = _safe(Path(root), directory=True)
    verify_v1(root / 'bootstrap/slice1/seals/v1', 'sha256:e6056d5bec095d4023b1a30286dc978d000e3d0b3e8ab19f4642da8c84bab704')
    verify_v2(root / 'bootstrap/slice1/seals/v2', PREVIOUS)
    verify_seal(root / 'bootstrap/slice1/seals/v3', V3_PIN)
    destination = Path(destination).absolute()
    _safe(destination.parent, directory=True)
    try:
        relative = destination.relative_to(root / 'bootstrap/slice1')
    except ValueError as exc:
        raise SealError('Destino fora do projeto') from exc
    if review is not None:
        if relative.as_posix() != 'seals/v4':
            raise SealError('Publicação exige seals/v4')
    elif not any(part.startswith('block-integration-') for part in relative.parts):
        raise SealError('Rascunho exige diretório temporário próprio')
    contents = {name: _safe(root / source).read_bytes() for name, source in SOURCE_FILES.items()}
    files = {name: _hash(data) for name, data in contents.items()}
    if review is not None:
        validate_review(review, files)
    body = dict(schema=1, generation=4, previous_at_version=V3_PIN,
                status='PUBLISHED' if review is not None else 'DRAFT', files=files,
                specs={name: _hash(_safe(root / name).read_bytes()) for name in SPEC_FILES},
                approval=APPROVAL, provenance='independent-block-acceptance', review=review)
    if review is not None:
        validate_baseline(body)
        evidence = json.loads(_safe(root / 'bootstrap/slice1/evidence/block-baseline.json').read_bytes())
        if evidence != review['baseline_gate']['report']:
            raise SealError('Artefato de baseline diverge da revisão')
        external = json.loads(_safe(root / 'bootstrap/slice1/evidence/block-regression.json').read_bytes())
        if external != evidence['external_evidence']:
            raise SealError('Artefato de regressão diverge da revisão')
    manifest = {'at_version': 'sha256:' + _hash(_canonical(body)), 'body': body}
    if destination.exists() or destination.is_symlink():
        return verify_seal(destination, manifest['at_version'])
    staging = Path(tempfile.mkdtemp(prefix='.block-staging-', dir=destination.parent))
    try:
        for name, data in contents.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (staging / 'manifest.json').write_bytes(_canonical(manifest) + b'\n')
        verify_seal(staging, manifest['at_version'])
        try:
            _publish_exclusive(staging, destination)
        except FileExistsError:
            return verify_seal(destination, manifest['at_version'])
        return verify_seal(destination, manifest['at_version'])
    finally:
        if staging.exists():
            shutil.rmtree(staging)
