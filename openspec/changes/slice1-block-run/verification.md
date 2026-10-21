# Verificação da especificação — 21/10/2026

Mudança exclusivamente documental. Proposta de PENDING→BLOCKED, migração explícita v1→v2, journal atômico/idempotente e entrada administrativa protegida do mesmo Core. Nenhuma mudança em código, AT, infraestrutura ou selos.

- `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests -v`: 16 testes existentes PASS nesta sessão.
- Core: hashes dos seis módulos coincidem com o candidato revisado bfb2cbb.
- Selos v1/v2 verificados pelos respectivos verificadores; hashes das fontes atuais correspondem aos dois selos.
- `git diff --check`: PASS.
- AT externos 10/11 e I1–I7/T1–T8: não reexecutados nesta preparação documental. Resultados anteriores continuam no recorte de policy; não são evidência da transição nova.
- Cenários M1–M3/B1–B5/R1–R2: não executados; migração, transição e fronteira administrativa nova não implementadas.
- Revisão independente desta proposta: não executada. Nenhuma alegação de aprovação técnica independente.
- Harness fast/full e CLI OpenSpec: não executados, ferramentas ausentes.

Suposições para aprovação: bloqueio administrativo antes de iniciar trabalho; opt-in preservando v1; operador no domínio confiável do SO sem novas credenciais. Handoff de infraestrutura/AT independente permanece pré-condição antes de implementar. Não é aceite de produto nem conclusão da Slice 1.

## Após aprovação do contrato

O usuário aprovou PENDING→BLOCKED e migração opt-in. Nesta etapa foram detalhados o DDL lógico, algoritmo de reconstrução com FKs ligadas, layout administrativo isolado e handoff com arquivos nomeados, preservando a semântica aprovada.

Revisão documental independente `block_contract_review` registrada em `design-review.json`: nenhum desvio semântico identificado. Não equivale a revisão de DDL executável ou R2 de produto. Permanecem gates: autorização do handoff protegido, SQL concreto e normalização após renames, pins/launcher/mounts e observação determinística de interrupções M3/B4.

Nesta sessão: 16 complementares existentes PASS com o comando acima; diff check PASS. Os AT novos não foram criados ou executados; produto/selos não foram alterados. A autorização do contrato funcional está registrada; o preparo exato de infraestrutura protegida está apresentado para autorização separada. Não há evidência nova de migração ou transição.
