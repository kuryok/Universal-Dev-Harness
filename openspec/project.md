# Contexto e processo documental do UDH

## Autoridade e escopo

O [baseline normativo rastreado](../Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md) preserva o propósito (Seção 0), as 28 ADRs, as matrizes e a proveniência. A [nota v2.5.1](../UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md) é derivada para consulta, sem substituí-lo.

**Ratificação formal por delegação humana (23/11/2026):** com autorização explícita do usuário para alinhar as decisões pendentes ao propósito do produto, ficam formalmente ratificadas no desenho:
- **Cláusulas B1–B4** (plataforma Linux-first/WSL2 validada e preflight `doctor`, rede/pacotes/MCPs mediados, perfil `artifact`/preview web isolado e testes de aceite protegidos).
- **Emenda ADR-018 A** (mediação de egress externo pelo Core exclusivamente por finalidades em política versionada, mantendo `network=none` no agente e credenciais de publicação segregadas conforme ADR-007).
- **Congelamento arquitetural:** nenhuma nova ADR será aberta antes da comprovação prática das POCs fundacionais sobre o fluxo real.

Esta ratificação encerra pendências de **desenho**; não altera o status factual `NOT_RUN` de POC-01–04, DOC-01/02, SKILL-01/02, DISC-01 ou TICKET-01/02, nem constitui certificação de implementação ou segurança.

O uso de OpenSpec neste repositório é **documental adaptado**: requisitos e planos dos incrementos estão em changes aprovados sob `openspec/changes/`. Não há CLI `openspec` instalada nem alegação de validação automática canônica. Formalização futura não deve reescrever requisitos ou histórico silenciosamente.

Antes de implementar: confirmar escopo/arquivos/critério de pronto no change, registrar aprovação humana e verificar o incremento. Mudança de requisito exige delta explícito. Não duplicar requisitos em README, STATE, MEMORY ou diagnósticos externos.

## Engenharia, bootstrap e política de evidências

Um Core determinístico decide o estado; SQLite guarda operação; Git guarda código; evidências e testes oficiais têm requisitos próprios de proteção. Componentes permanecem separados por responsabilidade.

**Diretrizes operacionais alinhadas (23/11/2026):**
- **Bootstrap congelado:** `bootstrap/slice1/` não recebe novos gates, selos (`v7+`) ou expansões. Os selos `v1–v6` permanecem congelados para regressão do recorte SQLite v1–v3; a verificação oficial do próximo marco migrará para o executor confiável do produto.
- **Uma branch por change:** a partir do Change 2, desenvolver cada change OpenSpec em branch dedicada (`feat/<change>`) e integrar em `main` somente após testes, regressão selada, revisão independente R2/F-02 (`PASS`) e sincronização documental, mantendo `main` sempre verde.
- **Política de evidências no Git:** não commitar relatórios JSON crus de múltiplos megabytes em `openspec/` ou `bootstrap/`. Commitar apenas resumos compactos (`<= 32 KiB`) com hashes/contagens verificáveis e preservar o histórico existente sem reescrevê-lo.
- **Dívida técnica v4 resolvida (23/11/2026):** no Change 1 ([v4-state-ledger-and-preflight](changes/v4-state-ledger-and-preflight/proposal.md)), bancos v4 adotam ledger unificado `transitions`, leitura `O(1)` via índices B-tree (sem `quick_check`/scan global em `show`/`policy`), integridade profunda desacoplada em `doctor`/`migrate --to 4`, checkpoints F-03 e política v2. A checagem global permanece restrita aos caminhos legados v2/v3 para manter regressão verde nos 50 AT congelados `v1–v6`.
- **Prioridade — marco vertical de produto:** (1) *(concluído)* Change 1: ledger v4 + leitura `O(1)` + política v2 + `doctor`; (2) *(próximo)* Change 2: Runtime Manager rootless com imagem OCI dedicada por digest + Gateway stdlib endurecido (dublê + OpenRouter `:free`) + adaptador Pi 1.2.0 + Executor Confiável F-04 (stack Python 3 stdlib) + execução de `POC-01`/`02`/`04`; (3) perfil `artifact`, preview local ("Ver o app") e interface guiada mínima; (4) segundo agente (OpenCode) + `POC-03`.

## Estado, segurança e retomada

Consultar [STATE.md](../STATE.md) e [MEMORY.md](../MEMORY.md) para o estado corrente. O baseline é um snapshot de desenho; seus estados históricos não são evidência de execução atual.

POC-01–04 seguem `NOT_RUN`. Bootstrap e spike não certificam isolamento/segurança do produto, store oficial protegida ou entrega. Não abrir credenciais reais nem alterar o workspace sensível do spike sem autorização própria. O repositório remoto `origin` está configurado em `https://github.com/kuryok/Universal-Dev-Harness.git`.

[Alinhamento de 27/10](changes/slice1-evidence-storage/alignment.md) e [auditoria reproduzida](changes/slice1-evidence-storage/report-audit.md) preservam o registro histórico das correções anteriores.
