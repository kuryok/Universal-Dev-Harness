# Tarefas — Change 5 (`discover-plan-tickets`)

**Branch:** `feat/change5-discover-plan-tickets`. **Retomada:** qualificar a inferência offline e aprovar [T3b.2b](t3-discovery-and-spec-handoff.md#t3b2b--integrar-o-agente-e-executar-o-encaminhamento-real).

- [x] **T1 — Validação determinística de propostas do PLAN.** `udh/ticket_plan.py` e `tests/test_ticket_plan.py` cobrem C5-01–04 com Python stdlib, sem persistência ou integração à execução. Em 24/11/2026: 6/6 testes focados (`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_ticket_plan.py'`) e 163/163 complementares PASS. Isso verifica T1, não o aceite oficial do change.
- [x] **T2 — Materialização persistente e gate de dependências (`TICKET-01`).** [Delta](t2-persistence-and-start.md): T2a e T2b implementadas com verificação complementar. Aceite oficial no fechamento.
  - [x] **T2a:** migração opt-in v4→v5, entrada aprovada imutável, materialização e reconciliação. 7 testes de persistência incluídos nos 170 complementares PASS em 24/11/2026.
   - [x] **T2b:** [escopo](t2b-managed-ticket-start.md).
    - [x] **T2b.1:** vínculo atômico run–ticket, migração opt-in v5→v6 e início gerenciado bloqueado. 7 novos testes incluídos nos 177 complementares PASS em 24/11/2026.
     - [x] **T2b.2:** contrato protegido e integração ao executor.
      - [x] **T2b.2a:** migração opt-in v6→v7, registro imutável da cobertura B4.2 e verificação do manifesto integral. 5 novos testes incluídos nos 182 complementares PASS em 24/11/2026.
      - [x] **T2b.2b:** vínculo do executor ao contrato protegido e revalidação transacional antes do `PASS`. 6 novos testes incluídos nos 188 complementares PASS em 24/11/2026.
      - [x] **T2b.3:** aceite oficial de predecessoras e autorização atômica de início.
       - [x] **T2b.3a:** migração e registro de aceite a partir do F-04 e do F-02 independente. 4 novos testes incluídos nos 192 complementares PASS em 24/11/2026.
        - [x] **T2b.3b:** gate transacional de início e dependências. 5 novos testes incluídos nos 197 complementares PASS em 24/11/2026.
- [ ] **T3 — Descoberta proporcional e handoff para SPEC (`DISC-01`).** [Delta proposto](t3-discovery-and-spec-handoff.md).
  - [x] **T3a:** seleção de perguntas no Core, sem dependência nova. 3 testes focados e 200 complementares PASS em 24/11/2026.
  - [ ] **T3b:** skill autorizada, resolução de lacunas e handoff do snapshot OpenSpec aprovado.
    - [x] **T3b.1:** validação local do snapshot com OpenSpec 2.1.1. 6 testes focados (incluindo CLI real) e 206 complementares PASS em 24/11/2026.
    - [ ] **T3b.2:** conectar as rodadas de descoberta ao encaminhamento para SPEC.
      - [x] **T3b.2a:** acompanhar lacunas, respostas e confirmações no Core. 4 testes novos; 210 complementares PASS em 24/11/2026.
      - [ ] **T3b.2b:** integração com o agente e encaminhamento `SPEC_READY`. [Adaptação da skill preparada e conferida](t3-discovery-and-spec-handoff.md#adaptação-da-skill-para-o-udh); faltam aprovação da integração e inferência offline qualificada.
  - [ ] **T3-org:** agrupar os módulos de descoberta e SPEC preservando imports. Adiada, sem aprovação.
- [ ] **T4 — Troca de agente através do plano persistido (`TICKET-02`).** Antes de codificar, aprovar delta com arquivos permitidos, cenário offline, pré-condições de aceite e coleta confiável de evidências.
- [ ] **Fechamento** — Rodar a suíte complementar, `doctor --conformance`, as quatro POCs e a regressão dos 50 AT selados. Executar as verificações oficiais do novo recorte via executor confiável, sem criar ou editar ATs protegidos. Registrar limites e resultados em resumo compacto (`<= 32 KiB`), obter revisão independente R2/F-02 `PASS` e sincronizar os documentos ativos antes de integrar em `main`.
