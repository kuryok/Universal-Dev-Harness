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

## Preparo executado após autorização — 21/10/2026

O usuário autorizou o handoff protegido e, em pergunta específica, o experimento instrumentado de interrupção. Produto/Core permanece no snapshot anterior; nenhuma migração ou transição foi acrescentada nesta tarefa.

- Autoria independente `block_at_author`: 13 AT. Baseline fixo `966036bb67fd36301ab9f638949ad31d13717bc1`: 11 falhas funcionais esperadas pela ausência do módulo operator/migração/transição e 2 PASS de preservação/fronteira. Zero errors/skips; 60 chamadas auditadas. `block-baseline.json` informa PASS da discriminação, não aprovação do produto novo.
- A primeira execução revelou erro do bridge ao rejeitar comando comum desconhecido antes do parser; corrigido o transporte, sem alterar os AT para acomodar o defeito. A revisão também exigiu vincular mapa de bytes do Core ao commit, registrar prova instrumentada na auditoria e conferir resultados normalizados/artefatos externos. Corrigidos e reexecutados.
- Complementares: 20 PASS, incluindo adulteração de snapshot/configuração, identidade de rascunho, arquivo extra/ausente/symlink e recusa de publicação sem revisão.
- Experimento sintético de SQLite: discovery de duas escritas e pós-commit; interrupção antes da segunda escrita recuperou zero linhas, interrupção após commit conservou duas linhas. Marcador, contêiner, comando kill confirmado, resultado bruto, remoção e integridade registrados em `block-instrumentation.json`. Não é execução da migração real nem teste de energia/hardware. A instrumentação pressupõe colaboração do código, conforme contrato aprovado.
- Regressões no mesmo Core: 10 AT v1 PASS, 11 AT policy v2 PASS e I1–I7/T1–T8 PASS; evidências `block-regression*.json`, `integration.json` e `boundary.json`. Selos anteriores preservados.
- Revisão independente `block_infra_review`: PASS nos hashes finais, sem achados pendentes; quatro adulterações do relatório também recusadas pelo gate. Relatório `infrastructure-review.json`.
- Selo v3 publicado por operação exclusiva: `sha256:d1362b119315a504976adf573f802fd7b3e4b045e03554a1432fc43c76cf3e0a`. Verificação e repetição idêntica PASS. R1/R2 CHECK_EXTERNAL do coverage são satisfeitos respectivamente pelo relatório externo vinculado e pela revisão independente; a lista estática não é apagada para forçar publicação.

### Reprodução

- Complementares: `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests -v`.
- Baseline negativo: `PYTHONDONTWRITEBYTECODE=1 python3 bootstrap/slice1/integrate_block.py --baseline`.
- Regressão de isolamento: `PYTHONDONTWRITEBYTECODE=1 python3 bootstrap/slice1/integrate_acceptance.py`.

Baseline identifica rascunho dos mesmos bytes, e publicação inclui revisão/relatório vinculados a esse rascunho. Não confundir a identidade do rascunho com a do selo publicado. Os requisitos e AT/controladores fixados no v3 não devem ser editados silenciosamente na implementação do produto; resultados novos ficam neste relatório e em evidências próprias.

Próximo incremento autorizado: implementar somente migração SQLite e leitura v2, com preservação v1; verificar esse recorte antes de habilitar bloqueio. M3/B4 de produto só podem passar quando executados contra a implementação real. Nenhum ACCEPTED/DELIVERED oficial ou Slice 1 completa. Harness fast/full e CLI OpenSpec continuam não executados por ausência das ferramentas.
