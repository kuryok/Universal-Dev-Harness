# Change 5 — Da descoberta ao plano de tickets

- **Estado:** T1 e T2 implementadas e verificadas com testes complementares. T3 e T4 aguardam detalhamento e aprovação; o aceite oficial do change segue pendente.
- **Branch:** `feat/change5-discover-plan-tickets`, a partir de `main` em `ef9c15d`.
- **Base normativa:** [baseline rastreado](../../../Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md) (DELTA-03–05, ADR-023/024/026/027, B4.2, F-03) e [governança do projeto](../../project.md).

## Objetivo e recorte

Ao final do change, uma ideia deve virar um OpenSpec aprovado e um conjunto de tickets verificáveis, com dependências explícitas. O Core autoriza a progressão; propostas de agente são dados não confiáveis. O primeiro incremento (T1) valida a proposta de tickets em memória. Persistência, descoberta real e troca de agentes precisam de deltas de escopo aprovados antes de implementação; os critérios de fechamento abaixo não aprovam essas tarefas.

Fora de escopo: `B2.4`, `B3.8/09`, `DOC-01/02`, `SKILL-01/02`, Codex, Memorix, Judge, instalador, rede externa e ampliação da UI. Preservar selos `v1–v6`, `bootstrap/slice1/`, operadores/políticas e imagens OCI existentes. Nenhuma dependência nova em T1.

## Contrato e critérios verificáveis de T1

O validador recebe duas estruturas Python já decodificadas: `approved_spec`, do chamador confiável, e `proposal`, não confiável. A procedência de `approved_spec` é responsabilidade do chamador; a igualdade de hash não comprova aprovação. O validador não lê Markdown nem acessa rede, SQLite, workspace ou agentes. A saída normalizada não autoriza início de trabalho.

- `approved_spec`: `dict` com exatamente `change_id`, `spec_version`, `spec_sha256` e `tasks`. `tasks` é um `dict` de 1–128 IDs de tarefa para listas de 1–32 IDs de critério, sem repetição dentro de uma tarefa. Tarefa sem critério não é válida neste recorte.
- `proposal`: `dict` com exatamente `change_id`, `spec_version`, `spec_sha256` e `tickets`. `tickets` é uma lista de 1–128 `dict`s, cada um com exatamente `id`, `objective`, `coverage`, `depends_on` e `checks`.
- `coverage` é uma lista de 1–128 objetos, cada um com exatamente `task_id` e `criterion_id`. Cada par deve existir em `tasks`. O mesmo par não pode se repetir no mesmo ticket; tickets diferentes podem cobri-lo quando uma tarefa for subdividida. A união dos pares de todos os tickets deve ser igual ao conjunto aprovado. Por exemplo, se `A` e `B` têm o critério `C1`, cobrir `(A, C1)` não cobre `(B, C1)`.
- `depends_on` é uma lista de 0–127 IDs locais. `checks` é uma lista ordenada de 1–16 textos, em ordem de verificação proposta, sem entradas idênticas. `objective` e cada item de `checks` são `str` de 1–512 caracteres Unicode (pontos de código), sem espaços em branco nas pontas. São descrições, não comandos executados nem evidência oficial.
- Identificadores e `spec_version` são `str` com correspondência integral a `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, sensível a maiúsculas. `spec_sha256` é `str` com correspondência integral a `[0-9a-f]{64}`, sem prefixo. Ambos os argumentos são validados antes de comparar os três campos de identidade. Campos faltantes, extras (inclusive de suposta aprovação), tipos errados e limites excedidos são recusados, sem coerção.
- Sucesso retorna um novo `dict` no formato de `proposal`, sem modificar os argumentos. Ordenar `tickets` topologicamente: em cada passo, escolher o menor ID ASCII dentre **todos** os tickets elegíveis naquele momento. Ordenar `coverage` pela tupla `(task_id, criterion_id)` e `depends_on` pelo ID; preservar `checks` e textos sem alteração. Se `b` depende de `a` e `a` e `c` não têm dependências, a saída é `a`, `b`, `c`, qualquer que seja a ordem de entrada.
- Toda rejeição prevista por este contrato lança `TicketPlanError`, subclasse de `ValueError`, sem devolver plano parcial. A mensagem é diagnóstica; texto e precedência entre falhas simultâneas não fazem parte da API.

| ID | Comportamento observável | Verificação complementar proposta |
|---|---|---|
| C5-01 | Referência ausente, identidade divergente ou dados fora do contrato resultam em `TicketPlanError`. Cobrir tipo incorreto, SHA-256 inválido mesmo quando igual nos dois lados, campo de autoaprovação, lista vazia proibida e limites (no máximo e um acima). | `test_rejects_unapproved_or_mismatched_spec`; `test_rejects_invalid_shapes_and_limits` |
| C5-02 | IDs repetidos, dependências inexistentes ou duplicadas, autorreferências e ciclos são recusados. Um DAG válido tem ordem topológica estável, com desempate pelo ID local. | `test_rejects_invalid_dependencies`; `test_stable_topological_order` |
| C5-03 | A cobertura é validada por par explícito. Cobrir critérios homônimos em tarefas distintas, vínculo cruzado inválido, par faltante, duplicata no ticket, par compartilhado entre tickets e tarefa aprovada sem critério. | `test_requires_approved_traceability_and_coverage` |
| C5-04 | Comparar a saída completa após repetir a validação ou permutar tickets, `coverage` e `depends_on`. A ordem de `checks` é preservada, os argumentos não mudam e a validação não grava estado nem inicia runs. | `test_normalization_is_deterministic` |

## Condições para fechar o change

- **DISC-01:** em uma jornada offline, uma ideia sem lacuna material não gera perguntas; uma ideia com lacuna que afeta o comportamento gera pergunta sobre essa lacuna, sem perguntas cosméticas. Descoberta não escreve código de produção nem cria plano paralelo. O handoff exige aprovação humana e validação real dos artefatos OpenSpec. Confirmar suporte via `openspec --help`; se a CLI ou `validate <change> --strict` não estiver disponível, bloquear o handoff e registrar o motivo. A CLI não está no `PATH` deste checkout. O processo documental do repositório e dublês não comprovam o caminho positivo real. Versão, proveniência e forma de disponibilização da CLI precisam de aprovação antes de integrar a dependência.
- **TICKET-01:** o Core materializa tarefas pequenas aprovadas diretamente; decomposição, quando necessária, entra como proposta e passa pelos mesmos gates. Repetir a materialização, inclusive após reabrir o SQLite, conserva IDs e não duplica registros. Um ticket com predecessora ainda não aceita não pode iniciar. Troca de versão ou hash torna o plano anterior obsoleto para novos inícios até reconciliação explícita, preservando histórico e evidências.
- **Gate B4.2 (T2):** na mesma operação de autorização de início, o Core verifica o vínculo de cada cenário aprovado com seu AT protegido. Mesmo com todas as predecessoras aceitas, um critério sem AT impede início, salvo exceção explícita para protótipo ou spike rotulado. As verificações propostas por um agente não suprem esse gate.
- **TICKET-02:** em um cenário offline com duas tarefas dependentes, o segundo ticket começa somente após evidência confiável e aceite do primeiro; a troca `Pi → OpenCode` recupera o contexto autorizado a partir do estado persistido, do checkpoint F-03 e das referências verificadas, sem transcrição da sessão. A POC-03 existente é regressão, não evidência do novo cenário.

## Decisões pendentes após T1

- Persistência: o [delta de T2](t2-persistence-and-start.md) registra T2a implementada; o gate de início ainda precisa de detalhamento em T2b.3.
- PLAN completo (DELTA-05): T1 cobre apenas a validação estrutural. O delta de T2 deve especificar as dependências e referências de contexto permitidas na entrada e a representação de lacunas na proposta, sem transformar lacuna em autorização. T4 deve verificar o uso desse contexto no handoff.
- Descoberta: confirmar proveniência e carregamento autorizado da skill prevista no DELTA-03, a CLI compatível e as regras de classificação/roteamento aplicáveis. A avaliação futura deve usar entradas offline fixas e rubricar materialidade, ausência de escrita e handoff; um dublê cobre o contrato determinístico, não a qualidade da descoberta nem o handoff real.
