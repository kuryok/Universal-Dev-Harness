# Reanálise do próximo incremento — 27/10/2026

Conferência solicitada pelo usuário, sem iniciar implementação. Este documento explica a aplicação do [contrato aprovado](proposal.md) e do [DDL](design.md); não cria requisitos, aprovação nova ou plano paralelo. O [alinhamento da auditoria](../slice1-evidence-storage/alignment.md) mantém o propósito e a prioridade do Core.

## Conclusão e prontidão

O próximo passo técnico continua sendo **migração SQLite v2→v3 e leitura v3**, antes da operação unblock. O preparo v6 já existe; não há razão funcional para terminar exportação/selo7 antes desse incremento. Não se dispensam os critérios de publicação7.

Nesta sessão:91 complementares PASS/9,899s, sem failures/errors/skips; baseline normativo idêntico ao HEAD; proposta/DDL com hashes correspondentes ao selo6;16 métodos de AT funcionais protegidos presentes; revisão atual de infraestrutura válida para seus14 componentes. Essas verificações não reexecutam os AT ou POCs nem certificam produto.

## Cuidados relevantes para não validar o candidato errado

- O Core atual só aceita perfil administrativo1 e migrate --to2. O perfil2 do launcher já foi preparado; sua aceitação e a autorização de MIGRATE_V3 ainda precisam ser implementadas no Core. O contexto é metadado, não credencial portátil; a fronteira continua sendo o domínio administrativo que monta o banco.
- `integrate_block.py --baseline --generation 7` usa o commit fixo ad2bd478fa8a07a06b6e1a1b832b1795fafe1c4d. É controle negativo, não teste automático do worktree nem do futuro Core3. Não alterar esse controle ou sua classificação para fazê-lo parecer candidato novo.
- A API existente run_suite recebe um ref Git e prepara o candidato por git ls-tree/show. Os testes do candidato precisam de um SHA novo e de hashes correspondentes ao código implementado. Como os ajustes atuais ainda não têm commit autorizado, recomenda-se consolidar um checkpoint explícito antes de misturá-los ao próximo incremento. Não criar commit oculto para contornar a restrição.
- É possível reutilizar o executor/AT congelados v6 para coletar o candidato; não precisa editar selos ou criar um novo gate. Relatar o SHA executado e as duas identidades quando coletor7/executor6 forem usados.

## Pendência adicional do protótipo v7

A checagem `_validate_external('complementary', ...)` ainda usa o mínimo histórico39. Uma reprodução com EvidenceStore temporária, relatório sintético PASS/count39/errors/failures/skipped vazios, foi aceita por essa checagem isolada. Isso não comprova os91 testes atuais, nem foi anunciado como aceitação pelo gate completo ou publicação7.

Antes de retomar a publicação7, será preciso alinhar a comprovação complementar à suíte corrente e à identidade dos testes efetivamente executados, em vez de usar somente aquele mínimo. Não alterar selos/relatórios antigos ou elevar contagem no papel. É pendência do aceite final de infraestrutura, já indisponível; neste incremento do Core executar a suíte completa e registrar o resultado real. Nenhum gate foi alterado por esta reanálise.

## Primeiro incremento de produto, dentro do escopo já aprovado

| Área | O que precisa estar coberto |
| --- | --- |
| Persistência | Migração explícita, BEGIN IMMEDIATE, FKs ativas, DDL final exato, cópia literal, nenhuma criação implícita ou downgrade. Repetição íntegra em3 sem efeito. |
| Domínio/leitura | Schema3 e seus journals válidos; recusar órfãos, cross-run, ordem/revisão/autorizações inconsistentes. Leitura de estado PENDING/2 não habilita ainda sua escrita pública. |
| Compatibilidade | Init continua v1; v1/v2 preservados. Create/show/policy em3 refletem versão/revisão reais. Block já existente continua coerente em3 e preserva resposta histórica2 dos pedidos migrados; pedidos novos guardam versão3. |
| Serviço/CLI administrativa | MIGRATE_V3 somente pelo perfil/operação autorizados; negativas antes de I/O; CLI comum sem migrate/block/unblock; nenhum comando novo de execução de agente. |
| Verificação | Preservar os91 complementares atuais, acrescentar os pertinentes, executar casos protegidos de migração/leitura e regressões legadas no candidato correto, com revisão dos bytes finais. |

Usar os módulos permitidos domain/migrations/store/service/transitions/operator; CLI comum/policy apenas se indispensável para preservar o contrato. Manter separação por responsabilidade. Em tests/test_unblock.py, acrescentar complementares sem remover os testes de preparo existentes; esse arquivo não é o AT protegido de mesmo nome no selo6.

Só depois implementar BLOCKED/1→PENDING/2 e verificar concorrência, idempotência e replay histórico de block. Antes disso, os AT que dependem da escrita de unblock continuarão pendentes/falhando; não editar, excluir, xfail ou ocultar esses resultados para declarar sucesso total. O controle negativo fixo permanece separado da avaliação do candidato. O recorte inteiro só pode ser declarado verificado após os16 AT funcionais completos e regressões pertinentes no SHA final.

## Pendências mantidas visíveis

- Exportação offline, AT de transporte e selo7: backlog preservado, publicação bloqueada até seus próprios critérios; sem ampliar bootstrap para adiantar esse selo.
- POCs, store oficial protegida, conformance de adapters e checkpoint completo Git/spec/artefatos da F-03: continuam pendentes antes das respectivas alegações/integrações de produto. Recuperação SQLite instrumentada não equivale a tudo isso nem a queda de energia.
- ADR-018 A/B1–B4 pendentes no baseline não foram ratificados pela escolha da fonte normativa. Esta migração não abre rede, recebe credenciais nem instala novos componentes.
- Unblock retira uma pausa administrativa; não prova causa técnica resolvida, não retoma subprocesso/agente e não concede IN_PROGRESS/DONE/ACCEPTED/DELIVERED.
- Interface guiada, memória semântica, Judge e entrega continuam no propósito e fora do incremento atual; não foram descartados para reduzir trabalho.

Nenhum código, AT, selo, evidência histórica, índice ou commit foi alterado por esta reanálise. Harness fast/full e CLI OpenSpec ausentes: não executados; AT/POCs reais não reexecutados. Próximo incremento está tecnicamente definido; autorização de commit continua sendo um assunto separado da aprovação funcional já registrada.
