# Tarefas — Change 5 (`discover-plan-tickets`)

**Branch:** `feat/change5-discover-plan-tickets`. **Retomada:** todos os gates offline (`225/225` complementares, `doctor --conformance` `READY`, `4/4` POCs, `50/50` AT selados `v1–v6` e R2/F-02 `PASS` em `089530d`) estão verdes ([resumo](verification-summary.json)). Merge em `main` aguarda apenas o ensaio ao vivo `DISC-01` com modelo real (OpenCode Zen retornou `HTTP 429` em `deepseek-v4.1-flash-free` com `'public'` e com chave pessoal; pendente de outro modelo/provedor de API ou Ollama local).

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
      - [x] **T3b.2b:** implementação da integração com o agente e do encaminhamento `SPEC_READY`. Seis testes focados e 216 complementares PASS; ensaio oficial com modelo real pendente.
  - [ ] **T3b.2c / Ensaio `DISC-01` com modelo real**: [delta aprovado](t3-discovery-and-spec-handoff.md#t3b2c--ensaio-real-de-disc-01-via-gateway-endurecido-opencode_zen_free). Código e testes com dublês (`C5-T3b.2c-01`/`02`) implementados e verificados (**225/225** PASS em 25/11/2026); falta executar o ensaio ao vivo `C5-T3b.2c-03` com modelo `-free` do OpenCode Zen (mantendo Ollama local como pendência futura).
  - [ ] **T3-org:** agrupar os módulos de descoberta e SPEC preservando imports. Adiada, sem aprovação.
- [x] **T4 — Troca de agente através do plano persistido (`TICKET-02`).** [Delta aprovado](t4-managed-handoff.md): `udh/ticket_handoff.py`, leitura transacional em `udh/ticket_store.py`/`udh/service.py` e `tests/test_managed_ticket_handoff.py`. Em 25/11/2026: 7/7 testes focados PASS (incluindo cenário E2E offline real `Pi 1.2.0 → OpenCode 1.18.35 + artifact-only F-04`, adulteração `F-02` e múltiplas predecessoras) e 223/223 complementares PASS. Aceite oficial no fechamento.
- [ ] **Fechamento** — **Gates offline concluídos em 25/11/2026 ([resumo](verification-summary.json)):** `225/225` complementares PASS (`089530d`), `doctor --conformance` `READY`, `4/4` POCs PASS, `50/50` AT selados `v1–v6` PASS (714 chamadas, 0 infra) e revisão independente R2/F-02 `PASS` (`089530d`). **Pendência para merge em `main`:** concluir ensaio ao vivo `DISC-01` (`C5-T3b.2c-03` bloqueado por `HTTP 429` no OpenCode Zen em `deepseek-v4.1-flash-free`, ou `C5-T3b.2b-03` em Ollama local).
