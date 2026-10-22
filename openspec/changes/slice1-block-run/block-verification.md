# Bloqueio persistido — resultado de 22/10/2026

Recorte aprovado implementado e verificado no candidato `ad2bd478fa8a07a06b6e1a1b832b1795fafe1c4d`. PENDING→BLOCKED acontece exclusivamente pela entrada administrativa do bootstrap, com revisão esperada, chave de requisição e motivo. Não inicia agentes e não representa aceite/entrega oficial.

## Mudança

- `transitions.py`: validação pura do pedido, contexto de configuração e conflitos; nenhuma dependência de SQLite/I/O. Contexto não é uma credencial: a autoridade real continua no domínio do SO que entrega o banco somente ao runtime administrativo.
- Serviço: valida contexto/pedido antes de I/O e gera UUID/data UTC.
- Persistência: BEGIN IMMEDIATE; valida v2; busca chave global e devolve replay literal, ou grava evento e pedido/autorização, atualiza por compare-and-set e valida integridade antes do commit. Falha em qualquer uma das três escritas desfaz tudo. Replay conserva timestamp, sequência, UUID e medições originais; não gera nova autorização persistida.
- CLI operator: novo comando block, sem --db/--target/--approved; conflito retorna 4, falta de contexto retorna DENY/3 antes de acessar banco. CLI comum e consulta de policy não ganham permissão de escrita. Migração v1→v2 continua explícita.

O Core contém nove módulos, 704 linhas no total; maior módulo store.py com 236 linhas. Nenhuma dependência, credencial, rede ou mudança de AT/infraestrutura foi necessária nesta tarefa.

## Verificação nesta sessão

- 36 complementares PASS, incluindo seis novos: v1 sem migração, literais/replay com medições originais, chave global/conflitos, validação/contexto sem I/O e rollback injetado nas três escritas. Revisor independente repetiu os 36: PASS.
- Todos os 13 AT do selo v5 PASS, zero failures/errors/skips. Os cinco testes de bloqueio antes pendentes agora passam.
- 171 chamadas isoladas auditadas, remoção confirmada; 15 checkpoints de interrupção comprovados: onze na migração e quatro no bloqueio. B3 verifica chamadas concorrentes iguais/distintas; B4 verifica rollback e replay após commit sem resposta.
- Regressões contra o mesmo candidato: dez AT v1, onze AT policy e I1–I7/T1–T8 PASS.
- Revisão independente em block-review.json: PASS, nenhum achado pendente. Hashes dos nove módulos coincidem com a revisão e os três conjuntos de snapshots executados. Selos v1–v5 íntegros; testes protegidos não foram editados.
- Resumo em block-verification.json. Relatórios detalhados: `bootstrap/slice1/evidence/block-candidate.json` e `block-product-regression*.json`.

Nenhum resultado parcial foi promovido a sucesso. O bootstrap continua sem proteção contra quem escreve no host; testes de interrupção são instrumentados e não provam queda de energia/hardware nem resistência do wrapper a código malicioso. Harness fast/full e CLI OpenSpec não executados porque ausentes.

## Reprodução

Complementares: `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests -v`.

AT isolados a partir da raiz do projeto; host importa somente ferramentas confiáveis, e o candidato executa no runtime:

```python
from pathlib import Path
import sys
sys.path.insert(0, str(Path('bootstrap/slice1').resolve()))
from block_seal import verify_seal
from integrate_block import run_suite
seal = Path('bootstrap/slice1/seals/v5').resolve()
manifest = verify_seal(seal, 'sha256:182d2ba72a7b5dfb5648e650577dcc3bbbbd67048c72a3bf959406ce7dd89ac6')
result = run_suite(seal, manifest, 'ad2bd478fa8a07a06b6e1a1b832b1795fafe1c4d')
assert result['tests_run'] == 13
assert not result['errors'] and not result['failures'] and not result['skipped']
print(result['outcomes'])
```

A operação real exercitada pelos AT é `python3 -m udh.operator block --run-id UUID --expected-revision 0 --request-id ID --reason TEXTO`, sempre através do launcher protegido. Chamar o módulo sem esse contexto administrativo retorna DENY e não acessa banco. Não há instalação operacional pronta neste recorte.

## Próximo passo

Especificar desbloqueio/retomada de BLOCKED com novas revisões e checkpoints consistentes, antes de implementar outra transição ou integrar agentes. Não implementados: retomada, IN_PROGRESS/DONE, executor de agentes, UI e entrega oficial. Slice 1 permanece em construção.
