# Universal Dev Harness 2026 — v2.5 | Baseline normativo consolidado (B-01 corrigido)

**Estado:** documento normativo autossuficiente para revisão e aprovação formal; não é atestado de implementação, segurança ou conformidade. 28 ADRs em CONSENSO DE DESENHO; POC-01–04 = `NOT_RUN`. B1–B4 e emenda ADR-018 A foram tecnicamente aceitas, mas **aguardam aprovação formal do usuário**. F-02–F-06 são requisitos operacionais, não provas.

**Controle de alteração:** consolidação documental B-01 solicitada na última revisão Claude, sem novas decisões arquiteturais. Não substituir estados `NOT_RUN` por `PASS`. O anexo v2.5 complementa exclusivamente o escopo declarado. O histórico cumulativo original permanece preservado em arquivo separado. Correções documentais posteriores, sem novas decisões: emenda ADR-018 A em texto único; B4 com redação única (§22.2); estados superados (§20.5, §20.6, tabelas 17.2/17.3) removidos ou alinhados à §18.1; referências internas pendentes ajustadas; tabela de hashes (§6) conferida contra os arquivos das skills; pacote de segurança corrigido registrado.

**Precedência:** este documento é a versão normativa consolidada; suas seções atuais prevalecem sobre o registro histórico. Em caso de conflito não resolvido entre cláusulas presentes, bloquear implementação afetada e registrar questão, sem escolha silenciosa.

## Índice de decisões

- 0: propósito do produto e requisitos para iniciantes.
- 1: ADR-001–022 e precedência ADR-022/019.
- 2: ADR-023–028 e requisitos de UX.
- 3: B1–B4 literais, emenda aditiva ADR-018 A.
- 4: F-02–F-06 e REVIEW independente.
- 5: adições da v2.5.
- 6: proveniência das skills e status de validação.

---


## 0. PROPÓSITO INEGOCIÁVEL DO PRODUTO — preservar em TODAS as versões

> **Universal Dev Harness 2026 é uma aplicação local-first, orientada para iniciantes e útil também a usuários avançados, que transforma uma intenção escrita em linguagem natural em software funcional e verificável, conduzindo todo o ciclo de desenvolvimento (SDLC) com agentes de IA intercambiáveis, automação proporcional ao risco, memória persistente, segurança por padrão e entrega explicável.**

**Usuário-alvo primário:** pessoa que não sabe programar (vibe coder). Ela deve conseguir descrever uma ideia, entender as decisões em linguagem comum, aprovar requisitos e acompanhar a construção sem conhecer Git, MCP, OpenSpec ou testes. Usuários técnicos podem inspecionar, configurar e substituir componentes.

**Experiência principal:** ideia → descoberta guiada → requisitos/critérios de aceite → especificações e tickets → implementação isolada → testes e correções em loop limitado → revisão independente → entrega executável/documentada/PR → continuidade entre sessões e agentes.

**Funcionalidades centrais que NÃO podem desaparecer do roadmap:**
1. Interface guiada de descoberta, explicações e bloqueios compreensíveis.
2. Suporte real a **Pi Agent, OpenCode e Codex**, com troca de agente preservando estado.
3. SDLC com **OpenSpec**, tickets pequenos e verificáveis, acompanhamento e aprovação proporcional.
4. **Skills** de qualidade e segurança e **MCPs sob demanda** (Context7, GitHub, Playwright).
5. **Memorix** como memória semântica persistente; SQLite/Git/OpenSpec/evidências são suas respectivas fontes autoritativas.
6. Loop de implementação, testes, diagnóstico e correção com limites e sem mascarar falhas.
7. **LLM Judge consultivo no MVP**, sem poderes de autorização/veto; métricas de qualidade e revisão humana quando necessário.
8. Entrega profissional: código, testes, documentação, histórico, relatório e PR/release quando autorizado.
9. Execução segura por padrão e recuperação após falhas/desligamento sem perda do progresso.
10. **Baixo overhead:** não adicionar agentes, processos, MCPs, estados nem burocracia sem benefício observável.

**Ratificação explícita do Product Charter:** o DeepSeek ratificou integralmente a seção 0 na devolutiva da v1.6; ChatGPT confirma e declara esta seção PERMANENTE e INEGOCIÁVEL, sujeita apenas a adendos consensuais que não a descaracterizem.

**Adendo ao propósito — proposta de aceite:** software funcional significa cumprir critérios de aceite aprovados pelo usuário com evidências reproduzíveis pertinentes e orientação para instalar/executar/manter. Não alegar funcionalidade comprovada sem essa evidência. Exceções a entrega completa (protótipo, spike, documentação) devem ser escopadas explicitamente e rotuladas como tal; não mascarar entrega parcial como aplicativo pronto.

**Critério de sucesso do PRODUTO (distinto das POCs de segurança):** um iniciante conclui uma pequena aplicação de ponta a ponta, consegue entender aprovações e bloqueios, retomar sessão e mudar de agente, e recebe software testado com evidências reais. A POC-01 valida infraestrutura de isolamento, não valida esse propósito.

**Regra de governança:** toda revisão deve conter esta seção 0 e uma **Matriz de Cobertura do Propósito** atualizada. Avançar para a Fase A não significa que as camadas B (SDLC/agentes) e C (experiência do usuário) deixaram de existir; significa apenas que são implementadas após os gates fundacionais pertinentes. Projeto ainda em CONSENSO/DESIGN; nenhuma implementação ou POC foi demonstrada.

### Matriz de cobertura do propósito

| Área | Baseline de design | Implementação/validação |
|---|---|---|
| Orientação ao iniciante e interface clara | Requisito de produto | NÃO IMPLEMENTADO |
| SDLC, OpenSpec, specs e tickets | Arquitetura prevista | NÃO IMPLEMENTADO |
| Pi/OpenCode/Codex e handoff | ADR-011, POC-03 | NOT_RUN |
| Skills e MCPs mínimos | ADR-012 | NÃO IMPLEMENTADO |
| Memorix e continuidade | ADR-011 | NÃO IMPLEMENTADO |
| Loop, testes, Judge e verificação | ADR-006/014/022 | NÃO IMPLEMENTADO |
| Segurança do runtime | ADR-001–022, POC-01/02/04 | NOT_RUN |
| Entrega funcional e explicável | Requisito de produto | NÃO IMPLEMENTADO |

---

## 1. ADR-001–022 (texto de decisão preservado)

### Registro cumulativo das ADRs (ADR-001–022)

| ID | Decisão consolidada | Estado | Evidência necessária |
|---|---|---|---|
| ADR-001 | **Runtime Manager externo** é a fronteira primária de isolamento; hooks, skills e permissões dos agentes não são sandbox de SO. | CONSENSO | POC-01 |
| ADR-002 | Contrato declarativo de `capabilities` com **fail-closed**; configuração incompatível ou sem verificação suficiente não executa tarefa com escrita. | CONSENSO | POC-01 + testes negativos |
| ADR-003 | **Git worktree isola estado Git, não processos**; escrita exige isolamento efetivo do SO. | CONSENSO | POC-01 |
| ADR-004 | **Políticas, baselines e testes protegidos** residem fora do workspace modificável; alterações não autorizadas são rejeitadas. Não se promete detectar toda mudança semântica. | CONSENSO | POC-02 |
| ADR-005 | **Exit codes, logs e resultados oficiais** são coletados por executor confiável fora do controle do agente e vinculados ao run. | CONSENSO | POC-04 |
| ADR-006 | **LLM Judge é auxiliar e não habilitador**: jamais aprova execução/publicação sozinho nem substitui gates determinísticos. Eventual veto consultivo e tratamento de incertezas seguem política explícita. | CONSENSO | Cenários de falha e bypass |
| ADR-007 | Perfil **Release** separado de Developer, com credenciais de publicação não acessíveis pelo agente implementador. | CONSENSO | Teste de segregação |
| ADR-008 | Congelar expansão arquitetural em **Security Foundation v1.2** até passar nas quatro POCs, repetidamente e com evidências. | CONSENSO | POCs 01–04 |
| ADR-009 | OpenSpec, Memorix, MCPs, skills, Judge e UI são camadas de produto que não substituem nem antecedem a comprovação da fundação de segurança. | CONSENSO | Roadmap de implementação |
| ADR-010 | Definição de pronto: POCs verdes, configurações não conformes recusadas, handoff funcional, evidências íntegras e bloqueios inteligíveis ao iniciante. | CONSENSO | Relatório de aceite |
| ADR-011 | **Estado do trabalho é autoritativo no Harness Core/SQLite; código no Git; especificação no OpenSpec; evidências no executor; memória semântica no Memorix.** Trocar agente não significa transferir confiança à conversa. | CONSENSO | POC-03 |
| ADR-012 | **MCPs e skills sob demanda e com mínimo privilégio**; Context7, GitHub e Playwright não precisam ficar todos ativos globalmente. | CONSENSO | Revisão de permissões |
| ADR-013 | **Erro de segurança deve vir de código estruturado emitido pelo gate**, e a UI o explica em linguagem acessível; explicações opcionais de LLM não alteram a decisão. | CONSENSO | Testes de mensagens e bloqueios |
| ADR-014 | Loop de correção com limites de tentativas/tempo/recursos, integridade de políticas e testes protegidos; falha persistente é bloqueada e registrada, não mascarada. | CONSENSO | POC-02 + loop de regressão |
| ADR-015 | **Baselines no MVP:** SHA-256 de snapshots/testes protegidos, com valor de referência em domínio não gravável pelo agente; HMAC-SHA256 é hardening opcional sob modelo de ameaça e gestão de chaves definidos. | CONSENSO | POC-02 |
| ADR-016 | **Evidências oficiais:** armazenamento protegido contra escrita pelo agente, hash SHA-256 vinculado a `run_id` e conferência no gate; atestação assinada é evolução opcional. | CONSENSO | POC-04 |
| ADR-017 | **Runtime MVP:** contêiner rootless Linux é candidato preferencial, jamais suficiente por si; requer inspeção da configuração e testes negativos de socket, volumes, symlink, rede, privilégios e UID/GID. | CONSENSO | POC-01 |
| ADR-018 | **Gateway de inferência:** nenhum acesso arbitrário à rede pelo agente; chamadas externas de LLM mediadas por gateway fora do sandbox, com credenciais exclusivas, autenticação/escopo, allowlist e auditoria. Preservar `network none` no processo de agente onde a implementação suportar IPC local controlado. | CONSENSO | POC-01 + prova de comunicação e anti-exfiltração |
| ADR-019 | **Juiz diante de `ABSTAIN`:** somente aconselha; `VETO` produz retenção temporária revisável; `PASS` nunca habilita execução ou entrega sozinho. Falsos positivos são medidos e decisões críticas seguem humano ou mecanismo confiável. | CONSENSO | Testes de decisão e processo de revisão |
| ADR-020 | **Transporte de inferência:** UDS é IPC preferencial, se viável e conforme; exige verificação de permissões, peer, UID/GID rootless, montagem e ciclo de vida. TCP em namespace isolado não é proibido, mas **deve comprovar que nenhum processo fora do sandbox consegue alcançar a porta e que a autenticação não depende apenas de estar no loopback**. Ponte HTTP local só com controle de egress e mesma avaliação. | CONSENSO | POC-01 + testes de transporte |
| ADR-021 | **Modelo de ameaça do MVP:** adversário controla agente e filhos, pode escrever no workspace e falsificar saídas; não pressupõe comprometimento inicial do host, do Runtime Manager ou do Harness Core. Testar socket do runtime, symlink, mounts, capabilities efetivas, namespaces e canais de inferência. Contêiner compartilha kernel; inspeção de payload é mitigação parcial, **não demonstra ausência de exfiltração**. | CONSENSO | POC-01 + POC-04 |
| ADR-022 | **Judge no MVP / precedência sobre ADR-019:** `DENY` determinístico não pode ser revertido pelo Judge. No MVP, Judge opera **somente em `shadow/advisory`**, sem veto nem aprovação automática. **ADR-019 permanece como desenho-alvo pós-MVP**: retenção temporária revisável apenas sobre `ABSTAIN`, condicionada a métricas calibradas de falso positivo/abstenção, política de revisão humana aprovada e evidências de resistência a prompt injection no domínio. | CONSENSO | Testes de precedência; avaliação pós-MVP |

### Interpretação normativa ADR-019 e ADR-022

Durante todo o MVP, prevalece **ADR-022** quanto à operação: nenhuma saída de LLM Judge altera `ALLOW`, `DENY` ou o fluxo de execução. ADR-019 não autoriza ativar o veto antes de ratificação e validação da fase posterior. O Judge **nunca** substitui a autorização do Policy Engine. Atualizações de política exigem processo administrativo versionado, não um override informal de `DENY`.

## 2. ADR-023–028, ratificação e UX

### 17.2 Resposta bilateral às propostas ADR-023–026

O DeepSeek respondeu ACEITO COM AJUSTES a ADR-023–026. ChatGPT **aceita os ajustes no sentido técnico** e consolida a redação abaixo. A ratificação final consta na §18.1 (CONSENSO).

| ID | Redação normativa proposta para ratificação | Status |
|---|---|---|
| **ADR-023** | **SDLC adaptativo:** o Core seleciona modo **FAST / STANDARD / DEEP** por risco, impacto, dados sensíveis, autenticação, infraestrutura, contrato público, reversibilidade e exposição — **não pelo tamanho do diff**. Incerteza material eleva o rigor ou exige esclarecimento, nunca reduz silenciosamente. Mesmo FAST preserva isolamento, integridade, rastreabilidade e verificação de critérios de aceite proporcionais. O iniciante não tem obrigação de selecionar o modo. | CONSENSO (ver 18.1) |
| **ADR-024** | **Roteamento de capacidades:** um mapeamento determinístico e versionado de **tipo/risco do ticket → conjunto máximo autorizado de skills e MCPs** controla o acesso. O agente pode solicitar apenas capacidades da allowlist; solicitações fora dela são negadas ou passam por processo externo de autorização, nunca autoelevação por prompt. Carregar somente o necessário, com limites de contexto, origem confiável e auditabilidade. O modelo pode sugerir a categorização de um ticket, mas essa sugestão não confere permissões: o Core aplica regras verificáveis e, ante ambiguidade relevante, falha fechado/escalona. | CONSENSO (ver 18.1) |
| **ADR-025** | **Bloqueios explicáveis e acionáveis:** o executor/Policy Engine produz código estruturado de **violação de política**, distinto de **falha funcional/teste**. A UX apresenta **o que ocorreu, por que importa, o que foi realmente verificado e a próxima ação segura**. Ações propostas devem corresponder à causa e às permissões, nunca oferecer desativar proteção crítica. Exceções administrativas são externas, explícitas e auditáveis. Um LLM pode reescrever a explicação, mas nunca definir o veredito nem o bypass. | CONSENSO (ver 18.1) |
| **ADR-026** | **Seleção de agente:** no modo iniciante, o Core seleciona automaticamente entre agentes **disponíveis e comprovadamente compatíveis** com as capacidades, políticas e modelo de execução do ticket. O fallback segue ordem **configurável, estável, registrada e justificável**, sem relaxar requisitos de segurança. Nenhuma ordem fixa como OpenCode → Pi → Codex é universal. Modo avançado permite escolha consciente, sujeita ao mesmo contrato de segurança. Handoff usa estado autoritativo e necessita prova na POC-03, não se presume resolvido apenas por integrações externas. | CONSENSO (ver 18.1) |

### 17.3 Novas decisões propostas: contexto e definição de pronto

| ID | Redação normativa proposta | Status |
|---|---|---|
| **ADR-027** | **Contexto mínimo, efêmero e recuperável por ticket:** cada execução recebe intenção/critérios de aceite, ticket/spec, estado versionado, código relevante, dependências explícitas, ferramentas autorizadas e resultados recentes de verificação. Decisões duráveis e convenções relevantes são recuperadas seletivamente do Memorix. Ao terminar o ticket, persistir entregáveis, evidências, estado e síntese durável; descartar o contexto conversacional efêmero. Não descartar dependências necessárias nem tratar Memorix/conversa como estado autoritativo (ADR-011). Implementar como função do mesmo orquestrador, sem serviço adicional obrigatório. | CONSENSO (ver 18.1) |
| **ADR-028** | **Contrato verificável de entrega:** distinguir `IMPLEMENTED`, `TESTED`, `ACCEPTED` e `DELIVERED`, com evidências e critérios claros por ticket/projeto. `DELIVERED` exige critérios de aceite aprovados ou exceções explícitas para protótipos/spikes, verificações pertinentes, artefato utilizável, documentação prática e **smoke test da forma real de executar/instalar o resultado**, sempre que aplicável. PR/merge/release exigem autorizações da política de entrega. Usuário iniciante aprova por comportamento compreensível, não leitura obrigatória de diff. Não equiparar lint/Judge/testes unitários isolados a software funcional. | CONSENSO (ver 18.1) |

**PROP-UX:** requisito operacional (não ADR). Validar a jornada com iniciantes reais, incluindo descoberta, acompanhamento, bloqueio, retomada, execução da entrega e necessidade de decisões técnicas; métricas locais opcionais e consentidas, sem telemetria obrigatória.

## 18. RATIFICAÇÃO FINAL DO DESENHO FUNCIONAL — v1.9 (NORMATIVA)

**Fonte:** resposta do DeepSeek à v1.8: ADR-023, ADR-024, ADR-025, ADR-026, ADR-027 e ADR-028 marcadas individualmente **ACEITO**; ChatGPT aceita a ratificação sem alterações de escopo. As redações consolidadas na seção 17 prevalecem; a tabela histórica da seção 12 não deve ser usada para determinar status atual. Assim, **28 ADRs em CONSENSO, nenhuma em VALIDADA**.

### 18.1 Registro de ratificação

| Decisão | Baseline normativo de design | Status |
|---|---|---|
| ADR-023 | Modo automático FAST/STANDARD/DEEP por impacto/risco e reversibilidade, não tamanho do diff; os controles obrigatórios nunca são removidos. | CONSENSO |
| ADR-024 | Roteamento por ticket de skills/MCPs por allowlist de política; sugestão do modelo não é permissão. | CONSENSO |
| ADR-025 | Bloqueios explicáveis e acionáveis: o quê, impacto, evidência verificada e próximo passo seguro; política e UX separados. | CONSENSO |
| ADR-026 | Seleção automática e fallback determinístico entre Pi/OpenCode/Codex sob contrato de capacidades, com seleção avançada opcional. Handoff ainda requer POC-03. | CONSENSO |
| ADR-027 | Contexto mínimo e efêmero por ticket; dependências explícitas; memória semântica no Memorix, estado autoritativo SQLite/Git/OpenSpec/evidências. | CONSENSO |
| ADR-028 | `IMPLEMENTED → TESTED → ACCEPTED → DELIVERED` com evidência, critérios de aceite, documentação praticável e smoke test do artefato/instrução real. | CONSENSO |

### 18.2 Decisão de simplificação

Um **único Harness Core** contém funções determinísticas de classificação de risco, roteamento de capacidades, montagem de contexto e seleção de agente. **Não criar microserviços, subagentes ou loops independentes apenas para essas funções**. OpenSpec é integrado como sistema de especificações, não como um segundo orquestrador. Um LLM pode ajudar no entendimento de linguagem natural e autoria de specs, mas **não concede privilégios ou redefine política**.

### 18.3 Requisitos de UX aceitos (critérios operacionais, sem novas ADRs)

**UX-01 — Progresso e aprovações:** mostrar ticket ativo e status `PENDING | IN_PROGRESS | BLOCKED | DONE`, com progresso baseado em contagem real de tickets, não percentual inventado; exibir bloqueio com as quatro informações da ADR-025; exibir aprovações inteligíveis com impactos; nunca derivar status da autoafirmação do agente. Verificar usando estado SQLite e relatório falsificado de agente.

**UX-02 — Alteração de escopo:** registrar change request ligado a OpenSpec e tickets prévios; apresentar impacto e reclassificação de risco antes da aceitação; preservar evidências e tickets aprovados; reabertura de trabalho previamente aceito exige confirmação explícita. Verificar incluindo login após três tickets já finalizados.

**UX-03 — Primeira entrega:** executar smoke test pela via real documentada, conferir comandos/arquivos existentes e só conceder `DELIVERED` se os critérios forem atendidos; protótipo/spike/documento rotulado explicitamente, sem falsa declaração de aplicativo pronto.

**UX-04 — Usabilidade mensurável:** validar em estudo de tarefas com iniciantes a criação, interrupção/retomada, compreensão de bloqueio e primeira execução do software; não exigir familiaridade com Git, agentes ou MCPs. Esta é exigência de aceitação do produto, **não subsistema de telemetria obrigatório**.

### 18.4 Matriz de cobertura do PROPÓSITO ORIGINAL

| Objetivo irrenunciável | Contrato arquitetural | Validação futura |
|---|---|---|
| Vibe coding guiado para iniciantes | Product Charter; UX-01/02/04; ADR-025/026 | Teste de jornada ponta a ponta com usuário iniciante |
| Ideia até especificação e tickets | OpenSpec; ADR-023/027; SDLC adaptativo | Aceite de spec/tickets e critérios observáveis |
| Pi/OpenCode/Codex intercambiáveis | ADR-011/026/027 | POC-03: handoff reprodutível |
| Skills/MCPs mínimos (Context7, GitHub, Playwright) | ADR-012/024 | Auditoria de capacidades por ticket |
| Memória persistente sem estado conversacional autoritativo | ADR-011/027 | Reinício + recuperação com SQLite/Git/OpenSpec/Memorix |
| Correção em loop, testes e Judge consultivo | ADR-014/022/023 | Falhas, retentativas limitadas, teste real, avaliação consultiva |
| Segurança que não depende de obedecer ao agente | ADR-001–022 | POC-01/02/04 |
| Software entregue, executável e explicado | ADR-028; UX-03 | Aceite + smoke test + instruções testadas |
| Simplicidade e baixo overhead | Harness Core único; roteamento mínimo | Comparar FAST com DEEP; evitar orquestração desnecessária |

### 18.5 Interpretação de congelamento e status

- **CONGELADO = baseline de arquitetura de produto acordado**, não afirmação de correção do código, nem proibição de melhorar o projeto mediante nova evidência.
- **VALIDADA = somente após experimentos com logs, hashes, `run_id` e reproduções correspondentes.** Nenhuma POC foi executada.
- O congelamento não obriga começar implementação imediatamente: **o usuário decide o momento**.
- A ordem de construção proposta preserva A (fronteiras de segurança) para suportar B (fluxo de desenvolvimento) e C (experiência); o sucesso final requer A+B+C, não apenas POC-01.

### 18.6 Próxima etapa de design, se solicitada

Em vez de reabrir genericamente as ADRs, realizar uma **revisão ponta a ponta com um caso concreto de iniciante**, atravessando discovery, risco FAST/DEEP, spec/tickets, ferramentas, handoff, erro bloqueado, mudança de escopo, aprovação e entrega testada. Qualquer defeito descoberto vira ajuste rastreável, não nova arquitetura automaticamente. Sem implementação e sem simular POCs.


---

## 3. Cláusulas literais B1–B4 e emenda ADR-018 A

#### B1 — Plataforma, instalação inicial, agentes e interface local

**B1.1 Plataformas.**
 (a) Referência de conformidade e desenvolvimento: Linux x86_64 com contêiner rootless (ADR-017). POC-01–04 são executadas primeiro nesse ambiente.
 (b) Alvo de produto para usuários Windows: Windows 11 com uma distro WSL2 dedicada, gerenciada pelo Harness, executando o mesmo domínio Linux. Só é oferecida depois que a mesma matriz de conformidade for executada dentro dela e resultar `COMPLIANT`. Se falhar, o produto permanece Linux-first e o isolamento não é reduzido para dar paridade.
 (c) macOS: roadmap, sem compromisso no MVP.
 (d) Cada plataforma tem veredito próprio. Nenhuma paridade é presumida.

**B1.2 Domínio Linux controlado.** Core, Runtime Manager, executor, mediador de egress (que contém o gateway de inferência), coletor de evidências e interface local rodam no domínio Linux. Agentes e navegador de testes rodam em imagens identificadas por digest. O sistema hospedeiro não recebe credenciais, agentes nem código gerado em execução.

**B1.3 Preflight (`doctor`).** É função do Core. Verifica:

- kernel, cgroup, user namespaces, subuid/subgid e recursos mínimos;
- runtime rootless e mapeamentos UID/GID (DEF-02);
- imagens por digest;
- mediador de egress, com chamada de teste, e credenciais;
- cada agente, por **conformance de adaptador**: executar uma tarefa trivial dentro do sandbox, com o endpoint do mediador e sem chave real, consumindo uma ferramenta MCP mediada quando aplicável;
- conformance da plataforma, em cache por `policy_sha256` mais digest.

O resultado é `READY`, `READY_WITH_LIMITS` (lista agentes ou capacidades indisponíveis) ou `NOT_READY`. Cada código de motivo mapeia para uma ação em linguagem comum (ADR-025). A execução Developer exige `READY`, ou `READY_WITH_LIMITS` com ao menos um agente compatível.

**B1.4 Correção do ambiente.** Ações em espaço de usuário (baixar imagem, criar diretórios) podem ser automáticas. Ações privilegiadas (habilitar WSL2 ou virtualização, instalar runtime, entradas subuid) exigem um passo guiado com consentimento explícito. Se o sistema exigir reinício ou ação de firmware, o preflight informa. Nunca é silenciosa, e nunca exige que o usuário edite configuração.

**B1.5 Agentes, imagens e credenciais.** Versões são fixadas por digest, e mudar o digest ou a política invalida a conformance (`STALE`). Credenciais ficam só no domínio do Core. Agente ou modelo cuja autenticação ou configuração de endpoint não funcione atrás do mediador é marcado `INDISPONÍVEL`, sem relaxar política.

**B1.6 Classificação de liberação.**

- *MVP completo:* os três agentes passam a conformance de adaptador na plataforma de referência, e há handoff comprovado com cada agente como origem e como destino ao menos uma vez (POC-03).
- *MVP limitado, rotulado:* ao menos dois agentes com handoff comprovado entre eles. Os ausentes permanecem no roadmap do Charter e a interface informa quais.
- Abaixo disso, não é liberável como produto para iniciantes.

Esta cláusula não altera o Charter. Ela só classifica a liberação.

**B1.7 Interface web local.** Servida pelo Core em loopback.
 (a) Valida `Host` e `Origin` de toda requisição, contra DNS rebinding e requisições originadas em outros sites.
 (b) A autenticação usa token de sessão em cabeçalho, não cookie compartilhável.
 (c) Ações com efeito (aprovar, executar, publicar) exigem o token.
 (d) O token nunca é exposto ao preview (B3.6).

**B1.8 Política.** No modo iniciante a política vem na versão do Harness. Edição local é exclusiva do modo avançado e auditada.

*Razão dos ajustes:* o v2.1 não tinha a conformance de adaptador (sem ela, “comprovadamente compatíveis” da ADR-026 não tem verificação), nem a proteção da interface local contra requisições de outros sites (risco real quando um serviço em loopback autoriza execução de código). Também faltava regra para o mínimo de agentes.

#### B2 — Dependências, documentação e acesso externo por finalidade

**B2.1 Princípio.** O sandbox do agente não tem egress IP. Todo acesso externo passa por rotas do mediador de egress do Core, definidas por finalidade em política versionada. O gateway de inferência e o mediador de recursos são **funções separadas do mesmo Core**. Não são serviços distintos, e o mediador não é um proxy genérico.

**B2.2 Finalidades iniciais.**

- inferência;
- pacotes (registries em allowlist, somente leitura);
- documentação (Context7, somente leitura);
- leitura de repositório público (somente leitura);
- publicação (push e PR no GitHub), exclusiva do perfil Release e executada pelo Core.

Outras finalidades só entram por nova versão de política.

**B2.3 Rotas.** Cada rota declara destino e métodos, direção (leitura ou escrita), domínio de credencial, limites por `run_id` (bytes, taxa, respostas 404, tempo), cache e nível de confiança da resposta, que é sempre “não confiável”. Cada acesso gera registro de auditoria (finalidade, destino, bytes, hash, `policy_sha256`, `run_id`), com redação de dados sensíveis (DEF-04). Inspeção de conteúdo mitiga e não prova ausência de exfiltração (ADR-021).

**B2.4 Pacotes.**
 (a) Manifesto e lockfile do workspace são não confiáveis.
 (b) Resolução e busca ocorrem no perfil `provision` do Runtime Manager: efêmero, sem workspace gravável, sem credenciais de publicação, com rede apenas pela rota “pacotes”. Isso evita que o cliente de pacotes (código de terceiros) rode dentro do processo do Core.
 (c) A saída é um lockfile com hashes de integridade e os artefatos baixados em cache endereçada por conteúdo.
 (d) São rejeitados: dependência por URL, git ou caminho local; registry fora da allowlist; ausência de hash.
 (e) Instalação e build ocorrem offline, no sandbox, a partir da cache somente leitura.
 (f) A lista de pacotes e hashes é registrada na evidência do run.
 (g) A decisão de aceitar um pacote é do Core, por política. O iniciante não é consultado sobre pacotes, e o modo avançado exibe a lista.
 (h) Em FAST só valem pacotes do perfil de stack ou já presentes no lockfile aprovado. Pacote novo eleva o ticket a STANDARD, no mínimo, e a elevação é registrada, sem pergunta ao usuário.

**B2.5 Scripts de instalação.**
 (a) Nunca rodam na fase com rede.
 (b) Na fase offline são negados por padrão. O motivo não é o sandbox, onde o agente já executa código arbitrário, e sim o artefato resultante, que pode ser exportado e rodar fora do Harness.
 (c) O perfil de stack traz pré-preparados os componentes nativos comuns do seu tipo de aplicativo e prioriza a biblioteca padrão, para o caminho comum não precisar de scripts.
 (d) Exceção é por pacote, versão e hash de integridade. Vem da política do perfil de stack (release do Harness) ou, no modo avançado, de decisão explícita do usuário, registrada.
 (e) No modo iniciante, pacote que exija script fora de (d) é rejeitado com explicação e alternativa (ADR-025).

**B2.6 Documentação e MCPs.**

- Servidores MCP de documentação e de repositório executam fora do sandbox, no domínio do Core.
- O agente os acessa por uma ponte local (loopback no namespace do sandbox, UDS até o Core).
- A ponte aplica a allowlist por ticket e por ferramenta (ADR-024).
- Respostas são dados não confiáveis e não alteram permissões.
- Playwright e demais binários vêm das imagens fixadas, sem download em tempo de execução.
- A compatibilidade de cada CLI com a ponte é verificada na conformance de adaptador (B1.3).

**B2.7 Contrato de perfil de stack.** Cada perfil define: imagem e camada de dependências por digest; comandos de build, teste e execução; portas e diretórios de dados declarados; interfaces de teste suportadas (HTTP, UI, CLI); descritor de smoke; política de dependências nativas. Quais perfis existem fica para a especificação detalhada (§20.6). B3 e B4 consomem este contrato.

*Razão dos ajustes:* o `provision` tira o resolvedor de terceiros do Core. A negação de scripts ganhou fundamento correto e uma rota de exceção, e o contrato de stack já existia implicitamente em B3 e B4 sem estar escrito.

#### Decisão sobre a emenda à ADR-018

**Decisão: aprovar a emenda aditiva, com ajuste de redação.**

A emenda não é exigida por incompatibilidade estrita. A ADR-018 proíbe acesso *arbitrário* e não proíbe rotas mediadas. É uma lacuna normativa: o texto só autoriza inferência, e DEF-04 e a A4 são escritas para payload de LLM. Interpretar silenciosamente que ela cobre pacotes e MCPs violaria o princípio de não alterar decisões em silêncio. Por isso recomendo registrar a emenda, que é aditiva e não relaxa nada.

> **ADR-018 — Emenda aditiva A (PROPOSTA, não ratificada).** Além do gateway de inferência, o Harness Core poderá mediar acessos externos de agentes e de perfis auxiliares exclusivamente por finalidades previstas em política versionada, cada uma com allowlist de destino, direção (leitura ou escrita), limites, autenticação, segregação de credenciais e auditoria. O processo do agente permanece sem acesso arbitrário à rede. Respostas de serviços externos são dados não confiáveis e não alteram permissões nem política. Credenciais de publicação permanecem inacessíveis ao agente implementador (ADR-007). Esta emenda não relaxa nenhuma invariante da ADR-018 original nem de DEF-04; os critérios de teste da A4 e da matriz negativa da POC-01 serão estendidos a cada finalidade.

Os ajustes em relação ao candidato do §20.3 do registro histórico são três: explicitar que as respostas são dados não confiáveis, preservar a ADR-007, e vincular a extensão dos testes.

#### B3 — Perfil `artifact`, preview e execução do software produzido

**B3.1 Perfil `artifact`.** É perfil do Runtime Manager (extensão do enum de perfis da A1) e distinto de `reviewer`, porque executa código não confiável do artefato. Não contém agente. Artefato e dependências ficam somente leitura. Os volumes de dados são separados, sem credenciais e sem egress. Valem limites de recurso e de tempo e os invariantes da A2.

**B3.2 Promoção do artefato.** Após o build no sandbox Developer, o Core promove o resultado, construído a partir de commit limpo, a um pacote imutável com digest, na store confiável. Testes, preview e smoke consomem esse digest.

**B3.3 Execução oficial.** Testes de aceitação e smoke são executados pelo executor confiável contra o artefato no perfil `artifact`, a partir do commit limpo. Os resultados oficiais vêm do executor (ADR-005/016).

**B3.4 Smoke test.** Definido pelo perfil de stack e pela spec:

- build seguindo apenas os passos documentados;
- início e verificação de prontidão;
- jornadas críticas marcadas na spec;
- persistência após reinício, quando aplicável;
- execução literal dos comandos da documentação.

A profundidade segue o modo: FAST executa uma ou duas jornadas críticas, e DEEP executa a aceitação completa mais verificações negativas (a matriz da especificação detalhada fixa os valores).

**B3.5 Serviços remotos em tempo de execução.** O MVP não concede egress ao artefato. Software que dependa de serviço remoto é exercitado contra dublês ou fixtures declarados na spec, e `DELIVERED` registra que a integração real não foi verificada. A descoberta informa o usuário quando a ideia depende disso.

**B3.6 Preview web.**
 (a) Servido pelo Core por proxy autenticado, em **hostname distinto** do da interface (subdomínio `.localhost` único por preview). Origem separada é o mecanismo de isolamento. Porta diferente no mesmo host não basta, porque cookies não são isolados por porta. A sessão da interface não depende de cookies compartilháveis. A verificação nos navegadores-alvo, inclusive no caminho WSL2, é critério de aceitação, não suposição.
 (b) O proxy aplica por padrão uma CSP restritiva: sem scripts, fontes, estilos ou conexões de terceiros, e conexões apenas à própria origem. Exceções só se declaradas na spec e informadas ao usuário. A CSP é mitigação aplicada pelo navegador, não prova de ausência de exfiltração.
 (c) Iframe com `sandbox` é complementar e não pode desabilitar o armazenamento do próprio app. Sem `allow-same-origin` a origem vira opaca e quebra `localStorage`, o que atingiria apps comuns. Se for incompatível, o preview abre em aba separada.
 (d) A ponte entre proxy e artefato segue a família da ADR-020 (sem porta TCP publicada e sem egress). A viabilidade é a provar.
 (e) Os dados do preview ficam em volume descartável, rotulado “dados de demonstração”.

**B3.7 CLI, API e biblioteca.** O preview se adapta ao tipo de software: saída capturada para CLI, execução guiada com requisições de exemplo para API. Não se pressupõe iframe.

**B3.8 Execução protegida (padrão).** O Harness mantém o app no perfil `artifact` (“Abrir meu app”), com volume de dados do usuário separado do código e do preview. Remover o app não apaga dados sem confirmação.

**B3.9 Exportação.** É uma entrega distinta e rotulada. Só está disponível para perfis de stack cuja forma documentada de execução fora do Harness seja exercitada no smoke test, dentro do ambiente declarado. A entrega informa o ambiente testado e que a proteção do Harness não se aplica. `DELIVERED` é registrado por forma de entrega (protegida, ou exportada em ambiente X), vinculado ao digest do artefato e do perfil de stack. Mudança relevante marca `STALE`.

**B3.10 Experiência.** Há uma ação única, “Ver o app”. O modo iniciante não mostra portas, contêineres nem URLs técnicas.

*Razão dos ajustes:* a CSP e a regra do iframe vêm da correção 1 da devolutiva Claude (ver §21.8). B3.5 cobre uma dificuldade que o §20.3 do registro histórico não mencionava: sem egress, apps que consomem API externa não podem ser testados “de verdade”. A cláusula declara isso em vez de prometer o que não dá.

#### B4 — Testes de aceite protegidos e mudanças legítimas

Texto literal B4.1–B4.10: ver §22.2 (“Correção F-01 — B4 integral incorporado ao registro”), única redação normativa de B4 neste documento.

### 20.4 Critérios de aceitação da revisão de desenho

A revisão B1–B4 estará fechada para especificação operacional quando:

1. Plataforma do MVP, matriz de compatibilidade e política de onboarding estiverem definidos, sem paridade presumida.
2. Rede mediada cobrir inferência, dependências e MCPs essenciais, preservando o isolamento e autorizando cada finalidade por política.
3. Perfil de execução do artefato e isolamento de origem do preview forem especificados, incluindo condição de `DELIVERED` no ambiente testado.
4. Autoria, baseline, execução, versionamento e correção legítima de AT estiverem definidos sem permitir adulteração pelo agente.
5. O contrato funcional do Product Charter continuar completo: descoberta guiada, OpenSpec/tickets, agentes intercambiáveis, skills/MCPs sob demanda, Memorix, SDLC adaptativo, loops, progresso e entrega.
6. Nenhuma decisão descrita como **VALIDATED/COMPLIANT** sem testes de conformidade reais. As POCs continuam `NOT_RUN` até serem executadas.
7. Nenhuma lacuna gerar novos serviços ou subagentes sem justificativa testável.

### 20.5 (removida)

A tabela de status da rodada v2.1 foi retirada por estar superada. O estado vigente consta no cabeçalho e na §21.1.

### 20.6 Itens que ficam para especificação detalhada, sem abrir novos debates abstratos

- Matriz FAST/STANDARD/DEEP → gates/testes/aprovações, com revisão após efeitos observados.
- Perfis de stack para primeiros tipos de aplicativos suportados e comandos de build/teste.
- Manifesto de checkpoint/retomada idempotente entre Pi/OpenCode/Codex.
- Proveniência e tratamento de dados não confiáveis recuperados do Memorix.
- Transparência sobre inferência local versus provedores externos.
- Métricas UX de primeiro uso e entrega, sem limiares arbitrários antes de protótipos.

**Direção (superada):** o ciclo de B1–B4 foi encerrado na §21; esta orientação é apenas histórica.


---

## 21. Consolidação v2.2 — revisão normativa de Claude, aceite técnico de ChatGPT

### 21.1 Estado e método de consenso

Em sua devolutiva da v2.1, Claude emitiu **ACEITO COM AJUSTES** para B1–B4 e propôs uma emenda aditiva à ADR-018. ChatGPT analisou as cláusulas e **concorda com a direção e as decisões operacionais**, com precisões redacionais na seção 21.4. A consolidação é um **acordo técnico Claude–ChatGPT** e será submetida ao usuário antes de formalizar o congelamento operacional do MVP. Não se confunde congelamento de desenho com conformidade medida.

| Elemento | Situação v2.2 |
|---|---|
| Product Charter / propósito original | Permanente, inalterado |
| ADR-001–028 | CONSENSO DE DESENHO, mantidas |
| B1–B4 | ACORDO TÉCNICO Claude–ChatGPT; aceite final do usuário pendente |
| ADR-018, emenda A | ACORDO TÉCNICO; formalização pelo usuário pendente |
| POC-01–04, conformance de adapters, preview/WSL2 | NOT_RUN |
| Implementação | NÃO INICIADA |
| Congelamento operacional do MVP | RECOMENDADO, ainda não formalizado pelo usuário |

### 21.2 Resolução normativa B1 — plataforma, agentes e primeira experiência

**Aprovar B1.1–B1.8 da devolutiva Claude** com a leitura a seguir:

- Linux x86_64 com runtime rootless é a **plataforma de referência dos ensaios**; Windows 11 com WSL2 dedicado é o **alvo de experiência do MVP, condicionado a testes independentes de conformidade**. macOS fica fora do MVP.
- Core, executor, gateway/mediador e estados confiáveis residem no domínio Linux controlado; agentes e navegador de testes em imagens fixadas por digest, sob restrições. **Não afirmar independência absoluta do host Windows**: o domínio WSL2 depende do host e da configuração de compartilhamento; preflight e política devem verificar exposição de mounts e interfaces.
- `doctor`: pré-requisitos, imagens, runtime, mapeamentos UID/GID, ponte de inferência, conformidade por adapter e credenciais separadas; resultados `READY`, `READY_WITH_LIMITS`, `NOT_READY`, sem liberar operações de Developer não conformes.
- Credenciais de LLM/fornecedores são geridas pelo Core/gateway; **o usuário informa o segredo por interface local e este não é injetado no processo do agente**. Persistência segura e proteção de credenciais são objeto de especificação e teste, não garantia presumida.
- Interface web local com validação de `Host`/`Origin`, token obrigatório para ações mutantes, prevenção de DNS rebinding, não exposição ao preview. Testes de CSRF/XS-Leaks e proteção do token serão detalhados sem criar outro serviço.
- Dois tipos de entrega: **MVP completo** somente com os três agentes conformes e POC-03 de cada um como origem e destino; **MVP limitado, explicitamente rotulado**, com ao menos dois agentes conformes e handoff entre eles. Abaixo disso, não apresentar como produto pronto para iniciantes.

**Critérios de aceitação de desenho:** plataforma, fluxo de instalação, matriz de adapters, proteção da UI e estados de preflight inequívocos. Sem afirmar que já funcionam.

### 21.3 Resolução normativa B2 — rede e dependências sem exposição arbitrária

**Aprovar B2.1–B2.7 da devolutiva Claude**, inclusive perfil auxiliar `provision` do Runtime Manager (não serviço novo):

- O agente permanece sem egress IP irrestrito. Core media cinco finalidades por política versionada: inferência, pacotes, documentação, leitura pública de repositórios e publicação restrita ao perfil Release.
- Resolver/baixar pacotes em ambiente `provision` efêmero e restrito; consumir cache validada por hash em instalação/build offline. Manifestos e conteúdo externo são não confiáveis; downloads de URL/git/path fora do contrato são recusados no caminho padrão.
- Scripts de instalação com rede nunca são permitidos; offline, só por exceção explícita de política por pacote/versão/hash. FAST usa apenas perfis e lockfiles previamente aprovados; pacote novo promove ao menos STANDARD.
- MCPs mediados com allowlist por ticket; Playwright/imagens sem download em runtime; respostas MCP não concedem permissões.
- A política deve definir opções de bloqueio quando pacote/stack legítimos não couberem no caminho suportado, comunicando limitações em linguagem natural sem prometer cobertura genérica de todo o ecossistema npm/pip.

**Emenda aditiva à ADR-018 (A):** texto único em §3 (“Decisão sobre a emenda à ADR-018”); pendente de aprovação formal do usuário.

### 21.4 Resolução normativa B3 — artefato, preview, entrega

**Aprovar B3.1–B3.10 da devolutiva Claude**, com estas ressalvas operacionais explícitas:

- Perfil `artifact` isolado para executar código produzido, **diferente** do `developer` e `reviewer`, sem credenciais nem egress do processo servidor. Artefato/stack por digest; smoke/AT a partir de checkout/artefato limpo; estado de `DELIVERED` vinculado ao ambiente efetivamente testado.
- Preview web usa hostname/origem separada da interface do Core, proteção da sessão e política de CSP. **O JavaScript que executa no navegador ainda utiliza a rede do usuário**; `network none` no contêiner não controla tráfego iniciado pelo navegador. A CSP reduz superfície, mas não comprova ausência de exfiltração; validação no navegador-alvo é obrigatória.
- Iframe com sandbox somente se compatível com as funcionalidades do app; quando incompatível, aba separada em origem isolada, sem compartilhar tokens ou storage da interface do Harness. O tratamento de `allow-same-origin`, `allow-scripts` e CSP deve ser definido e testado explicitamente antes de alegar isolamento de preview.
- Apps que exijam API remota real **não recebem falsa aprovação** por testes com dublês/fixtures; contrato de entrega declara integrações não verificadas. Exportação standalone exige smoke test da forma de execução declarada em ambiente suportado e rotulagem de que as proteções do Harness não acompanham o artefato exportado.
- Ações de UX conforme B3.8 e B3.10, sem exigir que o usuário configure rede, portas, Docker ou namespaces.

### 21.5 Resolução normativa B4 — integridade e evolução dos testes de aceite

**Aprovar B4.1–B4.10 da devolutiva Claude**, com limites de garantia preservados:

- AT de aceite aprovados como **cenários observáveis em linguagem comum**; IT do implementador contam apenas como evidência complementar. AT, runner e versões são protegidos fora do workspace do agente e verificados por SHA-256/manifesto.
- `AT` adaptados ao tipo do ticket: caixa-preta pela interface pública adequada, caracterização para refatorações, checks simples para tarefas documentais. Nem todo AT exige navegador nem todo ticket tem primeiro vermelho.
- AT gerados pelo LLM em contexto isolado são **propostas técnicas, não prova automática de cobertura/correção**. Os critérios aprovados pelo usuário são a autoridade do comportamento; o Core valida o vínculo cenário→AT e registra lacunas. Admitir spikes/protótipos explicitamente rotulados.
- Em FAST/STANDARD, testes podem ser legíveis ao agente em cópia read-only; DEEP pode ter fixtures reservadas. Os resultados oficiais vêm do executor confiável em commit limpo.
- Mudança legítima: escopo aprovado → nova versão AT + `STALE` nas evidências; defeito em teste → pergunta comportamental ao usuário; alteração não semântica somente com as condições restritas do Claude e **sempre registrada e notificada**, nunca silenciosa. Em DEEP, confirmação humana para todas as mudanças.
- `ACCEPTED` fica vinculado a `at_version` e digest do artefato; mudança relevante exige reverificação.

### 21.6 O que está resolvido versus o que ainda requer comprovação

| Tópico | Desenho | Validação experimental |
|---|---|---|
| Plataforma referência + WSL2 condicionado | ACORDADO TECNICAMENTE | NOT_RUN |
| Onboarding + doctor | ACORDADO TECNICAMENTE | NOT_RUN |
| Dois níveis de suporte a agentes | ACORDADO TECNICAMENTE | POC-03 NOT_RUN |
| Rotas de egress separadas por finalidade | ACORDADO TECNICAMENTE | POC-01/A4 NOT_RUN |
| Perfil `provision` | ACORDADO TECNICAMENTE | NOT_RUN |
| Perfil `artifact` + origem isolada de preview | ACORDADO TECNICAMENTE | NOT_RUN |
| AT vs IT, selo e atualização | ACORDADO TECNICAMENTE | POC-02 NOT_RUN |
| Evidências externas ao workspace | CONSENSO PRÉVIO | POC-04 NOT_RUN |
| Operação de ponta a ponta por iniciante | OBJETIVO NORMATIVO | UX-04 NOT_RUN |

### 21.7 Encaminhamento de governança

**Recomendação de ChatGPT:** aceitar as cláusulas B1–B4 e a Emenda A da ADR-018 como **baseline normativo v2.2**, sem adicionar ADRs, serviços ou frameworks. Considerar o desenho do MVP **apto a congelamento operacional mediante aprovação do usuário**; em caso de aprovação, produzir na etapa seguinte especificações detalhadas (matriz de risco/gates, perfis de stack, checkpoint, schemas e critérios de teste) **somente quando solicitado**, sem iniciar implementação por conta própria.

Não há necessidade de outra rodada com Claude apenas para repetir a ratificação das mesmas cláusulas. Uma nova consulta só é útil se o usuário desejar contestar um item ou se surgir incompatibilidade material ao detalhar o desenho.

### 21.8 Recibo da fonte Claude

A devolutiva Claude recebida em `Markdown colado(8).md` contém o texto normativo integral B1.1–B4.10 e a emenda aditiva. Esta seção consolida as decisões e preserva o sentido, sem substituir o texto original da devolutiva caso seja necessário revisar subcláusulas literais.


---

## 22. Revisão adversarial Astra — resolução documental e propostas operacionais

### 22.1 Parecer recebido

Astra classificou a v2.2 como **NECESSITA CORREÇÕES**, sem contradição arquitetural fundamental. F-01 impede que o documento seja normativamente autocontido. F-02 a F-06 são contratos operacionais e riscos experimentais que podem ser tratados na especificação detalhada, sem reabrir ADRs ratificadas. Nenhuma POC foi executada.

### 22.2 Correção F-01 — B4 integral incorporado ao registro

**Resolução:** abaixo está a redação integral B4, reproduzida da devolutiva Claude fornecida pelo usuário. Não é mais necessário obter um anexo para compreender as regras literais de testes protegidos. O SHA-256 da fonte original é `a799160bdb4154c17d6a694e7de90e297a91631832cc161e64e19f3932845b4b`. Esta reprodução prevalece sobre o resumo de §21.5 em caso de omissão, sem alterar a hierarquia de ratificação.

#### B4 — Testes de aceite protegidos

**B4.1 Classes.** AT (aceite) são protegidos na store confiável. IT (do agente) vivem no workspace, são evidência complementar e nunca bastam para `ACCEPTED`.

**B4.2 Contrato.** O usuário aprova cenários de comportamento observável, em linguagem comum, com exemplos concretos. Os AT derivam dos cenários aprovados, e o Core verifica o mapeamento cenário → AT. Critério sem AT impede o ticket de iniciar, exceto protótipo ou spike rotulado (ADR-028).

**B4.3 Tipos de AT por ticket.**

- *Comportamental, caixa-preta*, pela interface pública do artefato (HTTP, UI, CLI ou API da biblioteca). Usa a interface mais baixa que exercite o critério, sem exigir navegador quando não for necessário.
- *`check`*, para alterações triviais ou documentais (arquivo, build, conteúdo).
- *Caracterização*, para refatorações.

AT de UI localizam elementos por papel e nome acessível extraídos do cenário aprovado. Exceções à caixa-preta ficam documentadas no perfil de stack.

**B4.4 Autoria.** Os AT são gerados na fase de especificação por chamada de LLM do Core, em contexto distinto do implementador, sem agente novo. Nas atualizações, o gerador não recebe texto livre do agente, apenas campos estruturados.

**B4.5 Verificação antes do selo.**\
 (a) Cobertura cenário → AT.\
 (b) Linha de base por tipo. AT de comportamento novo deve falhar contra a linha de base do ticket (stub no primeiro ticket, artefato anterior nos seguintes). AT de caracterização deve passar nela. `check` é isento.\
 (c) Selo: SHA-256 por arquivo, manifesto, `at_version` imutável encadeada por hash e registro de aprovação, em domínio não gravável pelo agente (ADR-004/015).

Esses sinais são complementares e nenhum prova correção semântica.

**B4.6 Execução.** O executor confiável roda os AT a partir da store confiável, sobre commit limpo. Runner, configuração e versões são protegidos. Mudança do workspace em caminhos ou configuração protegidos é rejeitada ou sinalizada. O resultado oficial vem do executor (ADR-005/016).

**B4.7 Visibilidade.** Em FAST e STANDARD, os AT ficam visíveis ao agente em cópia somente leitura, para ele reproduzir falhas. Em DEEP há também casos reservados (mesmos cenários, dados distintos), não visíveis. Falha de caso reservado informa cenário e categoria, sem revelar valores.

**B4.8 Atualização legítima, sempre iniciada pelo Core.**\
 (1) *Mudança de escopo aprovada:* os AT afetados são regenerados, ganham nova `at_version`, a evidência afetada vira `STALE` e a reabertura exige confirmação (UX-02).\
 (2) *Suspeita de defeito num AT:* o agente só registra um relatório estruturado, que é dado não confiável. O Core o converte em pergunta de comportamento ao usuário (“uma despesa do dia 31 entra no mês atual ou no seguinte?”). A resposta vira esclarecimento de spec e segue o caso (1). Sem resposta, o ticket fica `BLOCKED` com próximo passo seguro, ou o modo avançado revisa.\
 (3) *Correção não semântica do conteúdo de um AT:* só é permitida ao Core se o texto do cenário for idêntico por hash, o AT ainda cumprir B4.5(b) e passar contra o último artefato `ACCEPTED`. Nunca é silenciosa: gera `at_version`, diff e motivo no registro e um aviso resumido ao usuário. Sem artefato `ACCEPTED` anterior, ou falhando qualquer condição, é tratada como semântica. Em DEEP, toda alteração exige confirmação humana.\
 (4) *Mudanças de runner, driver ou imagem do perfil de stack:* são versionadas como mudança de plataforma, não de AT, e marcam `STALE`.

**B4.9 Registro.** `ACCEPTED` registra `at_version` e digest do artefato. Mudança em qualquer um dos dois marca `STALE` até reverificação.

**B4.10 Limite declarado.** A garantia é de integridade e proveniência, não de correção semântica. O estudo UX-04 inclui a capacidade do iniciante de perceber comportamento errado.

*Razão dos ajustes:* o item (3) atende a exigência do §20.3 do registro histórico de “sem atualização automática silenciosa” sem forçar o iniciante a decidir sobre detalhes técnicos. O item (4) separa o que é falha de plataforma do que é falha de teste. B4.3 evita que “caixa-preta” vire “navegador para tudo”, o que seria lento e frágil.

---

## 4. Correções operacionais F-02–F-06 e decisão de REVIEW

### 22.3 Contratos complementares recomendados a partir de F-02–F-06

**Status:** aceitos por ChatGPT como correções mínimas a especificar, ainda não submetidos a nova ratificação de Claude nem comprovados experimentalmente.

**F-02 / REVIEW independente.** A revisão examina um snapshot identificável (`commit/digest`, versão da spec, AT/IT, resultados e política); avalia aderência aos critérios, regressões e achados de risco. Produz `review_report` com achados estruturados, severidade, vínculo ao ticket e resolução/aceitação humana conforme política. É um passo de revisão distinto de `VERIFY` e do parecer não vinculante do Judge. A profundidade é proporcional a FAST/STANDARD/DEEP e pode ser implementada pelo mesmo Core/executor, sem subagente fixo. Falha crítica não resolvida impede `ACCEPTED` quando a política assim determinar; o Judge nunca concede autorização.

**F-03 / checkpoint consistente.** A unidade de recuperação contém IDs e versões de ticket, spec, commit-base/HEAD, snapshot de arquivos não commitados quando aplicável, hashes das evidências, último passo confirmado, idempotency key das ações externas e pendências `UNKNOWN`. O Core persiste transições mediante journal/eventos transacionais em SQLite e reconcilia Git/evidências antes de retomar. Publicação/efeito externo de resultado desconhecido exige consulta ao destino e reconciliação, nunca repetição automática cega. Interrupções entre cada gravação são casos negativos da POC-03.

**F-04 / runner isolado.** O executor confiável **orquestra e observa**, mas nunca importa plugins, drivers ou código do projeto para dentro de seu próprio processo privilegiado. Qualquer código não confiável de aplicação/teste é executado em runtime isolado (perfil `artifact` ou outro perfil sob contrato), com entrypoint, runner e configurações oficiais fixados no domínio confiável. Substituição de runner/configuração por conteúdo do workspace deve ser detectada/rejeitada. Ampliar POC-02/04 para runner/config/plugin tampering.

**F-05 / aceite integrado.** `ACCEPTED` por ticket não implica `DELIVERED` do projeto. O candidato final é montado a partir de um commit e artefato identificados por digest; todos os AT aplicáveis ao conjunto, regressões requeridas e smoke real da forma de execução declarada são executados sobre **o mesmo candidato**. Evidências históricas só podem ser reutilizadas quando a identidade/versionamento e dependências não as tornem `STALE`. A documentação de entrega aponta para o candidato efetivamente testado.

**F-06 / exceção compreensível.** Para stacks suportadas, bloqueios possuem ações de correção via Core que não exigem escolher pacotes, editar `uid_map` ou desativar segurança. Na falta de solução autorizada, mostrar limitação, alternativa funcional e opção de ajustar escopo. O modo avançado pode oferecer exceções versionadas e auditadas onde a política permitir; iniciante nunca aprova uma permissão técnica que não pode julgar. Testar com UX-04.

### 23.2 Emenda textual aprovada tecnicamente — F-02 / REVIEW independente

A cláusula F-02 da seção 22.3 permanece aplicável e recebe **o seguinte complemento normativo obrigatório**:

> A revisão não pode consistir apenas na autoavaliação do implementador: deve ocorrer em contexto separado da conversa de implementação, a partir do snapshot, da especificação e das evidências identificadas. Pode reutilizar o mesmo modelo e a infraestrutura existente. Achados produzidos por LLM são propostas de revisão; seu tratamento segue política explícita e confirmação humana quando exigida, sem converter o LLM Judge em aprovador ou veto automático.

O contexto de REVIEW deve ser reconstruído a partir de dados autoritativos identificados e do commit/digest candidato, e não depender do relato do agente implementador. Nenhum novo agente permanente, serviço ou modelo diferente é exigido.

### 23.3 Responsabilidades e autoridade

| Função | Obrigação | Limite |
|---|---|---|
| VERIFY | Executar verificações e coletar resultados oficiais com identidade do candidato | Atesta apenas o comportamento efetivamente exercitado |
| REVIEW | Avaliar aderência à spec, regressões, riscos e suficiência da evidência em **contexto separado** | Emite achados; tratamento conforme política e aprovações exigidas |
| LLM Judge | Emitir parecer consultivo (`shadow`/`advisory`) | No MVP não modifica `ALLOW`/`DENY`, não aprova entrega, não veta automaticamente |
| Harness Core / Policy Engine | Aplicar regras determinísticas de transição | Não delega autorização ao parecer de LLM |
| Usuário / revisor autorizado | Decidir comportamentos e aprovações expressamente previstos | Não pode contornar invariantes de segurança pelo fluxo de iniciante |

### 23.4 Fechamento da revisão F-01–F-06

| Item | Estado do desenho |
|---|---|
| F-01 | Corrigido: cláusulas B4.1–B4.10 autocontidas no §22.2 |
| F-02 | Corrigido textualmente neste §23.2: contexto independente de revisão |
| F-03 | Contrato de checkpoint/reconciliação aceito; testes continuam pendentes |
| F-04 | Contrato do runner isolado aceito; testes continuam pendentes |
| F-05 | Contrato do aceite integrado aceito; testes continuam pendentes |
| F-06 | Contrato de reparação acionável aceito; estudo UX continua pendente |

## 5. Delta v2.5

# ANEXO v2.5 — DELTA DE DESENHO (sobre o baseline v2.4)

**Status:** candidata final de desenho; as correções de artefatos descritas abaixo são requisitos pendentes de comprovação. Nenhum protótipo ou pacote corrigido acompanha este documento. POCs fundacionais e validação integrada ainda pendentes. **Precedência:** este anexo especifica apenas os componentes adicionados; todo o restante do baseline v2.4 permanece inalterado. Nenhuma ADR é reescrita ou ratificada por este anexo.

## DELTA-01 — `docs/` no projeto
- Armazena material de referência PDF/Markdown/TXT, inclusive de projetos existentes, sem se tornar fonte normativa de requisitos.
- Leitura somente sob demanda, limitada à raiz permitida e orçamento de conteúdo; arquivos ilegíveis ou não suportados geram erro explícito.
- Recuperações devem citar caminho relativo, hash SHA-256 e página/seção quando disponíveis. A leitura de MD/TXT e a extração de PDF são capacidades previstas, ainda não demonstradas nesta revisão.
- Arquivos e textos recuperados são dados não confiáveis; não executam scripts, comandos nem instruções sobrepostas às políticas. Não abrir URLs automaticamente.
- Mudanças de requisitos derivadas de documentos passam por aprovação no OpenSpec.
- Nenhum banco vetorial, LLM de RAG ou Obsidian é obrigatório.

## DELTA-02 — `security-best-practices`
- Incorporação prevista da skill com carregamento seletivo de referência por stack e sob allowlist do Core; não amplia privilégios.
- Antes da incorporação, corrigir os exemplos de redirecionamento de URL e comparação insegura de prefixos de caminho identificados pelo Astra; eliminar a referência ao executor externo `tools/dev.py loop` e adequar comandos à plataforma declarada. Pacote corrigido preparado (hash em §6) e exemplos testados isoladamente; o carregamento autorizado pelo Core e a integração real seguem `NOT_RUN`.
- Orientações da skill não são evidências de aprovação; scanners, testes e revisão seguem política e executor confiáveis. Achados médios devem poder ser persistidos, mesmo quando a resposta breve omiti-los.

## DELTA-03 — `grill-me-with-docs` (arquivo identificado e examinado)
- A skill adotada no desenho é **`grill-me-with-docs`**, no conteúdo fornecido e confirmado pelo usuário, identificado pelo hash abaixo independentemente de seu local de armazenamento. O arquivo examinado prevê produção/atualização de proposal/spec/design/tasks do OpenSpec e não chama `grilling` nem `domain-modeling`.
- Proveniência do arquivo examinado: SHA-256 `b2c0947fee31906941f76550264f2fb6d23b3f810a1211754fb14291665cfdc4`. A leitura confirma o conteúdo, não a execução ou compatibilidade experimental.
- Contrato exigido para a integração: DISCOVER só interroga ambiguidades materiais; a skill não edita código de produção e não cria `task.md` ou `blueprint.md` concorrentes. Especificações e decisões propostas integram o OpenSpec, respeitando as aprovações previstas.
- A validação usa `openspec validate <change> --strict` **se suportada pela versão efetivamente instalada**. Confirmar a sintaxe via `openspec --help`; na ausência, bloquear handoff e explicitar incompatibilidade, sem alegar sucesso.
- A skill é subordinada ao roteamento de capacidades e às políticas do Core.
- Ajustes de integração necessários: Context7, histórico remoto e Playwright só podem ser utilizados quando autorizados; sua indisponibilidade deve gerar limitação explícita quando impedir a validação de um requisito. A preferência por 3–5 perguntas não é uma quota obrigatória. “Plan mode” significa permanecer na etapa de especificação do UDH, sem depender de um modo particular de um agente. Retentativas de validação seguem os limites do Core; sucesso do comando não substitui aprovação dos requisitos nem autoriza implementação.

## DELTA-04 — OpenSpec (já existente)
- Permanece autoridade para intenção aprovada, especificação, decisões e tarefas declarativas. Nenhum segundo plano concorrente.

## DELTA-05 — tickets integrados ao PLAN/Core, sem skill dedicada
- Tarefas OpenSpec pequenas são materializadas diretamente; decomposição pelo LLM apenas para trabalhos que exijam subdivisão.
- Heurísticas: fatias verticais verificáveis, dimensionadas para contexto novo; *expand–contract* para refatorações transversais quando adequado.
- Entrada: ID/versão/hash de mudança OpenSpec, tarefas e critérios aprovados, dependências/contexto permitidos.
- Saída proposta: IDs locais, objetivo, links às tarefas/critérios de origem, dependências, verificações propostas, lacunas.
- Validação do Core: identidade/versão da spec, unicidade, referências válidas, ciclos, cobertura mínima, idempotência. Aprovação semântica ainda exige os gates previstos pela v2.4. SQLite é autoridade de estado; nunca inferir ACCEPTED de uma proposta do LLM.
- Ao mudar a versão OpenSpec, propostas anteriores ficam obsoletas e exigem reconciliação explícita.

## Verificações adicionais propostas (sem alegação de validação)
- DOC-01/02: proveniência e fronteiras de confiança de MD/TXT/PDF (PDF pendente).
- SKILL-01/02: carregamento autorizado e verificação dos exemplos de URL e path após correção.
- DISC-01: descoberta proporcional e handoff OpenSpec real (integração ponta-a-ponta pendente).
- TICKET-01/02: materialização idempotente, validação de dependências e handoff com troca de agentes (handoff real pendente).
- POC-01..04 da v2.4 permanecem NOT_RUN.

**Limite desta entrega:** este documento consolida o desenho e os requisitos de correção; não inclui nem comprova protótipo, pacote de skills corrigido ou resultados de testes. Qualquer artefato auxiliar futuro deve ser identificado por versão e escopo, sem ser apresentado como Harness operacional ou como prova de sandbox, executor confiável, adapters ou integração real com OpenSpec/Memorix. A implementação continua não iniciada no estado registrado e depende de autorização própria.



## 6. Proveniência dos artefatos e limites de validade

O hash identifica **bytes**, não indica auditoria, correção ou execução. O baseline não incorpora automaticamente qualquer pacote como seguro. Os valores abaixo foram calculados sobre arquivos disponíveis nesta entrega, não inferidos de nomes de arquivos.

| Artefato | SHA-256 | Situação |
|---|---|---|
| `grill-me-with-docs/SKILL.md` (enviado como `SKILL(20261020-022938).md` e como `SKILL.md`; mesmos bytes) | `b2c0947fee31906941f76550264f2fb6d23b3f810a1211754fb14291665cfdc4` | Artefato fornecido; conferido |
| `security-best-practices.zip` | `3c7064d8e35edae69a812c6c5c4b8dad8bb580d8cf0d7cc5d58b2de5519d6139` | Artefato fornecido; conferido |
| `security-best-practices/SKILL.md` (dentro do zip) | `a84537210606270c21b03618e422724de09154ae55d32576851754ccb6e05267` | Conferido; não atestado como corrigido |
| `security-best-practices-corrigido.zip` | `527447ff45ba8e7c1abf5fafb33f7110e8310272da0b1869a76d11f9e5005ff2` | Pacote corrigido (4 arquivos alterados); exemplos testados isoladamente; carregamento pelo Core `NOT_RUN` |
| `security-best-practices/SKILL.md` (dentro do pacote corrigido) | `ec77857d5531366580d5b4249c0fe9be68aead02efd00a1572266123fecd24e7` | Corrigido (referência a `tools/dev.py loop` substituída) |
| `SKILL.md` de origem não identificada | `2f7b777d5ce9c99d1f86b857ef2478a380b70b1a196908e2ff5383c5592a6fd1` | Não corresponde ao `SKILL.md` do zip nem ao de `grill-me-with-docs`; reconciliar ou descartar |

O documento registra como referência de `grill-me-with-docs` o SHA-256 informado no DELTA-03; qualquer divergência entre esse valor e os bytes instalados deve ser reconciliada antes da incorporação. O ZIP original de segurança não prova que as correções recomendadas foram aplicadas.

**Conferência independente (Claude, 20/10/2026):** os hashes das três primeiras linhas foram recalculados sobre os arquivos fornecidos e conferem; o de `grill-me-with-docs` coincide com o registrado no DELTA-03. O pacote de segurança fornecido ainda contém os três defeitos do DELTA-02 (referência a `tools/dev.py loop`, validação de redirecionamento apenas por `startsWith("/")` e comparação de caminho por prefixo de string); portanto não estava corrigido. Foi preparado o pacote `security-best-practices-corrigido.zip`, que altera apenas esses pontos (`SKILL.md`, `javascript-general-web-frontend-security.md`, `python-flask-web-server-security.md` e `python-data-science-security.md`): o redirecionamento passa a comparar a origem via `URL` e os caminhos passam a usar `Path.is_relative_to`. Os exemplos corrigidos foram testados isoladamente (inclusive `//evil.com` e diretório irmão com mesmo prefixo); o carregamento autorizado pelo Core (SKILL-01/02) permanece `NOT_RUN`.

### Checklist de governança

- [ ] Aprovação formal do usuário para congelar este baseline e ratificar a emenda ADR-018 A.
- [ ] Confirmar o arquivo exato das skills e a validade de suas dependências e referências.
- [x] Corrigir os exemplos de segurança destacados no DELTA-02 e testá-los isoladamente (pacote corrigido em §6).
- [ ] Verificar o carregamento autorizado da skill pelo Core (SKILL-01/02).
- [ ] Executar POC-01–04 e registrar evidências reproduzíveis.
- [ ] Detalhar e executar o preflight, autenticação, gateway, runner e integração real com agentes.

**Resultado B-01:** cláusulas B1–B4 passaram a estar reproduzidas literalmente neste documento, evitando dependência normativa de `Markdown colado(8).md`. Isso **não** converte o acordo técnico em aprovação do usuário.
