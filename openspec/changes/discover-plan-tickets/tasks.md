# Tarefas — Change 5 (`discover-plan-tickets`)

**Branch:** `feat/change5-discover-plan-tickets`. **Retomada:** implementar [T2b.3b](t2b-managed-ticket-start.md#t2b3b--autorizar-início-de-tickets-gerenciados), aprovada pelo usuário.

- [x] **T1 — Validação determinística de propostas do PLAN.** `udh/ticket_plan.py` e `tests/test_ticket_plan.py` cobrem C5-01–04 com Python stdlib, sem persistência ou integração à execução. Em 24/11/2026: 6/6 testes focados (`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_ticket_plan.py'`) e 163/163 complementares PASS. Isso verifica T1, não o aceite oficial do change.
- [ ] **T2 — Materialização persistente e gate de dependências (`TICKET-01`).** [Delta](t2-persistence-and-start.md): T2a cobre migração, materialização idempotente e reconciliação; T2b cobre vínculo com run e gate atômico de início. T2 só termina após ambos.
  - [x] **T2a:** migração opt-in v4→v5, entrada aprovada imutável, materialização e reconciliação. 7 testes de persistência incluídos nos 170 complementares PASS em 24/11/2026.
  - [ ] **T2b:** [escopo](t2b-managed-ticket-start.md).
    - [x] **T2b.1:** vínculo atômico run–ticket, migração opt-in v5→v6 e início gerenciado bloqueado. 7 novos testes incluídos nos 177 complementares PASS em 24/11/2026.
     - [x] **T2b.2:** contrato protegido e integração ao executor.
      - [x] **T2b.2a:** migração opt-in v6→v7, registro imutável da cobertura B4.2 e verificação do manifesto integral. 5 novos testes incluídos nos 182 complementares PASS em 24/11/2026.
      - [x] **T2b.2b:** vínculo do executor ao contrato protegido e revalidação transacional antes do `PASS`. 6 novos testes incluídos nos 188 complementares PASS em 24/11/2026.
     - [ ] **T2b.3:** aceite oficial de predecessoras e autorização atômica de início.
       - [x] **T2b.3a:** migração e registro de aceite a partir do F-04 e do F-02 independente. 4 novos testes incluídos nos 192 complementares PASS em 24/11/2026.
       - [ ] **T2b.3b:** gate transacional de início e dependências.
- [ ] **T3 — Descoberta proporcional e handoff para SPEC (`DISC-01`).** Antes de codificar, aprovar delta com recorte de capacidades, arquivos permitidos, avaliação offline e versão/proveniência da CLI. Sem CLI compatível, registrar bloqueio no caminho positivo.
- [ ] **T4 — Troca de agente através do plano persistido (`TICKET-02`).** Antes de codificar, aprovar delta com arquivos permitidos, cenário offline, pré-condições de aceite e coleta confiável de evidências.
- [ ] **Fechamento** — Rodar a suíte complementar, `doctor --conformance`, as quatro POCs e a regressão dos 50 AT selados. Executar as verificações oficiais do novo recorte via executor confiável, sem criar ou editar ATs protegidos. Registrar limites e resultados em resumo compacto (`<= 32 KiB`), obter revisão independente R2/F-02 `PASS` e sincronizar os documentos ativos antes de integrar em `main`.
