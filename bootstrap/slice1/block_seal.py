"""Publicação exclusiva da geração 6; preserva os contratos congelados das gerações 3 a 5."""
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
V4_PIN = 'sha256:49a07d7f8f4c55daaee039940288a5b17aacae94df623c7ec6ec7638d01c4183'
V4_APPROVAL = 'Correção da publicação IPC sob o contrato instrumentado aprovado; continuação em 22/10/2026'
APPROVAL = 'Correção da corrida observada de enumeração IPC sob o mesmo contrato aprovado'
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
                   ('proposal.md', 'design.md', 'acceptance-handoff.md', 'fault-observation.md', 'observation-publication-fix.md', 'observation-directory-race.md'))

V5_PIN = 'sha256:182d2ba72a7b5dfb5648e650577dcc3bbbbd67048c72a3bf959406ce7dd89ac6'
UNBLOCK_APPROVAL = 'Desbloqueio e preparo protegido aprovados em 22/10/2026'
UNBLOCK_BASELINE_COMMIT = 'ad2bd478fa8a07a06b6e1a1b832b1795fafe1c4d'
UNBLOCK_BASELINE_FILES = {'udh/__init__.py': 'c480e26f289ce2061ce59b3ded500f02b0c475564e1c2ef3b382876935d5cb01', 'udh/__main__.py': '35c2199d6c990d9e0f02f6250929ae6c1088cd9fb340f04cc930f22fa11c9f8e', 'udh/domain.py': '589db31e4aac42c2719bd3d15f1b9be2eb9f94fa30785deb01d3c0f6bd6b8130', 'udh/migrations.py': 'd0cdcba75eb6f470caa69853ad264441a6b8a203b7c47fea583a7c7451f0059f', 'udh/operator.py': '4316e9b3b72375d9fbf389cb85606b4e821b2b399a742e2303745593e1e4a851', 'udh/policy.py': 'a9e67014098b84f0fe8ad4269190c77c7d94ff7d84ceac8fbded321342906d1b', 'udh/service.py': 'e225536da6296dd3de52c0b44eb636e11605859b7e228c9663f659c50c84178f', 'udh/store.py': 'f3545bea6c5ef6e6f7746de60aa87f0cde8e5fbaf6e98aef1fc6ed90994f1420', 'udh/transitions.py': '5e3b16cb4b0e170b46d2e6a204ae78f429c92c9e812bc2fb0368b4bc63f3ec19'}
UNBLOCK_SOURCE_FILES = {name: source for name, source in SOURCE_FILES.items() if not name.startswith('tests/')}
UNBLOCK_SOURCE_FILES['controller/block_fixtures/unblock_probe.py'] = 'bootstrap/slice1/block_fixtures/unblock_probe.py'
UNBLOCK_SOURCE_FILES.update({'tests/test_unblock.py': 'bootstrap/slice1/unblock_acceptance/test_unblock.py',
                             'tests/coverage.json': 'bootstrap/slice1/unblock_acceptance/coverage.json'})
UNBLOCK_SPEC_FILES = tuple('openspec/changes/slice1-unblock-run/' + name for name in ('proposal.md', 'design.md'))

V6_PIN = 'sha256:7196e5f4eea1fc78806efcdbd2b1e231446b2632937d5aff69c4f2f8bf0d9b37'
EVIDENCE_APPROVAL = 'Continuação aprovada: referências e novas saídas de evidências'
EVIDENCE_SOURCE_FILES = dict(UNBLOCK_SOURCE_FILES)
EVIDENCE_SOURCE_FILES.update({f'controller/{name}.py': f'bootstrap/slice1/{name}.py'
    for name in ('evidence_store', 'check_boundary', 'trust_snapshot')})
EVIDENCE_SOURCE_FILES.update({f'controller/fixtures/{name}.py': f'bootstrap/slice1/fixtures/{name}.py'
    for name in ('adversary', 'hang', 'import_effect', 'output_flood', 'positive', 'process_visibility', 'termination')})
EVIDENCE_SPEC_FILES = UNBLOCK_SPEC_FILES + tuple('openspec/changes/slice1-evidence-storage/' + name
    for name in ('proposal.md', 'integration.md'))

# Identidades derivadas dos AT congelados v1, v2 e v5, sem importá-los.
EXTERNAL_METHODS = {'v1': ('CoreStateAcceptance.test_a10_concurrent_divergent_requests',
        'CoreStateAcceptance.test_a10_concurrent_identical_requests',
        'CoreStateAcceptance.test_a1_reinitialization_preserves_run',
        'CoreStateAcceptance.test_a2_incompatible_fixtures_unchanged',
        'CoreStateAcceptance.test_a3_durable_across_processes',
        'CoreStateAcceptance.test_a4_event_failure_rolls_back',
        'CoreStateAcceptance.test_a5_invalid_inputs_and_missing_database',
        'CoreStateAcceptance.test_a6_literal_references',
        'CoreStateAcceptance.test_a7_no_promotion_and_persistent_state_constraint',
        'CoreStateAcceptance.test_a9_idempotence_and_conflicting_payload'),
 'v2': ('PolicyAcceptance.test_p1_read_existing_run',
        'PolicyAcceptance.test_p2_denied_actions',
        'PolicyAcceptance.test_p3_authority_flags_rejected',
        'PolicyAcceptance.test_p3_unknown_actions_are_literal',
        'PolicyAcceptance.test_p4_empty_action_precedes_domain_and_database',
        'PolicyAcceptance.test_p4_incompatible_database_unchanged',
        'PolicyAcceptance.test_p4_invalid_uuid_precedes_unknown_action',
        'PolicyAcceptance.test_p4_missing_database_precedes_unknown_action',
        'PolicyAcceptance.test_p4_unknown_run_precedes_unknown_action',
        'PolicyAcceptance.test_p5_stable_response_and_canonical_hash',
        'PolicyAcceptance.test_regression_v1_metadata'),
 'v5': ('BlockAcceptance.test_b1_persisted_block_and_v2_reader',
        'BlockAcceptance.test_b2_adversary_isolation_and_claims',
        'BlockAcceptance.test_b3_distinct_concurrent_requests',
        'BlockAcceptance.test_b3_equal_concurrent_requests',
        'BlockAcceptance.test_b3_exact_replay_and_payload_conflicts',
        'BlockAcceptance.test_b4_instrumented_interruption_and_replay',
        'BlockAcceptance.test_b5_common_policy_and_unsupported_targets_no_effect',
        'MigrationAcceptance.test_m1_empty_and_populated_preserve_and_replay',
        'MigrationAcceptance.test_m2_invalid_database_unchanged',
        'MigrationAcceptance.test_m2_missing_rejected_without_creation',
        'MigrationAcceptance.test_m3_concurrent_migration',
        'MigrationAcceptance.test_m3_instrumented_interruption_and_replay',
        'MigrationAcceptance.test_r1_v1_formats_and_common_refusal')}
CLI_LAUNCHER_SHA256 = '5f887097c8c313a25f554cc1b6e149d88eeca527ed192b1bd6df8e33bf99bab6'

STUB_FILES = {'_udh_cli.py': '5f887097c8c313a25f554cc1b6e149d88eeca527ed192b1bd6df8e33bf99bab6',
 'udh/__init__.py': '1c4fe51cbe922a5cd54857b5e9bae55165ab41588467deac36e9731fbcc9f915',
 'udh/__main__.py': '627390014aafc963a7454a8e6aadd53ac37a5a292f319cdafc8a899f4d9521b0'}

def source_files(generation):
    return EVIDENCE_SOURCE_FILES if generation == 7 else UNBLOCK_SOURCE_FILES if generation == 6 else SOURCE_FILES

def spec_files(generation):
    return EVIDENCE_SPEC_FILES if generation == 7 else UNBLOCK_SPEC_FILES if generation == 6 else SPEC_FILES[:generation + 1]


def validate_review(review, files):
    if (not isinstance(review, dict) or review.get('status') != 'PASS'
            or not review.get('review_id') or review.get('files') != files
            or review.get('unresolved_findings') != []
            or review.get('baseline_gate', {}).get('status') != 'PASS'
            or review.get('baseline_gate', {}).get('infrastructure_errors') != 0):
        raise SealError('Revisão independente e baseline negativo obrigatórios')


def validate_baseline(body):
    current = body['generation'] == 6
    baseline_commit = UNBLOCK_BASELINE_COMMIT if current else BASELINE_COMMIT
    baseline_files = UNBLOCK_BASELINE_FILES if current else BASELINE_FILES
    gate = body['review']['baseline_gate']
    report = gate.get('report')
    if (not isinstance(report, dict)
            or gate.get('report_sha256') != _hash(_canonical(report))
            or report.get('candidate_ref') != baseline_commit
            or report.get('candidate_hashes') != baseline_files
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
    if (external.get('result') != 'PASS' or external.get('candidate_ref') != baseline_commit
            or external.get('results') != ({'v1':'PASS','v2':'PASS','v5':'PASS','complementary':'PASS','I1_I7_T1_T8':'PASS'} if current else {'v1':'PASS','v2':'PASS','I1_I7_T1_T8':'PASS'})
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
                or type(body['generation']) is not int or body['generation'] not in (3, 4, 5, 6, 7)
                or body['previous_at_version'] != ({3: PREVIOUS, 4: V3_PIN, 5: V4_PIN, 6: V5_PIN, 7: V6_PIN}[body['generation']])
                or body['approval'] != ({3: V3_APPROVAL, 4: V4_APPROVAL, 5: APPROVAL, 6: UNBLOCK_APPROVAL, 7: EVIDENCE_APPROVAL}[body['generation']])
                or body['provenance'] != 'independent-block-acceptance'):
            raise SealError('Cadeia ou origem inválida')
        if (manifest['at_version'] != expected_at_version
                or expected_at_version != 'sha256:' + _hash(_canonical(body))
                or raw != _canonical(manifest) + b'\n'):
            raise SealError('STALE: identidade divergente')
        if body['generation'] == 7 and len(raw) > 128 * 1024:
            raise SealError('Manifesto7 excede o limite.')
        for mapping, names in ((body['files'], source_files(body['generation'])), (body['specs'], spec_files(body['generation']))):
            if not isinstance(mapping, dict) or set(mapping) != set(names):
                raise SealError('Lista de arquivos inválida')
            if any(not isinstance(v, str) or len(v) != 64 or any(c not in '0123456789abcdef' for c in v) for v in mapping.values()):
                raise SealError('Hash inválido')
        if body['status'] == 'PUBLISHED':
            if body['generation'] == 7:
                raise SealError('Publicação7 indisponível antes dos AT de transporte e exportação.')
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
                allowed = ('tests', 'controller', 'controller/block_fixtures')
                if body['generation'] == 7:
                    allowed += ('controller/fixtures',)
                if relative not in allowed:
                    raise SealError('Diretório extra')
            else:
                _safe(path)
                seen.add(relative)
        if seen != set(source_files(body['generation'])) | {'manifest.json'}:
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


def create_seal(root, destination, review=None, *, generation=6):
    root = _safe(Path(root), directory=True)
    if type(generation) is not int or generation not in (6, 7):
        raise SealError('Geração de selo inválida.')
    verify_v1(root / 'bootstrap/slice1/seals/v1', 'sha256:e6056d5bec095d4023b1a30286dc978d000e3d0b3e8ab19f4642da8c84bab704')
    verify_v2(root / 'bootstrap/slice1/seals/v2', PREVIOUS)
    for number, pin in ((3, V3_PIN), (4, V4_PIN), (5, V5_PIN), (6, V6_PIN)):
        frozen = verify_seal(root / f'bootstrap/slice1/seals/v{number}', pin)
    destination = Path(destination).absolute()
    _safe(destination.parent, directory=True)
    try:
        relative = destination.relative_to(root / 'bootstrap/slice1')
    except ValueError as exc:
        raise SealError('Destino fora do projeto') from exc
    if review is not None:
        if generation == 7:
            raise SealError('Publicação7 indisponível antes dos AT de transporte e exportação.')
        if relative.as_posix() != 'seals/v6' or review != frozen['body']['review']:
            raise SealError('Geração6 só permite verificar publicação congelada idêntica.')
        return verify_seal(destination, V6_PIN)
    if not any(part.startswith('block-integration-') for part in relative.parts):
        raise SealError('Rascunho exige diretório temporário próprio')
    if generation == 6:
        body = dict(frozen['body'], status='DRAFT', review=None)
        contents = {name: _safe(root / 'bootstrap/slice1/seals/v6' / name).read_bytes()
                    for name in body['files']}
    else:
        contents = {name: _safe(root / source).read_bytes() for name, source in EVIDENCE_SOURCE_FILES.items()}
        body = dict(schema=1, generation=7, previous_at_version=V6_PIN, status='DRAFT',
                    files={name: _hash(data) for name, data in contents.items()},
                    specs={name: _hash(_safe(root / name).read_bytes()) for name in EVIDENCE_SPEC_FILES},
                    approval=EVIDENCE_APPROVAL, provenance='independent-block-acceptance', review=None)
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


def validate_referenced_baseline(body, store, reference, *, coverage_bytes):
    """Valida coleta7 de um corpo já verificado; não publica selo nem aceita produto."""
    try:
        if body['generation'] != 7 or type(body['generation']) is not int:
            raise SealError('Gate referenciado exige geração7.')
        if _hash(coverage_bytes) != body['files']['tests/coverage.json']:
            raise SealError('Cobertura não corresponde aos bytes selados.')
        coverage = json.loads(coverage_bytes)
        store.validate(reference)
        envelope = store.read(reference)
        if envelope['kind'] != 'report':
            raise SealError('Baseline não aponta para relatório.')
        report, links = envelope['payload'], envelope['links']
        draft = dict(body, status='DRAFT', review=None)
        if (report.get('candidate_ref') != UNBLOCK_BASELINE_COMMIT
                or report.get('candidate_hashes') != UNBLOCK_BASELINE_FILES
                or report.get('at_version') != 'sha256:' + _hash(_canonical(draft))
                or report.get('execution_at_version') != V6_PIN
                or report.get('source_files') != body['files'] or report.get('specs') != body['specs']
                or report.get('coverage') != coverage or report.get('mode') != 'BASELINE'
                or report.get('collection_layout') != 'flat'
                or report.get('baseline_discrimination') != 'PASS' or report.get('result') != 'PASS'
                or report.get('errors') != [] or report.get('skipped') != []
                or report.get('temporary_removed') is not True):
            raise SealError('Baseline diverge da identidade, fontes ou resultados esperados.')
        methods = coverage['methods']
        if (len(methods) != 16
                or sum(item.get('baseline_expected') == 'NEW_FAILURE' for item in methods.values()) != 13
                or sum(item.get('baseline_expected') == 'EXISTING_PASS' for item in methods.values()) != 3):
            raise SealError('Cobertura funcional não conserva os16 métodos do v6.')
        expected = {name: 'FAIL' if item['baseline_expected'] == 'NEW_FAILURE' else 'PASS'
                    for name, item in methods.items()}
        if (type(report.get('tests_run')) is not int or report['tests_run'] != 16
                or report.get('outcomes') != expected
                or report.get('failed_methods') != sorted(name for name, outcome in expected.items() if outcome == 'FAIL')):
            raise SealError('Resultados não discriminam o baseline funcional completo.')
        pending = coverage.get('pending', {})
        if (any(name not in ('R1', 'R2') or item.get('blocks_functional_execution', True)
                for name, item in pending.items()) or report.get('external_checks') != {'R1': 'PASS'}):
            raise SealError('Pendência ou regressão externa não satisfeita.')
        audit = store.read(links['audit'])['payload']
        if not audit:
            raise SealError('Baseline sem auditoria.')
        for row in audit:
            _checked_audit(row)
        del audit
        external = store.read(links['regression'])
        names = ('v1', 'v2', 'v5', 'complementary', 'i1_i7_t1_t8')
        if (external['kind'] != 'regression'
                or external['payload'].get('candidate_ref') != UNBLOCK_BASELINE_COMMIT
                or external['payload'].get('result') != 'PASS'
                or external['payload'].get('results') != {name: 'PASS' for name in names}
                or set(external['links']) != set(names)):
            raise SealError('Regressão referenciada não corresponde ao candidato.')
        for name, child in external['links'].items():
            _validate_external(name, store, child, files=body['files'])
        return dict(status='PASS', tests_run=16, infrastructure_errors=0)
    except (ValueError, TypeError, KeyError, AttributeError, OSError, MemoryError, RecursionError) as error:
        if isinstance(error, SealError):
            raise
        raise SealError('Baseline referenciado incompleto ou inválido.') from None


def _validate_external(name, store, reference, *, files=None):
    entry = store.read(reference)
    payload = entry['payload']
    if payload.get('result') != 'PASS':
        raise SealError('Relatório externo incompatível com resumo PASS.')
    if name == 'i1_i7_t1_t8':
        _checked_infrastructure(store, entry, files)
        return
    count = payload.get('tests_run')
    if (type(count) is not int or payload.get('errors') != [] or payload.get('skipped') != []
            or payload.get('failures') != []):
        raise SealError('Regressão externa contém testes incompletos.')
    if name == 'complementary':
        if count < 39:
            raise SealError('Regressão complementar não conserva os39 testes anteriores.')
        return
    hashes = dict(UNBLOCK_BASELINE_FILES)
    if name in ('v1', 'v2'):
        hashes['_udh_cli.py'] = CLI_LAUNCHER_SHA256
    if (payload.get('candidate_hashes') != hashes
            or payload.get('at_version') != {'v1': 'sha256:e6056d5bec095d4023b1a30286dc978d000e3d0b3e8ab19f4642da8c84bab704', 'v2': PREVIOUS, 'v5': V5_PIN}[name]
            or payload.get('temporary_removed') is not True
            or count != {'v1': 10, 'v2': 11, 'v5': 13}[name]
            or payload.get('candidate_ref') != UNBLOCK_BASELINE_COMMIT
            or type(payload.get('outcomes')) is not dict or set(payload['outcomes']) != set(EXTERNAL_METHODS[name])
            or any(outcome != 'PASS' for outcome in payload['outcomes'].values())):
        raise SealError('Regressão funcional divergente do candidato ou resultados.')
    audit = store.read(entry['links']['audit'])['payload']
    if not audit:
        raise SealError('Regressão externa sem auditoria.')
    for row in audit:
        _checked_audit(row)


def _checked_audit(row):
    classification, outcome = row.get('classification'), row.get('outcome')
    if (row.get('removed') is not True
            or classification not in ('FUNCTIONAL_EXIT', 'OBSERVATION', 'ADVERSARY_CHECK')
            or outcome not in ('EXIT', 'OBSERVED_INTERRUPT')
            or classification != 'OBSERVATION' and outcome != 'EXIT'
            or outcome == 'EXIT' and (type(row.get('returncode')) is not int or row['returncode'] in (124, 125))):
        raise SealError('Auditoria contém infraestrutura ou execução não comprovada.')
    if outcome == 'OBSERVED_INTERRUPT':
        raw, evidence = row.get('raw_observation', {}), row.get('evidence', {})
        proof = raw.get('observation_proof', {})
        marker = proof.get('marker', {})
        if (not all(raw.get(key) is True for key in ('marker_received', 'process_killed', 'removed', 'integrity_verified'))
                or not all(evidence.get(key) is True for key in ('marker_received', 'process_killed', 'runtime_removed'))
                or evidence.get('proof') != proof or proof.get('kill', {}).get('returncode') != 0
                or not proof.get('inspected_container_id')
                or marker.get('container_name') != row.get('container_name')
                or not marker.get('nonce') or marker.get('checkpoint') != raw.get('checkpoint')
                or evidence.get('checkpoint') != raw.get('checkpoint')):
            raise SealError('Interrupção observada sem prova correspondente.')


def _checked_infrastructure(store, entry, files):
    payload, links = entry['payload'], entry['links']
    criteria, baseline = payload.get('criteria', {}), payload.get('baseline', {})
    review = payload.get('infrastructure_review', {})
    components = ('trust_snapshot', 'isolated_process', 'check_boundary', 'integrate_acceptance',
                  'evidence_store', 'cli_bridge', 'seal_version')
    expected_sources = {EVIDENCE_SOURCE_FILES['controller/' + name + '.py']: files['controller/' + name + '.py']
                        for name in components} if files else {}
    for target, source in EVIDENCE_SOURCE_FILES.items():
        if target.startswith('controller/fixtures/') and files:
            expected_sources[source] = files[target]
    if (not expected_sources or review.get('files') != expected_sources or review.get('status') != 'PASS'
            or review.get('unresolved_findings') != [] or not review.get('reviewer_context')
            or review.get('host_does_not_import_candidate') is not True
            or payload.get('collection_layout') != 'integration' or payload.get('temporary_removed') is not True
            or payload.get('at_version') != 'sha256:e6056d5bec095d4023b1a30286dc978d000e3d0b3e8ab19f4642da8c84bab704'
            or set(criteria) != {f'I{index}' for index in range(1, 8)}
            or any(value.get('status') != 'PASS' for value in criteria.values())
            or type(baseline.get('tests_run')) is not int or baseline['tests_run'] != 10
            or baseline.get('candidate_reference') != 'dc848ba' or baseline.get('candidate_hashes') != STUB_FILES
            or baseline.get('errors') != []
            or len(baseline.get('failures', [])) != 18
            or any(baseline.get(name) != 0 for name in ('skipped', 'expected_failures', 'unexpected_successes'))):
        raise SealError('Integração I/T sem identidade, baseline ou revisão completa.')
    failed = {item['test'].split(' (')[0].split('.')[-1] for item in baseline['failures']}
    if failed != {name.split('.')[-1] for name in EXTERNAL_METHODS['v1']}:
        raise SealError('Baseline I/T não discrimina todos os métodos do stub.')
    audit = store.read(links['audit'])['payload']
    if not audit:
        raise SealError('Integração I/T sem auditoria.')
    for row in audit:
        _checked_audit(row)
        if row.get('classification') != 'FUNCTIONAL_EXIT':
            raise SealError('Baseline I/T não é funcional.')
    del audit
    regression = store.read(links['regression'])
    if regression['payload'].get('result') != 'PASS' or set(regression['links']) != {'boundary'}:
        raise SealError('Fronteira não vinculada à integração.')
    boundary = store.read(regression['links']['boundary'])
    metadata = boundary['payload']
    criteria = metadata.get('criteria', {})
    if (metadata.get('result') != 'PASS' or metadata.get('collection_layout') != 'boundary'
            or metadata.get('temporary_store_removed') is not True or metadata.get('source_reference') != 'dc848ba'
            or set(criteria) != {f'T{index}' for index in range(1, 9)}
            or any(value.get('status') != 'PASS' for value in criteria.values())
            or criteria['T3'].get('review') != review):
        raise SealError('Critérios T1–T8 ou revisão de fronteira incompletos.')
    runs = store.read(boundary['links']['audit'])['payload']
    expected = dict(adversary='EXIT', **{'explicit-entrypoint': 'EXIT', 'timeout': 'TIMEOUT',
        'output-limit': 'OUTPUT_LIMIT', 'contained-signal': 'TERMINATION_UNKNOWN',
        'explicit-high-exit': 'TERMINATION_UNKNOWN', 'namespaces': 'EXIT', 'positive': 'EXIT'})
    if len(runs) != len(expected) or {row.get('label') for row in runs} != set(expected):
        raise SealError('Auditoria de fronteira incompleta.')
    for item in runs:
        row = item.get('run', {})
        if (row.get('outcome') != expected[item['label']] or row.get('removed') is not True
                or not row.get('command') or not row.get('container_name')
                or not row.get('inspection', {}).get('Id')
                or row.get('cleanup', {}).get('exists', {}).get('returncode') != 1):
            raise SealError('Execução de fronteira sem prova de runtime e limpeza.')
