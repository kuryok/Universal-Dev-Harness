"""Tradução determinística ADR-025 (4 campos obrigatórios) para bloqueios, doctor e entrega."""
from .domain import Run

CATEGORY_LABELS = {
    'POLICY_VIOLATION': 'Violação de política de segurança',
    'FUNCTIONAL_FAILURE': 'Falha funcional ou de verificação',
    'ADMIN_PAUSE': 'Pausa administrativa',
    'ENVIRONMENT_CHECK': 'Verificação do ambiente local',
}

OVERALL_STATUS_LABELS = {
    'READY': 'Pronto para uso',
    'READY_WITH_LIMITS': 'Pronto com limites',
    'NOT_READY': 'Ação necessária antes de executar',
}


def make_explanation(
    *,
    category: str,
    reason_code: str,
    what_happened: str,
    why_it_matters: str,
    what_was_verified: str,
    safe_next_action: str,
    severity: str = 'warning',
) -> dict:
    """Constroi cartão estruturado ADR-025 garantindo os 4 campos obrigatórios e zero bypass."""
    return {
        'category': category,
        'category_label': CATEGORY_LABELS.get(category, category),
        'reason_code': reason_code,
        'severity': severity,
        'what_happened': what_happened,
        'why_it_matters': why_it_matters,
        'what_was_verified': what_was_verified,
        'safe_next_action': safe_next_action,
        'allows_bypass': False,
    }


def explain_doctor_report(doctor_report: dict) -> dict:
    """Traduz o relatório do doctor para linguagem comum com cartões ADR-025 para alertas."""
    overall = str(doctor_report.get('overall_status', 'NOT_READY'))
    checks = doctor_report.get('checks') or []
    alerts: list[dict] = []
    passed_count = 0

    for check in checks:
        status = str(check.get('status', 'NOT_READY'))
        check_id = str(check.get('id', 'check'))
        summary = str(check.get('summary', '')).strip() or 'Verificação do ambiente local.'
        next_action = str(check.get('next_action', '')).strip() or 'Consulte o diagnóstico do ambiente local.'
        if status == 'READY':
            passed_count += 1
            continue
        severity = 'error' if status == 'NOT_READY' else 'warning'
        why = (
            'Sem este requisito atendido, o isolamento do ambiente de execução não pode ser garantido.'
            if status == 'NOT_READY'
            else 'Parte das capacidades opcionais está indisponível; o sistema continua seguro com limites explícitos.'
        )
        alerts.append(make_explanation(
            category='ENVIRONMENT_CHECK',
            reason_code=f'DOCTOR_{check_id.upper()}_{status}',
            what_happened=summary,
            why_it_matters=why,
            what_was_verified=f'Checagem determinística "{check_id}" retornou estado {status}.',
            safe_next_action=next_action,
            severity=severity,
        ))

    return {
        'overall_status': overall,
        'status_label': OVERALL_STATUS_LABELS.get(overall, overall),
        'checks_total': len(checks),
        'checks_ready': passed_count,
        'alerts': alerts,
    }


def explain_run_block(run: Run) -> dict | None:
    """Produz cartão ADR-025 de 4 campos para execução BLOCKED a partir do SQLite v4."""
    if run.state != 'BLOCKED':
        return None
    block_events = [ev for ev in run.events if getattr(ev, 'kind', None) == 'RUN_BLOCKED']
    if not block_events:
        return make_explanation(
            category='ADMIN_PAUSE',
            reason_code='RUN_BLOCKED_UNKNOWN',
            what_happened='A execução está bloqueada no registro oficial.',
            why_it_matters='Nenhuma ação automática prossegue enquanto o bloqueio estiver ativo.',
            what_was_verified=f'Estado persistido no SQLite: BLOCKED (revisão {run.revision}).',
            safe_next_action='Revise o motivo do bloqueio antes de retornar a execução para pendente.',
            severity='warning',
        )

    latest = block_events[-1]
    category = getattr(latest, 'block_category', None) or 'ADMIN_PAUSE'
    reason_code = getattr(latest, 'reason_code', None) or getattr(latest, 'block_reason_code', None) or 'RUN_BLOCKED'
    reason = getattr(latest, 'reason', None) or getattr(latest, 'block_reason', None) or 'Bloqueio registrado pelo Core.'
    next_action = getattr(latest, 'next_action', None) or 'Corrija a causa informada e retome a execução de forma segura.'

    if category == 'POLICY_VIOLATION':
        why = (
            'Uma regra de proteção do Harness impediu esta operação para evitar riscos de segurança ou integridade. '
            'Proteções críticas nunca podem ser desativadas pela interface.'
        )
        severity = 'error'
    elif category == 'FUNCTIONAL_FAILURE':
        why = (
            'O software não passou em uma verificação funcional ou de uso real; '
            'entregar sem corrigir causaria falha para quem usar o aplicativo.'
        )
        severity = 'warning'
    else:
        why = 'A execução foi pausada intencionalmente para revisão ou confirmação antes de continuar.'
        severity = 'info'

    evidences_count = len(run.evidence_refs)
    verified_text = (
        f'Estado oficial BLOCKED registrado na revisão {run.revision} '
        f'({CATEGORY_LABELS.get(category, category)}; código {reason_code}; '
        f'{evidences_count} evidência(s) registrada(s) no histórico).'
    )

    card = make_explanation(
        category=category,
        reason_code=reason_code,
        what_happened=reason,
        why_it_matters=why,
        what_was_verified=verified_text,
        safe_next_action=next_action,
        severity=severity,
    )
    card['unblock_note'] = (
        'Desbloquear apenas retorna a execução para PENDENTE com registro auditável; '
        'nenhuma regra de segurança ou evidência obrigatória é ignorada.'
    )
    return card


def explain_delivery_block(delivery: dict) -> dict | None:
    """Explica em 4 campos ADR-025 por que uma execução ainda não atingiu DELIVERED."""
    stage = delivery.get('delivery_stage')
    if stage == 'DELIVERED':
        return None
    if delivery.get('can_deliver'):
        return None
    if delivery.get('has_unknown_pendencies'):
        return make_explanation(
            category='POLICY_VIOLATION',
            reason_code='UNKNOWN_PENDENCY_ACTIVE',
            what_happened='Existe uma pendência incerta (UNKNOWN) ativa no último ponto de recuperação.',
            why_it_matters='O Core não autoriza entrega nem novas etapas enquanto houver estado não reconciliado.',
            what_was_verified='Verificação determinística do último checkpoint no SQLite v4.',
            safe_next_action='Reconcilie a pendência registrada antes de tentar concluir a entrega.',
            severity='error',
        )
    if stage == 'PENDING':
        return make_explanation(
            category='FUNCTIONAL_FAILURE',
            reason_code='ARTIFACT_NOT_PROMOTED',
            what_happened='Ainda não há uma versão oficial do aplicativo promovida e verificada para esta execução.',
            why_it_matters='Testes, visualização e entrega só podem operar sobre uma versão imutável verificada.',
            what_was_verified='Nenhuma evidência ARTIFACT_PROMOTION íntegra encontrada no registro oficial.',
            safe_next_action='Inicie a execução e promova o artefato verificado antes de rodar testes e preview.',
            severity='info',
        )
    if stage == 'IMPLEMENTED':
        detail = 'Os testes oficiais de aceite ainda não foram aprovados para a versão atual do aplicativo.'
        if delivery.get('has_stale_evidences'):
            detail += ' Evidências de versões anteriores não valem para a versão atual.'
        return make_explanation(
            category='FUNCTIONAL_FAILURE',
            reason_code='AT_REQUIRED_FOR_DELIVERY',
            what_happened=detail,
            why_it_matters='Os testes de aceite protegem contra declarar o aplicativo pronto sem comprovar o comportamento combinado.',
            what_was_verified='Artefato íntegro verificado na store oficial; último resultado de aceite (AT) ausente ou não aprovado.',
            safe_next_action='Execute a verificação oficial de testes de aceite para a versão atual.',
            severity='warning',
        )
    if stage == 'TESTED':
        return make_explanation(
            category='FUNCTIONAL_FAILURE',
            reason_code='SMOKE_REQUIRED_FOR_DELIVERY',
            what_happened='Os testes de aceite passaram, mas o teste de uso real (smoke test) ainda não foi aprovado para esta versão.',
            why_it_matters='Apenas testes unitários não bastam; é preciso provar que o aplicativo inicia, responde e preserva dados corretamente.',
            what_was_verified='Último AT_RESULT aprovado e íntegro; último SMOKE_RESULT ausente ou reprovado para este artefato.',
            safe_next_action='Execute o teste de uso real (smoke test) antes de concluir a entrega.',
            severity='warning',
        )
    return make_explanation(
        category='ADMIN_PAUSE',
        reason_code='DELIVERY_WAITING_IN_PROGRESS',
        what_happened='A execução não está em andamento para concluir a entrega.',
        why_it_matters='A conclusão oficial só ocorre a partir de uma execução ativa e verificada.',
        what_was_verified=f'Estado atual: {delivery.get("run_state")}; estágio de entrega: {stage}.',
        safe_next_action='Retome a execução para EM ANDAMENTO e conclua a entrega.',
        severity='info',
    )


def explain_action_error(reason_code: str, detail: str, *, category: str = 'POLICY_VIOLATION') -> dict:
    """Formata erro de operação da UI no formato explicável de 4 campos da ADR-025."""
    return make_explanation(
        category=category,
        reason_code=reason_code,
        what_happened=detail,
        why_it_matters='A operação foi interrompida pelo Core determinístico para preservar o contrato de segurança e integridade.',
        what_was_verified=f'Validação determinística no Core retornou código {reason_code}.',
        safe_next_action='Verifique os pré-requisitos informados acima e tente novamente pela ação guiada segura.',
        severity='error' if category == 'POLICY_VIOLATION' else 'warning',
    )
