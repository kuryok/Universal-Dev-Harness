"""Vínculos verificáveis de aceite por ticket, sem efeitos sobre a entrega."""

import re

from .domain import CoreError, require_sha1, require_sha256
from .transitions import ConflictError


RULE_VERSION = 'managed-ticket-acceptance-v1'
_REVIEW_KEYS = ('independent_context', 'review_context_ref', 'confirmation_ref', 'findings')


def _identifier(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', value):
        raise CoreError('Referência ou identificador de revisão inválido.')
    return value


def review_details(*, independent_context, review_context_ref, confirmation_ref, findings):
    if independent_context is not True:
        raise ConflictError('O aceite requer revisão em contexto independente.')
    if type(findings) is not list or len(findings) > 64:
        raise CoreError('Lista de achados inválida.')
    resolved = {}
    for finding in findings:
        if type(finding) is not dict or set(finding) != {'id', 'severity', 'resolution'}:
            raise CoreError('Achado de revisão fora do contrato.')
        fid = _identifier(finding['id'])
        resolution = finding['resolution']
        if (fid in resolved or finding['severity'] not in ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
                or type(resolution) is not str or not resolution.strip() or len(resolution) > 512):
            raise ConflictError('O aceite requer achados identificados e resolvidos, sem dispensas.')
        resolved[fid] = dict(finding)
    return dict(independent_context=True, review_context_ref=_identifier(review_context_ref),
                confirmation_ref=_identifier(confirmation_ref), findings=[resolved[k] for k in sorted(resolved)])


def completion_evidence(run, contract, at_id):
    if run.state != 'DONE' or run.has_unknown_pendencies:
        raise ConflictError('Aceite exige execução DONE sem pendência UNKNOWN.')
    at = next((ev for ev in run.evidence_refs if ev.evidence_id == at_id), None)
    if (at is None or at.kind != 'AT_RESULT' or at.verdict != 'PASS' or at.collector_route != 'TRUSTED_EXECUTOR'
            or at.at_version != contract['at_version']
            or not any(t.operation == 'COMPLETE_RUN' and t.evidence_id == at_id for t in run.events)):
        raise ConflictError('A conclusão não está vinculada a um AT_RESULT oficial do contrato.')
    latest = next(ev for ev in reversed(run.evidence_refs) if ev.kind == 'AT_RESULT'
                  and (ev.at_version, ev.commit_sha, ev.artifact_digest) ==
                  (at.at_version, at.commit_sha, at.artifact_digest))
    if latest != at:
        raise ConflictError('A conclusão não referencia o último resultado oficial do candidato.')
    require_sha1(at.commit_sha, 'Commit do candidato')
    return at


def _report(trust_store, ev):
    parsed = trust_store.read_evidence(ev.run_id, ev.request_id, ev.summary_ref, ev.evidence_sha256)
    if any(parsed.get(field) != getattr(ev, field) for field in (
            'run_id', 'request_id', 'kind', 'verdict', 'at_version', 'commit_sha', 'artifact_digest')):
        raise ConflictError('Relatório protegido diverge do ledger.')
    return parsed


def acceptance_snapshot(run, bound, at, policy_sha256):
    contract = bound['contract']
    return {
        **{key: contract[key] for key in ('run_id', 'change_id', 'spec_version', 'spec_sha256',
                                          'approval_revision', 'plan_sha256', 'ticket_id')},
        'run_revision': run.revision, 'rule_version': RULE_VERSION,
        'contract_sha256': bound['contract_sha256'], 'at_version': at.at_version,
        'commit_sha': at.commit_sha, 'artifact_digest': at.artifact_digest,
        'at_evidence_id': at.evidence_id, 'at_evidence_sha256': at.evidence_sha256,
        'policy_sha256': require_sha256(policy_sha256, 'Hash da política de verificação'),
    }


def verified_snapshot(run, bound, at, trust_store):
    report = _report(trust_store, at)
    if report.get('contract_sha256') != bound['contract_sha256']:
        raise ConflictError('Relatório F-04 não está vinculado ao contrato protegido.')
    _, manifest = trust_store.verify_artifact(at.artifact_digest)
    if manifest.get('commit_sha') != at.commit_sha:
        raise ConflictError('Commit do candidato diverge do artefato promovido.')
    return acceptance_snapshot(run, bound, at, report.get('policy_sha256'))


def review_evidence(run, at, review_id):
    review = next((ev for ev in run.evidence_refs if ev.evidence_id == review_id), None)
    if (review is None or (review.kind, review.verdict, review.collector_route) !=
            ('REVIEW_REPORT', 'PASS', 'OPERATOR_LOCAL') or
            (review.at_version, review.commit_sha, review.artifact_digest) !=
            (at.at_version, at.commit_sha, at.artifact_digest)):
        raise ConflictError('Revisão oficial ausente ou incompatível com o candidato.')
    return review


def verify_review(trust_store, review, snapshot):
    report = _report(trust_store, review)
    if any(report.get(key) != value for key, value in snapshot.items()):
        raise ConflictError('Revisão não corresponde ao snapshot oficial F-04.')
    review_details(**{key: report.get(key) for key in _REVIEW_KEYS})
