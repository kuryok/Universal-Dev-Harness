"""Política determinística de consulta (v1 em bancos v1–v3; v2 em bancos v4); sem I/O."""
import hashlib
import json

from .domain import Run, require_text

POLICY_VERSION = 1
POLICY_VERSION_V2 = 2
_RULES = {
    'READ_RUN': ('ALLOW', 'READ_ONLY_METADATA',
                 'Leitura de metadados permitida no contexto administrativo local.',
                 'Consulte os metadados da execução.'),
    'EXECUTE_AGENT': ('DENY', 'EXECUTOR_NOT_READY',
                      'A execução de agentes ainda não está disponível.',
                      'Prepare a integração e as evidências confiáveis do executor.'),
    'TRANSITION_RUN': ('DENY', 'TRANSITIONS_NOT_ENABLED',
                       'Transições de estado ainda não estão disponíveis.',
                       'Aprove o contrato versionado das transições.'),
    'ACCEPT_ARTIFACT': ('DENY', 'ACCEPTANCE_GATE_UNAVAILABLE',
                        'Os gates de aceite ainda não estão disponíveis.',
                        'Implemente os gates e as evidências de aceite.'),
    'DELIVER_ARTIFACT': ('DENY', 'DELIVERY_GATE_UNAVAILABLE',
                         'Os gates de entrega ainda não estão disponíveis.',
                         'Implemente os gates de entrega e a verificação de funcionamento.'),
}
_DEFAULT = ('DENY', 'UNKNOWN_ACTION', 'Ação desconhecida.',
            'Escolha uma operação suportada pela política.')

_V2_POLICY_SPEC = {
    'version': POLICY_VERSION_V2,
    'rules': {
        'READ_RUN': {'decision': 'ALLOW', 'reason_code': 'READ_ONLY_METADATA'},
        'TRANSITION_RUN': {
            'done': {'decision': 'DENY', 'reason_code': 'RUN_TERMINAL_DONE'},
            'unknown_pendency': {'decision': 'DENY', 'reason_code': 'UNKNOWN_PENDENCY_ACTIVE'},
            'active': {'decision': 'ALLOW', 'reason_code': 'LIFECYCLE_TRANSITION_AVAILABLE'},
        },
        'EXECUTE_AGENT': {
            'not_in_progress': {'decision': 'DENY', 'reason_code': 'RUN_NOT_IN_PROGRESS'},
            'unknown_pendency': {'decision': 'DENY', 'reason_code': 'UNKNOWN_PENDENCY_ACTIVE'},
            'preflight_not_ready': {'decision': 'DENY', 'reason_code': 'PREFLIGHT_NOT_READY'},
            'default': {'decision': 'DENY', 'reason_code': 'GATEWAY_AND_ADAPTER_NOT_READY'},
        },
        'ACCEPT_ARTIFACT': {
            'not_in_progress': {'decision': 'DENY', 'reason_code': 'RUN_NOT_IN_PROGRESS'},
            'unknown_pendency': {'decision': 'DENY', 'reason_code': 'UNKNOWN_PENDENCY_ACTIVE'},
            'evidence_pass': {'decision': 'ALLOW', 'reason_code': 'OFFICIAL_EVIDENCE_VERIFIED'},
            'missing_evidence': {'decision': 'DENY', 'reason_code': 'MISSING_OFFICIAL_EVIDENCE'},
        },
        'DELIVER_ARTIFACT': {'decision': 'DENY', 'reason_code': 'DELIVERY_GATE_UNAVAILABLE'},
    },
    'default': {'decision': _DEFAULT[0], 'reason_code': _DEFAULT[1]},
}


def _v2_digest() -> str:
    return hashlib.sha256(json.dumps(
        _V2_POLICY_SPEC, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
        allow_nan=False).encode('utf-8')).hexdigest()


def _evaluate_v2(run: Run, action: str, *, preflight_status: str | None = None) -> dict:
    has_unknown = run.has_unknown_pendencies
    if action == 'READ_RUN':
        decision, code, message, next_action = (
            'ALLOW', 'READ_ONLY_METADATA',
            'Leitura de metadados permitida para a execução v4.',
            'Consulte o estado, transições e checkpoints da execução.',
        )
    elif action == 'TRANSITION_RUN':
        if run.state == 'DONE':
            decision, code, message, next_action = (
                'DENY', 'RUN_TERMINAL_DONE',
                'A execução já está em estado terminal DONE.',
                'Abra uma nova mudança ou execução para alterar trabalho já concluído.',
            )
        elif has_unknown:
            decision, code, message, next_action = (
                'DENY', 'UNKNOWN_PENDENCY_ACTIVE',
                'Existe pendência UNKNOWN ativa no último checkpoint.',
                'Reconcilie a pendência externa e registre um novo checkpoint sem pendências antes de avançar.',
            )
        else:
            decision, code, message, next_action = (
                'ALLOW', 'LIFECYCLE_TRANSITION_AVAILABLE',
                f'Transições do ciclo de vida estão habilitadas a partir do estado {run.state}.',
                'Execute a próxima transição válida usando python3 -m udh.lifecycle.',
            )
    elif action == 'EXECUTE_AGENT':
        if run.state != 'IN_PROGRESS':
            decision, code, message, next_action = (
                'DENY', 'RUN_NOT_IN_PROGRESS',
                f'Execução em estado {run.state} não pode acionar agente.',
                'Inicie a execução (PENDING → IN_PROGRESS) antes de solicitar execução de agente.',
            )
        elif has_unknown:
            decision, code, message, next_action = (
                'DENY', 'UNKNOWN_PENDENCY_ACTIVE',
                'Existe pendência UNKNOWN ativa no último checkpoint.',
                'Reconcilie a pendência externa antes de retomar a execução.',
            )
        elif preflight_status == 'NOT_READY':
            decision, code, message, next_action = (
                'DENY', 'PREFLIGHT_NOT_READY',
                'O preflight do ambiente retornou NOT_READY.',
                'Execute python3 -m udh doctor e resolva os pré-requisitos ausentes.',
            )
        else:
            decision, code, message, next_action = (
                'DENY', 'GATEWAY_AND_ADAPTER_NOT_READY',
                'Runtime Manager, Gateway de inferência e adaptador Pi 1.2.0 ainda não estão integrados.',
                'Conclua e aprove o Change 2 (runtime rootless, gateway e adaptador Pi 1.2.0).',
            )
    elif action == 'ACCEPT_ARTIFACT':
        if run.state != 'IN_PROGRESS':
            decision, code, message, next_action = (
                'DENY', 'RUN_NOT_IN_PROGRESS',
                f'Aceite exige execução em IN_PROGRESS (estado atual: {run.state}).',
                'Coloque a execução em IN_PROGRESS antes de avaliar o aceite.',
            )
        elif has_unknown:
            decision, code, message, next_action = (
                'DENY', 'UNKNOWN_PENDENCY_ACTIVE',
                'Existe pendência UNKNOWN ativa no último checkpoint.',
                'Reconcilie a pendência externa antes de concluir ou aceitar a execução.',
            )
        elif run.has_passing_evidence:
            decision, code, message, next_action = (
                'ALLOW', 'OFFICIAL_EVIDENCE_VERIFIED',
                'Evidência oficial com veredito PASS registrada para esta execução.',
                'Conclua a execução referenciando o evidence_id verificado.',
            )
        else:
            decision, code, message, next_action = (
                'DENY', 'MISSING_OFFICIAL_EVIDENCE',
                'Nenhuma evidência oficial verificadora com veredito PASS foi registrada para esta execução.',
                'Execute os testes oficiais no executor confiável e registre a evidência antes de aceitar.',
            )
    elif action == 'DELIVER_ARTIFACT':
        decision, code, message, next_action = _RULES['DELIVER_ARTIFACT']
    else:
        decision, code, message, next_action = _DEFAULT

    return dict(
        policy_version=POLICY_VERSION_V2,
        policy_sha256=_v2_digest(),
        run_id=run.run_id,
        state=run.state,
        revision=run.revision,
        action=action,
        decision=decision,
        reason_code=code,
        message=message,
        next_action=next_action,
    )


def evaluate(run: Run, action: str, *, preflight_status: str | None = None) -> dict:
    """Avalia fatos do domínio; v1 congelada em bancos v1–v3, v2 em bancos v4."""
    require_text(action, 'Ação da política')
    if run.schema_version == 4:
        return _evaluate_v2(run, action, preflight_status=preflight_status)
    body = {'version': POLICY_VERSION,
            'rules': {name: {'decision': rule[0], 'reason_code': rule[1]}
                      for name, rule in _RULES.items()},
            'default': {'decision': _DEFAULT[0], 'reason_code': _DEFAULT[1]}}
    digest = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
        allow_nan=False).encode('utf-8')).hexdigest()
    decision, code, message, next_action = _RULES.get(action, _DEFAULT)
    return dict(policy_version=POLICY_VERSION, policy_sha256=digest,
                run_id=run.run_id, state=run.state, action=action,
                decision=decision, reason_code=code, message=message,
                next_action=next_action)
