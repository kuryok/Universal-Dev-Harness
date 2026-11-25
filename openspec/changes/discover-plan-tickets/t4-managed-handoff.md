# T4 — Troca de agente através do plano persistido (`TICKET-02`)

- **Estado:** aprovado pelo usuário, implementado e endurecido após revisão independente em 25/11/2026. Verificado com 7/7 testes focados (`test_managed_ticket_handoff.py`, incluindo cenário E2E offline real `Pi 1.2.0 → OpenCode 1.18.35 + artifact-only F-04`, adulteração `F-02` e múltiplas predecessoras) e 223/223 complementares `PASS` (98,7 s, incluindo `UDH_TEST_DISCOVERY_PI=1` e `OpenSpec 2.1.1`). Aceite oficial do change fica no fechamento.
- **Base:** [proposta do Change 5](proposal.md#condições-para-fechar-o-change), [T2](t2-persistence-and-start.md), [T2b](t2b-managed-ticket-start.md) e [baseline](../../../Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md) (DELTA-05, ADR-011/026/027, B4.2, F-02, F-03 e F-04).

## Objetivo e recorte

Validar a troca determinística de agente (`Pi → OpenCode`) entre duas tarefas dependentes materializadas no mesmo plano persistido (SQLite v8/v9), garantindo que:

1. O segundo ticket só pode iniciar (`START_RUN`) e receber pacote de handoff após evidência confiável (`F-04`) e aceite oficial (`F-04 + F-02`) do primeiro ticket na mesma geração aprovada.
2. O contexto autorizado do segundo ticket (`context_ids ⊆ allowed_context_ids`, objetivo, cobertura e predecessoras) é recuperado exclusivamente do estado persistido no SQLite, do checkpoint `F-03` verificado da predecessora e das referências verificadas na `TrustStore`, sem transcrição conversacional da sessão do primeiro agente.
3. A coleta de evidências de ambos os tickets ocorre pelo `TrustedExecutor` (`F-04`) em contêiner `artifact-only` isolado, com `git_dir` externo ao `workspace` e sem alterar `POC-03` nem os 50 AT selados (`v1–v6`).

**Fora de escopo:** nova migração SQLite (sem v10; opera sobre v8/v9 existentes), rotas novas de CLI/UI, rede externa, novas dependências, alteração em `udh/handoff.py` (`POC-03` permanece regressão intocada), `udh/operator.py`, `udh/policy.py`, `udh/__main__.py`, manifestos OCI ou `bootstrap/slice1/`.

## Pré-condições de aceite e contrato de handoff entre tickets

### 1. Leitura transacional no Core (`TicketStore` / `CoreService`)

Expor operação somente-leitura no chamador local confiável (`CoreService.managed_ticket_handoff_context(run_id, *, trust_store)`), executada sob uma única transação `BEGIN` (exigindo SQLite `>= v8` e `TrustStore` confiável):

- Conferir que o run de destino está em `IN_PROGRESS`, sem pendências `UNKNOWN` (`has_unknown_pendencies == False`), com vínculo em `managed_ticket_runs` pertencente à identidade e `approval_revision` vigentes em `ticket_plan_heads` e ao `active_plan_sha256` ativo.
- Revalidar o plano persistido (`ticket_plans` + `ticket_plan_inputs`), o contrato `B4.2` do ticket de destino (`managed_ticket_contracts`, incluindo `unresolved_gaps == []`, `context_ids ⊆ allowed_context_ids` e igualdade de cobertura com o ticket) e o selo de AT na `TrustStore` (`verify_contract_seal`).
- Derivar `objective`, `coverage`, `checks`, `depends_on`, `context_ids` e `allowed_context_ids` exclusivamente do plano e do contrato persistidos; nunca aceitar listas alternativas enviadas pelo chamador ou pelo agente.
- Exigir `depends_on` não vazio para handoff entre tickets dependentes. Para cada predecessora direta em `depends_on`:
  - exigir run vinculado na **mesma geração** `(change_id, spec_version, spec_sha256, approval_revision, plan_sha256)` e registro em `managed_ticket_acceptances`;
  - revalidar fisicamente na `TrustStore` o snapshot aceito (`AT_RESULT PASS` de `TRUSTED_EXECUTOR`, `REVIEW_REPORT PASS` de `OPERATOR_LOCAL`, `artifact_digest` íntegro e `commit_sha` compatível, pela mesma regra de T2b.3a/b);
  - exigir checkpoint `F-03` mais recente da predecessora na mesma `run_revision` aceita, sem pendências `UNKNOWN`, com `base_commit_sha`, `head_commit_sha` e `worktree_snapshot_sha256` preenchidos, `head_commit_sha == accepted.commit_sha` e `at_evidence_sha256` presente em `evidence_hashes` e verificado em disco na `TrustStore`.

### 2. Verificação física do worktree e `git_dir` externo

- Validar o `workspace` com `verify_worktree_clean(git_dir, workspace)` (`git_dir` estritamente fora do `workspace`, `O_NOFOLLOW`, correspondência 1:1 com `git ls-files`, rejeição de `.git`, symlinks, `.pth` e sombreamentos).
- Conferir que o `head_commit_sha` e o `worktree_snapshot_sha256` do worktree conferem com o checkpoint `F-03` verificado da predecessora aceita (quando houver múltiplas predecessoras diretas, todos os `commit_sha` aceitos devem ser ancestrais ou iguais ao `HEAD` atual, e `HEAD` + `worktree_snapshot_sha256` devem coincidir com o checkpoint `F-03` de uma predecessora aceita).

### 3. Contexto autorizado (`context_ids`) e ausência de transcrição

- O chamador local confiável pode passar `context_refs: dict[str, dict] | None = None`:
  - se `contract['context_ids']` estiver vazio, `context_refs` deve ser `None` ou `{}`;
  - se `contract['context_ids']` não estiver vazio, `context_refs` é obrigatório e `set(context_refs.keys())` deve ser **exatamente igual** a `set(contract['context_ids'])` (rejeitando IDs extras, não autorizados ou faltantes).
- Cada entrada de `context_refs[cid]` deve ser um `dict` com exatamente `kind` (identificador T1), `ref_sha256` (64 hex minúsculos) e `summary` (`str` UTF-8 não vazia, sem espaços nas pontas, até 4 KiB; total das entradas `<= 32 KiB`).
- Rejeitar recursivamente qualquer chave conversacional proibida (`FORBIDDEN_CONVERSATIONAL_KEYS`: `messages`, `transcript`, `chat_history`, `conversation`, `assistant_text`, `raw_stdout`, `raw_stderr`, `stdout`, `stderr`, `agent_output`).

### 4. Autorização do adaptador e pacote selado (`udh/ticket_handoff.py`)

- Exigir `from_adapter` e `to_adapter` válidos e distintos (ex.: `pi → opencode`), selecionando/autorizando `to_adapter` via `select_adapter(..., exclude=(from_adapter,))` e Política v2 (`ALLOW`, `preflight_status != 'NOT_READY'`).
- Montar `ManagedTicketHandoffPacket` (`build_managed_handoff_packet`), selado por `packet_sha256` sobre JSON canônico (`schema_version: 1`, `packet_kind: 'managed_ticket_handoff'`), contendo:
  - identidade da geração (`change_id`, `spec_version`, `spec_sha256`, `approval_revision`, `plan_sha256`);
  - alvo (`run_id`, `run_revision`, `ticket_id`, `contract_sha256`, `at_version`, `task_contract` com `objective`, `coverage`, `checks`, `allowed_tools`);
  - `authorized_context` (`allowed_context_ids`, `context_ids`, `items` ordenados por ID);
  - `from_adapter`, `to_adapter`;
  - `predecessors` ordenados por `ticket_id` (com `run_id`, `run_revision`, `checkpoint_id`, `last_confirmed_step`, `acceptance_snapshot_sha256`, `at_evidence_id`, `at_evidence_sha256`, `review_evidence_id`, `review_evidence_sha256`, `commit_sha`, `artifact_digest`, `worktree_snapshot_sha256`);
  - `git` (`base_commit_sha`, `head_commit_sha`, `worktree_snapshot_sha256`, `changed_files`).
- `verify_managed_handoff_packet(packet)` valida esquema, digests, ausência de chaves conversacionais e integridade de `packet_sha256`.
- `render_managed_handoff_prompt(packet)` renderiza prompt determinístico mínimo exclusivamente a partir do pacote verificado.

## Cenário offline (`TICKET-02`) e coleta confiável de evidências

Cenário determinístico 100% offline com duas tarefas dependentes (`T1` → `T2`) no mesmo plano aprovado (SQLite v8/v9) e nas imagens OCI reais já verificadas:

1. **Plano e contratos:** aprovar entrada com `T1` (direta) e `T2` (direta, `depends_on=['T1']`), materializar e reconciliar o plano; selar suítes de AT distintas para `T1` e `T2` na `TrustStore`; criar runs gerenciados e registrar contratos `B4.2` (com `context_ids` ⊆ `allowed_context_ids` em `T2`).
2. **Gate antes do aceite:** comprovar que `T2` não inicia (`start`) enquanto `T1` não tiver aceite oficial — inclusive após `T1` concluir como `DONE` mas antes do `REVIEW_REPORT` + `accept_managed_ticket`.
3. **Execução de `T1` no Pi 1.2.0:** iniciar `T1`, executar `PiAdapter` (`network=none`, Gateway `stub`, `git_dir` externo, `TrustStore` inacessível) gravando implementação de `T1` e emitindo texto conversacional com marca sentinela exclusiva da sessão do Pi. Core commita o worktree limpo, promove artefato com `commit_sha`, executa `TrustedExecutor.verify_run(..., use_artifact_image=True)` (AT selado de `T1` → `PASS`), grava checkpoint `F-03` limpo, conclui `T1` (`DONE`), registra revisão independente `F-02` e grava aceite oficial (`accept_managed_ticket`).
4. **Handoff `Pi → OpenCode` para `T2`:** iniciar `T2` pelo gate transacional (`start(..., trust_store=...)`), construir e verificar `ManagedTicketHandoffPacket` recuperando contexto autorizado + checkpoint `F-03` + evidências verificadas de `T1`, e comprovar ausência da marca sentinela conversacional do Pi no pacote e no prompt renderizado.
5. **Execução de `T2` no OpenCode 1.18.35 e evidência final:** executar `OpenCodeAdapter` em sua imagem OCI dedicada (`network=none`, E3 `allowed_tool_names`, E4 config protegida, `git_dir` externo) recebendo apenas o prompt renderizado, estendendo o worktree entregue por `T1`. Core commita worktree limpo, promove artefato com `commit_sha`, executa `TrustedExecutor.verify_run(..., use_artifact_image=True)` (AT selado de `T2` → `PASS`), grava checkpoint `F-03`, conclui `T2` (`DONE`), registra revisão independente `F-02` e grava aceite oficial de `T2`.

## Critérios verificáveis e arquivos permitidos

| ID | Resultado verificável | Teste complementar proposto |
|---|---|---|
| C5-T4-01 | `T2` permanece bloqueado para início e handoff antes do aceite oficial de `T1` (inclusive com `T1` em `DONE` sem aceite). Após aceite na mesma geração, `start` libera `T2` e o pacote deriva objetivo, cobertura, `checks`, predecessora e `context_ids` apenas do SQLite/TrustStore. | `test_blocks_handoff_until_predecessor_officially_accepted`; `test_builds_verified_managed_handoff_from_persisted_state` |
| C5-T4-02 | Controles negativos falham `fail-closed`: `context_refs` faltante/extra/não autorizado, chave conversacional proibida, pacote adulterado, checkpoint `F-03` ausente/com `UNKNOWN`/com commit divergente, worktree sujo, evidência `F-04`/`F-02` adulterada na `TrustStore`, plano/geração obsoleta e adaptador não conforme. | `test_rejects_unauthorized_context_and_conversational_keys`; `test_rejects_tampered_evidence_checkpoint_worktree_or_stale_plan` |
| C5-T4-03 | Cenário offline real `Pi 1.2.0 → OpenCode 1.18.35` com duas tarefas dependentes no mesmo plano persistido, `git_dir` externo, `TrustedExecutor` `F-04` na imagem `artifact-only`, revisão `F-02`, aceite oficial de `T1` e `T2`, e ausência comprovada de transcrição do Pi no prompt entregue ao OpenCode. | `test_offline_pi_to_opencode_dependent_tickets_e2e` |

**Arquivos permitidos para T4:**
- `udh/ticket_store.py` (apenas método de leitura transacional do contexto verificado de handoff)
- `udh/service.py` (exposição desse método em `CoreService`)
- Novo `udh/ticket_handoff.py`
- Novo `tests/test_managed_ticket_handoff.py`
- Documentação ativa (`openspec/changes/discover-plan-tickets/t4-managed-handoff.md`, `tasks.md`, `proposal.md`, `STATE.md`, `MEMORY.md`, `AGENTS.md`, `README.md`, `openspec/project.md`)

**Verificação:** `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests -p 'test_managed_ticket_handoff.py'` (incluindo o teste real com contêineres `Pi`, `OpenCode` e `artifact-only`), seguido pela suíte complementar completa de `STATE.md`. `POC-03` e os 50 AT selados (`v1–v6`) permanecem intocados.
