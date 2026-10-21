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
