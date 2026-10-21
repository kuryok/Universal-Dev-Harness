"""Política determinística de consulta; sem I/O ou concessão de execução."""
import hashlib
import json

from .domain import Run, require_text

POLICY_VERSION = 1
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


def evaluate(run: Run, action: str) -> dict:
    """Avalia fatos do domínio; ALLOW limita-se à leitura de metadados."""
    require_text(action, 'Ação da política')
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
