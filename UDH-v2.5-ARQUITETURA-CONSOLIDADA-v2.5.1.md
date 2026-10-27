# Universal Dev Harness 2026 — Nota editorial derivada v2.5.1 (não normativa)

**Tipo:** material editorial de consulta, derivado da arquitetura v2.5; não é revisão normativa nem substitui o baseline.  
**Fonte normativa:** [baseline rastreado](Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md), incluindo seção0, matrizes e proveniência.  
**Estado:** preservado como derivado em 27/10/2026, por escolha delegada pelo usuário; sem promoção a nova autoridade. Emenda ADR-018 A continua pendente de aprovação formal; este texto não comprova implementação nem POCs.  
**Regra de uso:** usar para navegação e consulta; em divergência prevalecem as regras normativas e os changes aprovados no seu escopo. Propostas e divergências exigem avaliação explícita, não substituição silenciosa.

## 1. Produto e objetivo

O **Universal Dev Harness (UDH)** é um sistema **local-first** que permite a iniciantes e usuários avançados transformar uma ideia em software funcional, testado e utilizável. O iniciante deve conseguir definir o comportamento desejado, acompanhar decisões e bloqueios, experimentar a aplicação e receber uma entrega compreensível sem conhecer Git, agentes ou ferramentas internas.

**Fluxo:** DISCOVER → SPEC → PLAN → EXECUTE → VERIFY → REVIEW → DELIVER, com checkpoints e memória ao longo do ciclo.

**Requisitos de produto:** Pi Agent, OpenCode e Codex intercambiáveis; OpenSpec; tickets verificáveis; segurança por padrão; skills e MCPs autorizados sob demanda; Memorix como memória auxiliar; correções com limites; evidências independentes; entrega executável/documentada; retomada após interrupções; baixo overhead. A interface guiada deve existir no produto, mas não é pré-requisito da prova técnica inicial.

**Critério de sucesso:** um iniciante cria um pequeno aplicativo de ponta a ponta, verifica seu comportamento, compreende bloqueios, retoma um trabalho e recebe um artefato utilizável com evidências. Um protótipo ou uma POC não é uma entrega final do produto.

## 2. Arquitetura lógica

```text
Usuário → interface guiada local / experiência de descoberta
                        │
             UDH Core (orquestrador único)
       ┌────────────────┼────────────────────────────┐
       │                │                            │
  OpenSpec          PLAN / tickets             Context Builder
  specs e critérios  estados em SQLite         Git / Memorix / docs/
       │                │                            │
       └────────────────┼────────────────────────────┘
                        │
           Policy Engine / Capability Router
                        │
      Agent adapter (Pi / OpenCode / Codex)
                        │
       Runtime Manager / sandbox isolado
              │                      │
    Medição de egress         Implementação / build
              │                      │
        Inferência/MCP          Trusted Executor
                                     │
                              AT/IT + Evidências
                                     │
                    REVIEW independente / Aceite
                                     │
                      Preview / DELIVER / Release
```

### Fontes de autoridade

| Informação | Autoridade |
|---|---|
| Especificações e requisitos aprovados | OpenSpec |
| Ticket, estado, checkpoints, autorizações | UDH Core / SQLite |
| Código e commits | Git |
| Resultados oficiais e integridade | Executor confiável / store protegida |
| Conhecimento histórico contextual | Memorix (não autoritativo) |
| Referências externas e manuais | `docs/` (não autoritativo) |

O Core governa a execução e a política; o modelo pode propor especificações, classificação e tickets, mas não autorizar acesso, alterar estados oficiais ou declarar testes aprovados. Não criar um segundo orquestrador, gerenciador de tickets, sistema de especificação ou pipeline RAG obrigatório.

## 3. Decisões arquiteturais finais — ADR-001 a ADR-028

Estas redações são uma apresentação editorial das decisões de desenho; a ratificação, a proveniência e a precedência devem ser consultadas no baseline normativo. Esta nota não redefine aprovação nem validação de testes.

| ID | Decisão normativa |
|---|---|
| ADR-001 | **Runtime Manager externo** é a fronteira primária de isolamento; hooks, skills e permissões dos agentes não são sandbox de SO. |
| ADR-002 | Contrato declarativo de `capabilities` com **fail-closed**; configuração incompatível ou sem verificação suficiente não executa tarefa com escrita. |
| ADR-003 | **Git worktree isola estado Git, não processos**; escrita exige isolamento efetivo do SO. |
| ADR-004 | **Políticas, baselines e testes protegidos** residem fora do workspace modificável; alterações não autorizadas são rejeitadas. Não se promete detectar toda mudança semântica. |
| ADR-005 | **Exit codes, logs e resultados oficiais** são coletados por executor confiável fora do controle do agente e vinculados ao run. |
| ADR-006 | **LLM Judge é auxiliar e não habilitador**: jamais aprova execução/publicação sozinho nem substitui gates determinísticos. Eventual veto consultivo e tratamento de incertezas seguem política explícita. |
| ADR-007 | Perfil **Release** separado de Developer, com credenciais de publicação não acessíveis pelo agente implementador. |
| ADR-008 | Congelar expansão arquitetural em **Security Foundation v1.2** até passar nas quatro POCs, repetidamente e com evidências. |
| ADR-009 | OpenSpec, Memorix, MCPs, skills, Judge e UI são camadas de produto que não substituem nem antecedem a comprovação da fundação de segurança. |
| ADR-010 | Definição de pronto: POCs verdes, configurações não conformes recusadas, handoff funcional, evidências íntegras e bloqueios inteligíveis ao iniciante. |
| ADR-011 | **Estado do trabalho é autoritativo no Harness Core/SQLite; código no Git; especificação no OpenSpec; evidências no executor; memória semântica no Memorix.** Trocar agente não significa transferir confiança à conversa. |
| ADR-012 | **MCPs e skills sob demanda e com mínimo privilégio**; Context7, GitHub e Playwright não precisam ficar todos ativos globalmente. |
| ADR-013 | **Erro de segurança deve vir de código estruturado emitido pelo gate**, e a UI o explica em linguagem acessível; explicações opcionais de LLM não alteram a decisão. |
| ADR-014 | Loop de correção com limites de tentativas/tempo/recursos, integridade de políticas e testes protegidos; falha persistente é bloqueada e registrada, não mascarada. |
| ADR-015 | **Baselines no MVP:** SHA-256 de snapshots/testes protegidos, com valor de referência em domínio não gravável pelo agente; HMAC-SHA256 é hardening opcional sob modelo de ameaça e gestão de chaves definidos. |
| ADR-016 | **Evidências oficiais:** armazenamento protegido contra escrita pelo agente, hash SHA-256 vinculado a `run_id` e conferência no gate; atestação assinada é evolução opcional. |
| ADR-017 | **Runtime MVP:** contêiner rootless Linux é candidato preferencial, jamais suficiente por si; requer inspeção da configuração e testes negativos de socket, volumes, symlink, rede, privilégios e UID/GID. |
| ADR-018 | **Gateway de inferência:** nenhum acesso arbitrário à rede pelo agente; chamadas externas de LLM mediadas por gateway fora do sandbox, com credenciais exclusivas, autenticação/escopo, allowlist e auditoria. Preservar `network none` no processo de agente onde a implementação suportar IPC local controlado. |
| ADR-019 | **Juiz diante de `ABSTAIN`:** somente aconselha; `VETO` produz retenção temporária revisável; `PASS` nunca habilita execução ou entrega sozinho. Falsos positivos são medidos e decisões críticas seguem humano ou mecanismo confiável. |
| ADR-020 | **Transporte de inferência:** UDS é IPC preferencial, se viável e conforme; exige verificação de permissões, peer, UID/GID rootless, montagem e ciclo de vida. TCP em namespace isolado não é proibido, mas **deve comprovar que nenhum processo fora do sandbox consegue alcançar a porta e que a autenticação não depende apenas de estar no loopback**. Ponte HTTP local só com controle de egress e mesma avaliação. |
| ADR-021 | **Modelo de ameaça do MVP:** adversário controla agente e filhos, pode escrever no workspace e falsificar saídas; não pressupõe comprometimento inicial do host, do Runtime Manager ou do Harness Core. Testar socket do runtime, symlink, mounts, capabilities efetivas, namespaces e canais de inferência. Contêiner compartilha kernel; inspeção de payload é mitigação parcial, **não demonstra ausência de exfiltração**. |
| ADR-022 | **Judge no MVP / precedência sobre ADR-019:** `DENY` determinístico não pode ser revertido pelo Judge. No MVP, Judge opera **somente em `shadow/advisory`**, sem veto nem aprovação automática. **ADR-019 permanece como desenho-alvo pós-MVP**: retenção temporária revisável apenas sobre `ABSTAIN`, condicionada a métricas calibradas de falso positivo/abstenção, política de revisão humana aprovada e evidências de resistência a prompt injection no domínio. |
| ADR-023 | **SDLC adaptativo:** o Core seleciona modo **FAST / STANDARD / DEEP** por risco, impacto, dados sensíveis, autenticação, infraestrutura, contrato público, reversibilidade e exposição — **não pelo tamanho do diff**. Incerteza material eleva o rigor ou exige esclarecimento, nunca reduz silenciosamente. Mesmo FAST preserva isolamento, integridade, rastreabilidade e verificação de critérios de aceite proporcionais. O iniciante não tem obrigação de selecionar o modo. |
| ADR-024 | **Roteamento de capacidades:** um mapeamento determinístico e versionado de **tipo/risco do ticket → conjunto máximo autorizado de skills e MCPs** controla o acesso. O agente pode solicitar apenas capacidades da allowlist; solicitações fora dela são negadas ou passam por processo externo de autorização, nunca autoelevação por prompt. Carregar somente o necessário, com limites de contexto, origem confiável e auditabilidade. O modelo pode sugerir a categorização de um ticket, mas essa sugestão não confere permissões: o Core aplica regras verificáveis e, ante ambiguidade relevante, falha fechado/escalona. |
| ADR-025 | **Bloqueios explicáveis e acionáveis:** o executor/Policy Engine produz código estruturado de **violação de política**, distinto de **falha funcional/teste**. A UX apresenta **o que ocorreu, por que importa, o que foi realmente verificado e a próxima ação segura**. Ações propostas devem corresponder à causa e às permissões, nunca oferecer desativar proteção crítica. Exceções administrativas são externas, explícitas e auditáveis. Um LLM pode reescrever a explicação, mas nunca definir o veredito nem o bypass. |
| ADR-026 | **Seleção de agente:** no modo iniciante, o Core seleciona automaticamente entre agentes **disponíveis e comprovadamente compatíveis** com as capacidades, políticas e modelo de execução do ticket. O fallback segue ordem **configurável, estável, registrada e justificável**, sem relaxar requisitos de segurança. Nenhuma ordem fixa como OpenCode → Pi → Codex é universal. Modo avançado permite escolha consciente, sujeita ao mesmo contrato de segurança. Handoff usa estado autoritativo e necessita prova na POC-03, não se presume resolvido apenas por integrações externas. |
| ADR-027 | **Contexto mínimo, efêmero e recuperável por ticket:** cada execução recebe intenção/critérios de aceite, ticket/spec, estado versionado, código relevante, dependências explícitas, ferramentas autorizadas e resultados recentes de verificação. Decisões duráveis e convenções relevantes são recuperadas seletivamente do Memorix. Ao terminar o ticket, persistir entregáveis, evidências, estado e síntese durável; descartar o contexto conversacional efêmero. Não descartar dependências necessárias nem tratar Memorix/conversa como estado autoritativo (ADR-011). Implementar como função do mesmo orquestrador, sem serviço adicional obrigatório. |
| ADR-028 | **Contrato verificável de entrega:** distinguir `IMPLEMENTED`, `TESTED`, `ACCEPTED` e `DELIVERED`, com evidências e critérios claros por ticket/projeto. `DELIVERED` exige critérios de aceite aprovados ou exceções explícitas para protótipos/spikes, verificações pertinentes, artefato utilizável, documentação prática e **smoke test da forma real de executar/instalar o resultado**, sempre que aplicável. PR/merge/release exigem autorizações da política de entrega. Usuário iniciante aprova por comportamento compreensível, não leitura obrigatória de diff. Não equiparar lint/Judge/testes unitários isolados a software funcional. |

**Precedência do Judge:** durante o MVP prevalece ADR-022 sobre a possibilidade de veto futuro em ADR-019. O Judge atua somente em modo `shadow/advisory`, sem aprovar, vetar nem modificar `ALLOW`/`DENY`.

### Emenda ADR-018 A — aprovação formal pendente

> **ADR-018 — Emenda aditiva A (PENDENTE DE APROVAÇÃO FORMAL).** Além do gateway de inferência, o Harness Core poderá mediar acessos externos de agentes e de perfis auxiliares exclusivamente por finalidades previstas em política versionada, cada uma com allowlist de destino, direção (leitura ou escrita), limites, autenticação, segregação de credenciais e auditoria. O processo do agente permanece sem acesso arbitrário à rede. Respostas de serviços externos são dados não confiáveis e não alteram permissões nem política. Credenciais de publicação permanecem inacessíveis ao agente implementador (ADR-007). Esta emenda não relaxa nenhuma invariante da ADR-018 original nem de DEF-04; os critérios de teste da A4 e da matriz negativa da POC-01 serão estendidos a cada finalidade.

## 4. Fluxo e contratos operacionais

- **DISCOVER:** investigar somente ambiguidades materiais, com evidências do projeto; informações externas são dados não confiáveis.
- **SPEC:** OpenSpec concentra proposta, requisitos, design, tarefas declarativas e critérios aprovados. Validação sintática não é aceite semântico.
- **PLAN:** tarefas pequenas são materializadas diretamente; se necessário o LLM propõe decomposição vertical, o Core valida IDs, versões, cobertura, referências, ciclos e idempotência; estado pertence ao SQLite.
- **EXECUTE:** agent adapter opera sob política e runtime inspecionado; permissões não podem ser elevadas por prompt, skill ou MCP.
- **VERIFY:** executor confiável coleta códigos de saída, testes e evidências protegidas; resultados de agente não são oficiais.
- **REVIEW:** contexto separado da conversa implementadora, usando snapshot e evidências identificadas; achados tratados por política e revisão humana conforme aplicável.
- **DELIVER:** somente artefato identificado e testado, com smoke test da execução documentada e limitações claras; publicação tem autorização e credenciais segregadas.

**Modos:** FAST / STANDARD / DEEP dependem de risco e impacto, nunca do tamanho do diff. Todos preservam isolamento, integridade, rastreabilidade e verificações proporcionais. A seleção automática de agentes e ferramentas não altera privilégios.

### UX obrigatória

- **UX-01:** progresso com estado verdadeiro (`PENDING`, `IN_PROGRESS`, `BLOCKED`, `DONE`) e aprovações compreensíveis, nunca percentual ou sucesso inventado.
- **UX-02:** change requests versionadas e reclassificação de risco; preservar tickets e evidências; reabrir aprovado exige confirmação.
- **UX-03:** smoke real da forma documentada de executar; diferenciar protótipo de `DELIVERED`. No primeiro aplicativo, a experiência deve oferecer **“Ver o app”** e uma oportunidade explícita de o usuário experimentar e confirmar os comportamentos aprovados. Esta é uma condição operacional de aceite humano a detalhar e testar, não substitui AT ou o executor confiável.
- **UX-04:** piloto com iniciantes para testar descoberta, bloqueios, interrupção, retorno e primeira execução.

### Segurança e evidência — regras transversais

1. Agentes e processos filhos são considerados não confiáveis; a fronteira de segurança é o isolamento efetivo do SO, não regras escritas em `AGENTS.md`.
2. Política declarada e configuração efetiva do runtime devem coincidir; na dúvida bloquear escrita (`fail-closed`).
3. Testes protegidos, política e evidências residem fora do workspace gravável; hashes identificam bytes e não demonstram correção semântica.
4. Processos do agente não recebem acesso arbitrário à rede nem chaves reais dos provedores; egress tem finalidade e destino autorizados.
5. Código do projeto, plugins e parsers de arquivos não devem executar no processo privilegiado do Core ou do coletor confiável.
6. Permissões, resultados oficiais, verificações e status não derivam de pareceres de LLM.

## 5. Plataforma e ambiente (B1)

**B1.1 Plataformas.**
 (a) Referência de conformidade e desenvolvimento: Linux x86_64 com contêiner rootless (ADR-017). POC-01–04 são executadas primeiro nesse ambiente.
 (b) Alvo de produto para usuários Windows: Windows 11 com uma distro WSL2 dedicada, gerenciada pelo Harness, executando o mesmo domínio Linux. Só é oferecida depois que a mesma matriz de conformidade for executada dentro dela e resultar `COMPLIANT`. Se falhar, o produto permanece Linux-first e o isolamento não é reduzido para dar paridade.
 (c) macOS: roadmap, sem compromisso no MVP.
 (d) Cada plataforma tem veredito próprio. Nenhuma paridade é presumida.
 (e) **Instalação adaptativa:** em Linux nativo, como Debian ou Ubuntu, o UDH usa diretamente o domínio Linux existente, **sem instalar WSL2**. Em Windows 11, utiliza distro WSL2 dedicada somente se o ambiente for necessário e não estiver conforme; uma distro existente só pode ser reutilizada se passar nas verificações de isolamento e separação de dados. Fedora e outras distribuições são candidatas a suporte, não plataformas automaticamente certificadas; cada perfil de distribuição exige testes de conformidade.
 (f) **Experiência sem conhecimentos de Linux:** tanto no Windows quanto em Linux com interface gráfica, a instalação, a configuração, as atualizações e o uso normal devem ser guiados, sem exigir comandos Bash, PowerShell, Docker ou edição manual de arquivos de sistema. No Linux headless, admite-se instalação/gestão por meio administrativo documentado, mantendo os mesmos controles de segurança. A instalação não dispensa elevação de privilégios quando requerida pelo SO: solicita consentimento compreensível e explica reinícios ou bloqueios. A infraestrutura interna permanece invisível no modo iniciante; não se promete automatizar restrições impostas pelo host.

**B1.2 Domínio Linux controlado.** Core, Runtime Manager, executor, mediador de egress (que contém o gateway de inferência), coletor de evidências e interface local rodam no domínio Linux. Agentes e navegador de testes rodam em imagens identificadas por digest. O sistema hospedeiro não recebe credenciais, agentes nem código gerado em execução.

**B1.3 Preflight (`doctor`).** É função do Core. Identifica antes de tudo se está em Linux nativo ou Windows/WSL2, aplica apenas as verificações pertinentes e não tenta instalar WSL2 em Linux. Verifica:

- kernel, cgroup, user namespaces, subuid/subgid e recursos mínimos; em WSL2 verificar especificamente mounts automáticos como `/mnt/c`, interoperabilidade com executáveis Windows e delegação efetiva de cgroups;
- runtime rootless e mapeamentos UID/GID (DEF-02);
- imagens por digest;
- mediador de egress, com chamada de teste, e credenciais;
- cada agente, por **conformance de adaptador**: executar uma tarefa trivial dentro do sandbox, com o endpoint do mediador e sem chave real, consumindo uma ferramenta MCP mediada quando aplicável;
- conformance da plataforma, em cache por `policy_sha256` mais digest.

O resultado é `READY`, `READY_WITH_LIMITS` (lista agentes ou capacidades indisponíveis) ou `NOT_READY`. Cada código de motivo mapeia para uma ação em linguagem comum (ADR-025). A execução Developer exige `READY`, ou `READY_WITH_LIMITS` com ao menos um agente compatível.

**B1.4 Correção do ambiente.** O fluxo guiado disponibiliza diagnósticos, ações autorizadas e opções de recuperação sem exigir terminal no modo iniciante. Ações em espaço de usuário (baixar imagem, criar diretórios) podem ser automáticas. Ações privilegiadas (habilitar WSL2 ou virtualização, instalar runtime, entradas subuid) exigem um passo guiado com consentimento explícito. Se o sistema exigir reinício ou ação de firmware, o preflight informa. Nunca é silenciosa, e nunca exige que o usuário edite configuração.

**B1.5 Agentes, imagens e credenciais.** Versões são fixadas por digest, e mudar o digest ou a política invalida a conformance (`STALE`). Credenciais ficam só no domínio do Core. Agente ou modelo cuja autenticação ou configuração de endpoint não funcione atrás do mediador é marcado `INDISPONÍVEL`, sem relaxar política. A POC deve testar separadamente autenticação por chave e por login/assinatura (quando oferecida), endpoint configurável, telemetria, atualização automática, buscas web nativas e MCPs embutidos; nenhuma dessas funcionalidades pode contornar o mediador.

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

## 6. Rede, dependências e MCPs (B2)

**B2.1 Princípio.** O sandbox do agente não tem egress IP. Todo acesso externo passa por rotas do mediador de egress do Core, definidas por finalidade em política versionada. O gateway de inferência e o mediador de recursos são **funções lógicas separadas**, sob a autoridade do mesmo Core; o mediador não é um proxy genérico. A separação do gateway em processo de SO com privilégio reduzido será avaliada na Slice 0 por causa do processamento de tráfego e credenciais, sem introduzir outro orquestrador ou atribuir autoridade de política a esse processo.

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

## 7. Execução do artefato e preview (B3)

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

**B3.10 Experiência.** Há uma ação única, “Ver o app”. O modo iniciante não mostra portas, contêineres nem URLs técnicas. Após experimentar a primeira entrega, o usuário pode confirmar o comportamento aprovado ou relatar uma divergência; essa confirmação humana não substitui testes automatizados nem altera resultados de evidências.

## 8. Testes de aceite protegidos (B4)

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

## 9. Recuperação, revisão e entrega — contratos F-02 a F-06

**F-02 / REVIEW independente.** A revisão examina um snapshot identificável (`commit/digest`, versão da spec, AT/IT, resultados e política); avalia aderência aos critérios, regressões e achados de risco. Produz `review_report` com achados estruturados, severidade, vínculo ao ticket e resolução/aceitação humana conforme política. É um passo de revisão distinto de `VERIFY` e do parecer não vinculante do Judge. A profundidade é proporcional a FAST/STANDARD/DEEP e pode ser implementada pelo mesmo Core/executor, sem subagente fixo. Falha crítica não resolvida impede `ACCEPTED` quando a política assim determinar; o Judge nunca concede autorização.

**F-03 / checkpoint consistente.** A unidade de recuperação contém IDs e versões de ticket, spec, commit-base/HEAD, snapshot de arquivos não commitados quando aplicável, hashes das evidências, último passo confirmado, idempotency key das ações externas e pendências `UNKNOWN`. O Core persiste transições mediante journal/eventos transacionais em SQLite e reconcilia Git/evidências antes de retomar. Publicação/efeito externo de resultado desconhecido exige consulta ao destino e reconciliação, nunca repetição automática cega. Interrupções entre cada gravação são casos negativos da POC-03.

**F-04 / runner isolado.** O executor confiável **orquestra e observa**, mas nunca importa plugins, drivers ou código do projeto para dentro de seu próprio processo privilegiado. Qualquer código não confiável de aplicação/teste é executado em runtime isolado (perfil `artifact` ou outro perfil sob contrato), com entrypoint, runner e configurações oficiais fixados no domínio confiável. Substituição de runner/configuração por conteúdo do workspace deve ser detectada/rejeitada. Ampliar POC-02/04 para runner/config/plugin tampering.

**F-05 / aceite integrado.** `ACCEPTED` por ticket não implica `DELIVERED` do projeto. O candidato final é montado a partir de um commit e artefato identificados por digest; todos os AT aplicáveis ao conjunto, regressões requeridas e smoke real da forma de execução declarada são executados sobre **o mesmo candidato**. Evidências históricas só podem ser reutilizadas quando a identidade/versionamento e dependências não as tornem `STALE`. A documentação de entrega aponta para o candidato efetivamente testado.

**F-06 / exceção compreensível.** Para stacks suportadas, bloqueios possuem ações de correção via Core que não exigem escolher pacotes, editar `uid_map` ou desativar segurança. Na falta de solução autorizada, mostrar limitação, alternativa funcional e opção de ajustar escopo. O modo avançado pode oferecer exceções versionadas e auditadas onde a política permitir; iniciante nunca aprova uma permissão técnica que não pode julgar. Testar com UX-04.

**Complemento obrigatório de F-02:** A revisão não pode consistir apenas na autoavaliação do implementador: deve ocorrer em contexto separado da conversa de implementação, a partir do snapshot, da especificação e das evidências identificadas. Pode reutilizar o mesmo modelo e a infraestrutura existente. Achados produzidos por LLM são propostas de revisão; seu tratamento segue política explícita e confirmação humana quando exigida, sem converter o LLM Judge em aprovador ou veto automático.

O contexto de REVIEW deve ser reconstruído a partir de dados autoritativos identificados e do commit/digest candidato, e não depender do relato do agente implementador. Nenhum novo agente permanente, serviço ou modelo diferente é exigido.

## 10. Incrementos incorporados na v2.5

### 1. `docs/` no projeto
- Armazena material de referência PDF/Markdown/TXT, inclusive de projetos existentes, sem se tornar fonte normativa de requisitos.
- Leitura somente sob demanda, limitada à raiz permitida e orçamento de conteúdo; arquivos ilegíveis ou não suportados geram erro explícito.
- Recuperações devem citar caminho relativo, hash SHA-256 e página/seção quando disponíveis. A leitura de MD/TXT e a extração de PDF são capacidades previstas, ainda não demonstradas nesta revisão.
- Arquivos e textos recuperados são dados não confiáveis; não executam scripts, comandos nem instruções sobrepostas às políticas. Não abrir URLs automaticamente. O parser/extrator de PDF executa fora do processo privilegiado do Core, em perfil isolado de baixo privilégio.
- Mudanças de requisitos derivadas de documentos passam por aprovação no OpenSpec.
- Nenhum banco vetorial, LLM de RAG ou Obsidian é obrigatório.

### 2. `security-best-practices`
- Incorporação prevista da skill com carregamento seletivo de referência por stack e sob allowlist do Core; não amplia privilégios.
- Antes da incorporação, corrigir os exemplos de redirecionamento de URL e comparação insegura de prefixos de caminho identificados pelo Astra; eliminar a referência ao executor externo `tools/dev.py loop` e adequar comandos à plataforma declarada. Essas correções ainda não foram verificadas em um pacote atualizado.
- Orientações da skill não são evidências de aprovação; scanners, testes e revisão seguem política e executor confiáveis. Achados médios devem poder ser persistidos, mesmo quando a resposta breve omiti-los.

### 3. `grill-me-with-docs` (arquivo identificado e examinado)
- A skill adotada no desenho é **`grill-me-with-docs`**, no conteúdo fornecido e confirmado pelo usuário, identificado pelo hash abaixo independentemente de seu local de armazenamento. O arquivo examinado prevê produção/atualização de proposal/spec/design/tasks do OpenSpec e não chama `grilling` nem `domain-modeling`.
- Proveniência do arquivo examinado: SHA-256 `b2c0947fee31906941f76550264f2fb6d23b3f810a1211754fb14291665cfdc4`. A leitura confirma o conteúdo, não a execução ou compatibilidade experimental.
- Contrato exigido para a integração: DISCOVER só interroga ambiguidades materiais; a skill não edita código de produção e não cria `task.md` ou `blueprint.md` concorrentes. Especificações e decisões propostas integram o OpenSpec, respeitando as aprovações previstas.
- A validação usa `openspec validate <change> --strict` **se suportada pela versão efetivamente instalada**. Confirmar a sintaxe via `openspec --help`; na ausência, bloquear handoff e explicitar incompatibilidade, sem alegar sucesso.
- A skill é subordinada ao roteamento de capacidades e às políticas do Core.
- Ajustes de integração necessários: Context7, histórico remoto e Playwright só podem ser utilizados quando autorizados; sua indisponibilidade deve gerar limitação explícita quando impedir a validação de um requisito. A preferência por 3–5 perguntas não é uma quota obrigatória. “Plan mode” significa permanecer na etapa de especificação do UDH, sem depender de um modo particular de um agente. Retentativas de validação seguem os limites do Core; sucesso do comando não substitui aprovação dos requisitos nem autoriza implementação.

### 4. OpenSpec (já existente)
- Permanece autoridade para intenção aprovada, especificação, decisões e tarefas declarativas. Nenhum segundo plano concorrente.

### 5. tickets integrados ao PLAN/Core, sem skill dedicada
- Tarefas OpenSpec pequenas são materializadas diretamente; decomposição pelo LLM apenas para trabalhos que exijam subdivisão.
- Heurísticas: fatias verticais verificáveis, dimensionadas para contexto novo; *expand–contract* para refatorações transversais quando adequado.
- Entrada: ID/versão/hash de mudança OpenSpec, tarefas e critérios aprovados, dependências/contexto permitidos.
- Saída proposta: IDs locais, objetivo, links às tarefas/critérios de origem, dependências, verificações propostas, lacunas.
- Validação do Core: identidade/versão da spec, unicidade, referências válidas, ciclos, cobertura mínima, idempotência. Aprovação semântica ainda exige os gates previstos pela v2.4. SQLite é autoridade de estado; nunca inferir ACCEPTED de uma proposta do LLM.
- Ao mudar a versão OpenSpec, propostas anteriores ficam obsoletas e exigem reconciliação explícita.

## 11. Validação e critérios de implementação

**Estados de evidência:** `CONSENSO` documenta decisão; `IMPLEMENTADA` indica artefato examinável; `VALIDADA` requer execução real com evidências reproduzíveis ligadas a versão, política e runtime. `UNVERIFIED` e `NONCOMPLIANT` bloqueiam execução com escrita no escopo protegido. Mudança relevante marca evidência `STALE`.

| Teste | Deve provar | Estado documental |
|---|---|---|
| POC-01 | Isolamento rootless, políticas efetivas, IPC de inferência, rede e acesso proibido | NOT_RUN |
| POC-02 | Testes oficiais e baselines fora do alcance do agente, incluindo adulteração | NOT_RUN |
| POC-03 | Handoff e recuperação entre agentes, snapshots e idempotência | NOT_RUN |
| POC-04 | Coleta oficial de evidências não falsificável por saída editável do agente | NOT_RUN |
| DOC-01/02 | Consulta seletiva, proveniência e defesa contra conteúdo/links maliciosos | NOT_RUN |
| SKILL-01/02 | Seleção autorizada, correção dos exemplos inseguros de URL/caminho | NOT_RUN |
| DISC-01 | Descoberta proporcional e handoff OpenSpec real | NOT_RUN |
| TICKET-01/02 | Referências, DAG, idempotência e retomada sem duplicação | NOT_RUN |

**Critérios adicionais de aceite de instalação (B1):** testar (1) instalação limpa do Windows 11, incluindo consentimento/reinício quando necessário, sem terminal obrigatório ao iniciante; (2) instalação em Debian/Ubuntu nativos sem instalação ou invocação de WSL2; (3) máquina Linux headless via procedimento administrativo documentado; (4) falha de pré-requisito com bloqueio e orientação acionável; (5) equivalência de políticas de isolamento e evidências por plataforma. Os resultados permanecem `NOT_RUN` até teste real.

**Dependências críticas de implementação:** provar autenticação (API e fluxos de login quando disponíveis) e endpoint dos CLIs atrás de mediação, UID/UDS rootless em Linux e WSL2, preflight de isolamento, integridade da store AT, coleta oficial e smoke/preview em origem isolada. O Core deverá aplicar limites por ticket de chamadas, tokens, tempo e custo, conforme política; valores serão determinados por testes, não inventados no desenho. Para `/docs`, PDF deve ser extraído em execução de baixo privilégio; agentes não podem converter textos lidos em políticas ou comandos.

**Ordem de construção:** (0) spike de agente, sandbox e inferência em Linux/WSL2; (1) Core + SQLite + policy + executor/AT; (2) handoff com segundo agente; (3) DISCOVER/OpenSpec/PLAN, skills e artefato/preview; (4) interface e jornada do iniciante; (5) terceiro agente, Memorix, Judge consultivo e extensões DEEP. Cada etapa requer evidências próprias; estágios parciais não são rotulados como produto entregue.

**Pendências de aprovação e execução:** ratificação formal da emenda ADR-018 A e do congelamento; correção real dos exemplos identificados em `security-best-practices`; verificação de integridade e versão das skills incorporadas; POCs fundacionais e testes incrementais; seleção do primeiro agente mediante conformance, não por preferência presumida.

## 12. Escopo e limites

- O baseline não define Rust/Rails, UI específica, esquema definitivo SQLite nem módulos Python obrigatórios. **Python com abordagem stdlib-first** é a direção recomendada de implementação, ainda não registrada como ADR; dependências maduras e fixadas por versão/hash podem ser utilizadas para HTTP e transporte de streaming em vez de protocolos de segurança implementados manualmente.
- Nano-RAG, embeddings, Obsidian, Jev/Laya, GraphRAG e a skill `to-tickets` não são dependências do MVP.
- A documentação é normativa **para desenho**, não atestado de segurança ou implementação. Modificar requisitos exige change request e rastreabilidade.
- O arquivo histórico separado não é necessário para interpretar este baseline e não cria precedência sobre ele.


## 13. Pendências explícitas, sem novas decisões arquiteturais

- **Aprovação:** ratificação formal da emenda ADR-018 A e congelamento do desenho pelo usuário.
- **Skills:** validar os bytes/versões efetivamente instalados. `security-best-practices` exige correção comprovada dos exemplos de redirecionamento e contenção de caminho antes do uso; nenhum resultado anterior prova que um pacote atualizado foi auditado.
- **A confirmar em POC:** isolamento real de gateway/CLI, autenticação por assinatura quando aplicável, compatibilidade Linux e WSL2, extração de PDF em processo isolado, migração entre agentes, integridade de AT e evidência oficial.
- **A detalhar ao implementar:** primeira stack suportada, contratos exatos dos adapters, esquema SQLite, orçamento de execução por ticket e confirmação de experiência no primeiro app.
- **Status factual:** todas as POC-01–04 e DOC/SKILL/DISC/TICKET permanecem `NOT_RUN`; este texto não atesta código produzido nem segurança validada.
