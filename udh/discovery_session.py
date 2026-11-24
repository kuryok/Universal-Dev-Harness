"""Acompanha lacunas de descoberta em memória até a revisão documental."""

from dataclasses import dataclass, replace
import hashlib

from .discovery import select_discovery_questions


_MAX_ROUNDS = 3
_MAX_GAPS = 16
_MAX_ANSWER = 1024


class DiscoverySessionError(ValueError):
    """A operação não satisfaz o contrato da sessão de descoberta."""


@dataclass(frozen=True)
class DiscoveryGap:
    id: str
    impact: str
    reason: str
    question: str
    answer: str | None = None
    confirmed_answer_sha256: str | None = None

    @property
    def answer_sha256(self) -> str | None:
        if self.answer is None:
            return None
        return hashlib.sha256(self.answer.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class DiscoverySession:
    rounds: int = 0
    gaps: tuple[DiscoveryGap, ...] = ()

    @property
    def open_gaps(self) -> tuple[DiscoveryGap, ...]:
        return tuple(gap for gap in self.gaps
                     if gap.confirmed_answer_sha256 is None or gap.confirmed_answer_sha256 != gap.answer_sha256)

    @property
    def ready_for_document_review(self) -> bool:
        return self.rounds > 0 and not self.open_gaps

    def apply_round(self, proposal: object) -> 'DiscoverySession':
        if self.rounds >= _MAX_ROUNDS:
            raise DiscoverySessionError('Limite de rodadas de descoberta atingido.')
        questions = select_discovery_questions(proposal)['questions']
        by_id = {gap.id: gap for gap in self.gaps}
        # Uma lacuna conhecida não pode ser reclassificada como cosmética.
        for item in proposal['gaps']:
            previous = by_id.get(item['id'])
            if previous is not None and any(item[key] != getattr(previous, key) for key in item):
                raise DiscoverySessionError('A proposta alterou uma lacuna material conhecida.')
        for item in questions:
            if item['id'] not in by_id:
                by_id[item['id']] = DiscoveryGap(**item)
        if len(by_id) > _MAX_GAPS:
            raise DiscoverySessionError('Limite de lacunas materiais da sessão excedido.')
        return replace(self, rounds=self.rounds + 1, gaps=tuple(by_id[key] for key in sorted(by_id)))

    def _gap(self, gap_id: str) -> DiscoveryGap:
        if type(gap_id) is str:
            for gap in self.gaps:
                if gap.id == gap_id:
                    return gap
        raise DiscoverySessionError('Identificador de lacuna material desconhecido.')

    def _with_gap(self, updated: DiscoveryGap) -> 'DiscoverySession':
        return replace(self, gaps=tuple(updated if gap.id == updated.id else gap for gap in self.gaps))

    def record_answer(self, gap_id: str, answer: str) -> 'DiscoverySession':
        """Registra uma resposta recebida pela rota humana confiável."""
        gap = self._gap(gap_id)
        if type(answer) is not str or not 1 <= len(answer) <= _MAX_ANSWER or answer != answer.strip():
            raise DiscoverySessionError('Resposta deve ter de 1 a 1024 caracteres, sem espaços nas pontas.')
        try:
            answer.encode('utf-8')
        except UnicodeEncodeError:
            raise DiscoverySessionError('Resposta deve ser codificável em UTF-8.') from None
        if answer == gap.answer:
            return self
        return self._with_gap(replace(gap, answer=answer, confirmed_answer_sha256=None))

    def confirm_resolution(self, gap_id: str, answer_sha256: str) -> 'DiscoverySession':
        """Confirma resolução somente para o conteúdo que a pessoa revisou."""
        gap = self._gap(gap_id)
        if type(answer_sha256) is not str or answer_sha256 != gap.answer_sha256:
            raise DiscoverySessionError('Confirmação exige o digest da resposta atual.')
        return self._with_gap(replace(gap, confirmed_answer_sha256=answer_sha256))
