"""Publicação exclusiva da geração 2; preserva o verificador inicial."""
import json
from pathlib import Path
import shutil
import tempfile

from seal_version import SealError, _canonical, _hash, _safe, _publish_exclusive, verify_seal as verify_v1

PREVIOUS = 'sha256:e6056d5bec095d4023b1a30286dc978d000e3d0b3e8ab19f4642da8c84bab704'
BASELINE_COMMIT = 'b26fd969e20a72b8494b815c57e984b279699714'
APPROVAL = 'Preparo dos AT e selo v2 aprovado pelo usuário em 21/10/2026: aprovado'
SOURCE_FILES = {
    'tests/test_policy.py': 'bootstrap/slice1/policy_acceptance/test_policy.py',
    'tests/coverage.json': 'bootstrap/slice1/policy_acceptance/coverage.json',
    **{f'controller/{name}.py': f'bootstrap/slice1/{name}.py' for name in
       ('policy_seal', 'policy_bridge', 'integrate_policy', 'seal_version', 'isolated_process', 'cli_bridge', 'integrate_acceptance')},
}
SPEC_FILES = ('openspec/changes/slice1-policy-query/proposal.md',
              'openspec/changes/slice1-policy-query/acceptance-handoff.md')


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
            or report.get('mode') != 'BASELINE' or report.get('result') != 'PASS'
            or report.get('errors') != [] or report.get('skipped') != []
            or not report.get('temporary_removed')
            or report.get('source_files') != body['files'] or report.get('specs') != body['specs']):
        raise SealError('Baseline não corresponde ao candidato ou às fontes atuais')
    draft = dict(body, review=None, status='DRAFT')
    if report.get('at_version') != 'sha256:' + _hash(_canonical(draft)):
        raise SealError('Baseline executou outro rascunho')
    tests = report.get('coverage', {}).get('tests', {})
    if not tests or any(v.get('baseline_expected') not in ('NEW_FAILURE', 'EXISTING_PASS') for v in tests.values()):
        raise SealError('Classificação independente ausente')
    expected = {name: ('FAIL' if value['baseline_expected'] == 'NEW_FAILURE' else 'PASS')
                for name, value in tests.items()}
    if (report.get('outcomes') != expected or report.get('tests_run') != len(expected)
            or not any(v == 'FAIL' for v in expected.values())
            or not report.get('audit')
            or any(r.get('classification') != 'FUNCTIONAL_EXIT' or not r.get('removed')
                   or r.get('outcome') != 'EXIT' for r in report['audit'])):
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
                or type(body['generation']) is not int or body['generation'] != 2
                or body['previous_at_version'] != PREVIOUS or body['approval'] != APPROVAL
                or body['provenance'] != 'independent-policy-acceptance'):
            raise SealError('Cadeia ou origem inválida')
        if (manifest['at_version'] != expected_at_version
                or expected_at_version != 'sha256:' + _hash(_canonical(body))
                or raw != _canonical(manifest) + b'\n'):
            raise SealError('STALE: identidade divergente')
        for mapping, names in ((body['files'], SOURCE_FILES), (body['specs'], SPEC_FILES)):
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
                if relative not in ('tests', 'controller'):
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
    verify_v1(root / 'bootstrap/slice1/seals/v1', PREVIOUS)
    destination = Path(destination).absolute()
    _safe(destination.parent, directory=True)
    try:
        relative = destination.relative_to(root / 'bootstrap/slice1')
    except ValueError as exc:
        raise SealError('Destino fora do projeto') from exc
    if review is not None:
        if relative.as_posix() != 'seals/v2':
            raise SealError('Publicação exige seals/v2')
    elif not any(part.startswith('policy-integration-') for part in relative.parts):
        raise SealError('Rascunho exige diretório temporário próprio')
    contents = {name: _safe(root / source).read_bytes() for name, source in SOURCE_FILES.items()}
    files = {name: _hash(data) for name, data in contents.items()}
    if review is not None:
        validate_review(review, files)
    body = dict(schema=1, generation=2, previous_at_version=PREVIOUS,
                status='PUBLISHED' if review is not None else 'DRAFT', files=files,
                specs={name: _hash(_safe(root / name).read_bytes()) for name in SPEC_FILES},
                approval=APPROVAL, provenance='independent-policy-acceptance', review=review)
    if review is not None:
        validate_baseline(body)
        evidence = json.loads(_safe(root / 'bootstrap/slice1/evidence/policy-baseline.json').read_bytes())
        if evidence != review['baseline_gate']['report']:
            raise SealError('Artefato de baseline diverge da revisão')
    manifest = {'at_version': 'sha256:' + _hash(_canonical(body)), 'body': body}
    if destination.exists() or destination.is_symlink():
        return verify_seal(destination, manifest['at_version'])
    staging = Path(tempfile.mkdtemp(prefix='.policy-staging-', dir=destination.parent))
    try:
        for name, data in contents.items():
            target = staging / name
            target.parent.mkdir(exist_ok=True)
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
