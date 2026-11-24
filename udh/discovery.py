"""Valida propostas de descoberta e seleciona perguntas sem autorizar ações."""

import re


_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
_IMPACTS = ('behavior', 'data', 'delivery', 'cosmetic')
_GAP_FIELDS = ('id', 'impact', 'reason', 'question')


class DiscoveryProposalError(ValueError):
    """A proposta de descoberta não satisfaz o contrato."""


def _has_fields(value, *fields):
    return (type(value) is dict and len(value) == len(fields)
            and all(type(key) is str and key in fields for key in value))


def select_discovery_questions(proposal: object) -> dict[str, list[dict[str, str]]]:
    """Devolve as perguntas selecionadas; ausência de perguntas não autoriza avanço."""
    if not _has_fields(proposal, 'gaps'):
        raise DiscoveryProposalError('A proposta deve conter somente o campo gaps.')
    gaps = proposal['gaps']
    if type(gaps) is not list or len(gaps) > 16:
        raise DiscoveryProposalError('gaps deve ser uma lista com 0 a 16 itens.')

    by_id = {}
    for gap in gaps:
        if not _has_fields(gap, *_GAP_FIELDS):
            raise DiscoveryProposalError('Campos inválidos na proposta de lacuna.')
        gap_id = gap['id']
        if type(gap_id) is not str or not _IDENTIFIER.fullmatch(gap_id) or gap_id in by_id:
            raise DiscoveryProposalError('Identificador de lacuna inválido ou repetido.')
        impact = gap['impact']
        if type(impact) is not str or impact not in _IMPACTS:
            raise DiscoveryProposalError('Impacto inválido na proposta de lacuna.')
        for field in ('reason', 'question'):
            text = gap[field]
            if type(text) is not str or not 1 <= len(text) <= 512 or text != text.strip():
                raise DiscoveryProposalError(f'Texto inválido em {field}.')
        by_id[gap_id] = gap

    return {'questions': [by_id[key].copy() for key in sorted(by_id) if by_id[key]['impact'] != 'cosmetic']}
