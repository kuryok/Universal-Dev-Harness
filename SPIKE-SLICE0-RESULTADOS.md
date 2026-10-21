# Slice 0 — resultado do spike na VM

Data: 21/10/2026. **Experimento descartável; rodada Linux/API gratuita concluída, com escopo explícito.**

A rodada local foi executada e todas as 29 perguntas receberam resultado. **C1 e D2 agora têm prova real:** Pi e OpenCode receberam resposta do modelo gratuito `nvidia/nemotron-3-ultra-550b-a55b:free`, via OpenRouter, com agentes em `network=none` e chave somente no gateway externo. A auditoria registrou custo zero nas três chamadas normais da rodada final. O corte durante uma quarta chamada real encerrou o Pi com erro estruturado, sem timeout; essa chamada interrompida não possui recibo final de uso.

**Decisão: seguir para a especificação da Slice 1 no perfil Linux/API**, usando Pi 1.2.0 como primeiro agente recomendado. Assinatura, Windows/WSL2 e produto completo não foram certificados. A3/B5 mantêm limites explícitos; não foram convertidos em garantia universal de isolamento. A recomendação não autoriza implementação fora de uma tarefa/spec aprovada.

## Ambiente e identidade

- VM Linux informada pelo usuário: Debian 13, kernel `6.12.111+deb13-amd64`, x86_64, UID/GID 1000. `systemd-detect-virt` retornou `microsoft`; isso não certifica WSL2 nem a máquina física. Ver `environment.json`.
- Podman 5.4.2 rootless, cgroup v2. Memória, processos e CPU foram exercitados; a lista de controladores do `podman info`, isoladamente, não demonstrava todos os limites efetivos.
- Imagem local: Alpine 3.20, digest `sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc`. Nenhuma imagem ou dependência foi baixada.
- Pi 1.2.0 e Node 22.23.3 já instalados; OpenCode 1.18.35 encontrado fora do PATH. Binários/bibliotecas e runtime Pi foram montados somente leitura. Os hashes estão nas evidências. Isso é uma composição experimental sobre a imagem Alpine, não uma imagem final de agente empacotada para produto.
- Sem home pessoal, credenciais reais ou configuração pessoal montados nos contêineres. Nos contêineres há apenas fixtures, prompt de teste e capacidade local sintética; a credencial OpenRouter real é resolvida exclusivamente pelo processo externo do gateway. Nenhum CLI local de modelo foi encontrado entre `ollama`, `llama-server` e `vllm` no PATH; isso não é inventário de todos os serviços possíveis.

## Matriz preenchida

As perguntas e seus critérios originais permanecem em `SPIKE-SLICE0-PERGUNTAS.md`. Caminhos de evidência abaixo são relativos a `spike/slice0/evidence/`. “Passou” refere-se ao experimento descrito, não ao produto ou a uma garantia universal de segurança.

| ID | Resultado | Evidência e alcance |
|---|---|---|
| A1 | passou | `results.json`, A1: UID/GID 1000, rootless, capabilities efetivas zeradas, NoNewPrivs=1 e seccomp ativo. |
| A2 | passou | `results.json`: memória 64 MiB, exit137/OOMKilled=true; pids.max=32, fork recusado/exit2; CPU 25000/100000, 20 throttles e uso de 3170 → 518194 µs em cerca de 2 s. CPU limita taxa; não encerra o processo. |
| A3 | parcial | Só workspace sintético e recursos explicitamente montados ficam disponíveis como dados do experimento. Imagem, /proc, /sys e dispositivos também existem; o critério literal “só a pasta” não foi demonstrado. Inspeções em `results.json` e `agent-probe.json`. |
| A4 | não executado | Discos Windows/automount de WSL2 não exercitados nesta VM Linux. |
| A5 | não executado | Interoperabilidade com executáveis Windows não exercitada. |
| B1 | passou | Sentinela externa ao bind inacessível pelo caminho absoluto; `results.json`, registro combinado A3/B. |
| B2 | passou | Symlink validado no host aponta para sentinela existente; fica pendente/inacessível no contêiner. Mesmo registro. |
| B3 | passou | Socket do Podman existe no host e não aparece nos caminhos testados do contêiner; sem bind do runtime. `results.json`. |
| B4 | passou | No perfil testado: network=none, somente loopback, TCP/UDP IPv4/IPv6 externos retornam ENETUNREACH e DNS ECONNREFUSED. O mesmo cliente Node recebe controles positivos locais TCP/UDP. Receptores do host não recebem marcadores do contêiner. `agent-probe.json`, B4-E1-E2-network; `network-probe.json`. Não prova resistência a comprometimento do kernel. |
| B5 | parcial | Escrita persistente nos caminhos comuns fora do workspace foi recusada; raiz RO e tmpfs automáticos desativados. /proc e dispositivos especiais existem; não foi provada impossibilidade de qualquer escrita fora do workspace. |
| B6 | passou | Variável sintética ausente também em /proc/1/environ; arquivo sintético externo inacessível diretamente e por symlink. `results.json`. O ensaio sintético B6 não usou credenciais reais; os agentes isolados não leram stores pessoais. |
| C1 | passou | Resposta REAL UDH_REAL_PROBE_OK recebida por Pi e OpenCode via gateway/UDS com network=none. `free-models-probe.json`: término normal estruturado, exit0 e auditoria completa por agente; `free-gateway-audit.jsonl`, modelo :free selecionado e custo reportado0. Não é resposta sintética. |
| C2 | passou | `agent-probe.json`: rota/método/finalidade/modelo recusados, capacidade incorreta 401, corpo excedente 413, schema/framing inválidos 400. Rota/método/finalidade recusados também pela ponte. Socket 0600, diretório 0700 e peer UID verificado. Mediador sintético; limites adicionais abaixo. |
| C3 | passou | Streaming sintético e REAL exercitados. Pi/OpenCode receberam eventos do modelo real; auditoria contém múltiplos eventos SSE e encerramento completo. `agent-probe.json`, `free-models-probe.json` e `free-gateway-audit.jsonl`. |
| C4 | passou | Mediador morto tanto no ensaio sintético quanto no real, depois de texto parcial efetivamente entregue. Pi registra stopReason:error, sem timeout, embora retorne exit0. `free-models-probe.json`, pi-free-mediatorkilled: received_partial_text=true, mediator_returncode=-9, cleanup0. |
| C5 | passou | Resolvedor oficial do Pi entrega a chave apenas à memória do gateway. Scan opaco verificou ausência exata da chave nos artefatos examinados; não são registrados cabeçalhos, auth store ou erros brutos do provedor. `free-models-probe.json`, secret_scan, e `free-secret-scan.json`. Não prova ausência de toda forma de exfiltração. |
| D1 | passou | Pi e OpenCode consumiram endpoint customizado real pelo mesmo gateway externo, sem egress IP próprio. Provedor OpenRouter/modelo gratuito, distinto do acesso público nativo OpenCode. `free-models-probe.json`. |
| D2 | passou | Credencial OpenRouter resolvida externamente pelo CLI oficial do Pi em modo readonly/no-refresh, injetada somente no HTTPS upstream. Nos agentes há só capacidade local sintética. Ambos receberam resposta real; inspeções/mounts/ambiente e scan em `free-models-probe.json`. |
| D3 | não executado | Autenticação por assinatura não exercitada. Nenhum auth store pessoal foi montado nos agentes; somente o resolvedor oficial externo consultou a credencial OpenRouter existente. Não afirmar suporte nem incompatibilidade de assinatura. |
| D4 | passou | Caminho mínimo agora exercitado: API OpenRouter com modelo :free, chave mantida pelo gateway e capacidade local nos agentes. Assinatura não testada permanece fora do escopo declarado; nenhum fallback pago é permitido. |
| E1 | passou | Perfil Pi sem ferramentas expostas ao modelo e sem egress IP; clientes reais dentro do mesmo perfil confirmam bloqueio. Não foi feita busca web real. |
| E2 | passou | Pi offline/telemetria desligada no perfil e egress IP bloqueado. OpenCode aceita autoupdate=false e flags disable*; isso não certifica ausência universal de canais de exfiltração por inferência. |
| E3 | passou | Perfil Pi com no-mcp/no-extensions/no-skills/no-tools; auditoria recebe has_tools=false. OpenCode tem plugins vazios/pure e flags restritivas, mas uma requisição ainda declara ferramentas: não foi certificado como perfil sem ferramentas. |
| E4 | passou | **Variante inicial falhou:** renomear diretório pai sombreou configurações RO (`pi-parent-rename-variant.json`). Perfil corrigido fixa diretório como mountpoint e arquivos RO; escrita, remoção, symlink e rename do pai são recusados. Hashes intactos em `agent-probe.json`. Não certificar configuração OpenCode por esse resultado. |
| F1 | passou | Processo sintético declara sucesso, grava relatório falso e sai23; coletor externo registra official_success=false. `f-probe.json`. Não é executor confiável do produto. |
| F2 | passou | Fixture protegida SINTÉTICA resiste a escrita, remoção e substituição; hash antes/depois idêntico. `f-probe.json`. Prova prevenção das tentativas, não detecção semântica nem AT real. |
| F3 | passou | SIGKILL durante gravação deixa temporário JSON inválido e checkpoint antigo; antes do replace preserva antigo; depois preserva novo; controle normal passa. `f-probe.json`. Corte de processo local, não queda de energia nem Core/SQLite. |
| G1 | não executado | Só esta VM foi exercitada; comparação WSL2 depende de outra execução. Não presumir paridade. |
| G2 | não executado | Sem execução WSL2 não há causa de falha exclusiva a atribuir. |

## Decisão e riscos que sobraram

**Primeiro agente recomendado para a Slice 1: Pi 1.2.0**, pelas provas de endpoint real, UDS, streaming, interrupção real e configuração protegida neste perfil. OpenCode 1.18.35 também completou inferência real pelo OpenRouter. Não foi integrado segundo agente de produto nem certificado um perfil Developer completo: o gateway deste spike só encaminha o prompt fixo de teste, sem contexto/código recebido do agente.

- **C1/D2 resolvidos neste perfil:** modelo :free confirmado por preço de prompt/completion zero no [catálogo público OpenRouter](https://openrouter.ai/api/v1/models) antes da rodada; teto max_price=0, sem fallback pago, até quatro chamadas por processo e prompt/token/tempo limitados. A chave existente foi usada pelo resolvedor oficial do Pi, sem atualizar ou copiar instalações/configurações pessoais. D3 permanece não executado: isso não certifica login por assinatura.
- **OpenCode nativo versus experimento:** o usuário demonstrou DeepSeek V4.1 Flash Free funcionando em seu terminal. As tentativas HTTP públicas separadas de Big Pickle e DeepSeek retornaram403/FreeTierError, com evidência em `public-free-probe.json`. Isso não invalida seu CLI ou todos os modelos free; o perfil e os atributos de acesso são diferentes. A prova real bem-sucedida dos dois agentes foi feita pelo OpenRouter.
- **Roteamento genérico gratuito:** uma primeira rodada usou openrouter/free, que escolheu um classificador em uma chamada OpenCode e não satisfez o marcador esperado. Preservada em `free-router-attempt.json`; não foi contada como sucesso dos dois agentes. A rodada final fixou um modelo gratuito de conversa e verificou término estruturado, exit0, marcador, auditoria por agente e custo reportado0.
- **Corte real e recibo:** uma tentativa inicial sinalizou o corte antes do flush do evento de texto; a verificação de texto parcial detectou a insuficiência e falhou. Preservada em `free-cut-before-flush.json`. O perfil final sinaliza depois de entregar evento SSE completo; received_partial_text=true e erro estruturado confirmados. Não há recibo final da chamada morta por SIGKILL; somente as chamadas normais têm custo reportado zero.
- **C4/F1:** o adaptador futuro deve conferir os eventos estruturados do Pi; exit0 não demonstra conclusão de inferência. O spike não implementa esse adaptador de produto.
- **E4:** arquivos RO dentro de diretório renomeável não bastam. A configuração experimental corrigida protege o mountpoint e permite locks no workspace. O Pi falhou inicialmente quando todo o diretório de configuração era RO, pois precisa criar settings.json.lock/auth.json.lock; essa falha foi preservada em `pi-readonly-directory-failure.json`.
- **A2/OpenCode:** listagem models falhou com OOM em 256 MiB e passou em 768 MiB; inferência sintética passou em 768 MiB. Pi passou em 384 MiB. Não foi medido mínimo exato nem benchmark comparável de consumo entre agentes.
- **C2:** protótipo com limite de corpo, oito conexões simultâneas e timeout de inatividade de 3 s. O encaminhamento HTTPS real tem deadline de60s, destino fixo, TLS verificado e limite de resposta; a leitura inicial de cabeçalhos continua sem prazo absoluto contra envio lento. Não é gateway de produção. A prova com credencial real foi restrita ao prompt fixo e aos controles descritos, não ao envio de código/contexto arbitrário.
- **F1–F3:** evidências ficam fora dos mounts graváveis dos contêineres, porém são editáveis pelo usuário/assistente no host. Não são store selada, executor oficial ou aprovação independente do produto. Nenhum AT real foi criado ou alterado.
- **G:** resultados da VM não certificam máquina física nem WSL2. O erro inicial de filesystem RO no runtime não se repetiu após a mudança de permissões da sessão.

Não foi identificada falha de isolamento IP nos casos executados. O bypass de configuração foi reproduzido, preservado e fechado no perfil experimental; não foi apagado do resultado para aparentar sucesso.

## Como reproduzir e verificar

No diretório do projeto, com os mesmos binários locais e imagem disponível:

```bash
python3 spike/slice0/run.py
python3 spike/slice0/network_probe.py
python3 spike/slice0/agent_probe.py
python3 spike/slice0/evidence_probe.py
python3 spike/slice0/opencode_probe.py --memory-mib 256
python3 spike/slice0/opencode_probe.py --memory-mib 768 --synthetic-inference
python3 spike/slice0/free_models_probe.py --verify-only
# Nova inferência real gratuita usa a credencial Pi/OpenRouter, sem exibi-la:
python3 spike/slice0/free_models_probe.py
python3 -m py_compile spike/slice0/*.py
git diff --check
```

Executar sem concorrência entre roteiros: alguns arquivos de evidência são atualizados a cada rodada. O retorno137 de memória, o retorno2 do teste de processos e o OOM do catálogo OpenCode em 256 MiB são resultados esperados dos experimentos, não gates de produto aprovados. A listagem de processos da VM e stores de credenciais pessoais não precisam ser lidas.

Os roteiros foram executados; compilação e checks das evidências passaram. Uma varredura ampla encontrou PermissionError em fixtures/workspaces dos ensaios e não foi considerada concluída; a abordagem foi alterada para fontes e evidências de entrega, com 30 arquivos examinados e ausência da chave confirmada. Limite registrado em `free-secret-scan.json` e `secret-scan-attempts.json`; isso não é varredura completa do filesystem. Houve revisão em contexto separado; os achados de configuração, framing e limpeza foram incorporados ou mantidos como limite explícito. O Podman já remove cidfiles na limpeza: falhas iniciais de remoção duplicada foram corrigidas e repetidas com sucesso. O caminho longo de UDS foi corrigido com caminho relativo no processo servidor. Nenhuma dependência foi instalada.

`tools/harness.py` e gates do projeto não existem: fast/full **não executados**. OpenSpec CLI ausente: validação pela CLI **não executada**. A emenda ADR-018 A não foi ratificada e não foi necessária para estes ensaios exclusivos de inferência. Exclusão do baseline antigo e arquivo de arquitetura novo, preexistentes, foram preservados sem entrar nos commits desta tarefa.

## Próximo passo

**Especificar a primeira tarefa da Slice 1: Core mínimo + SQLite**, preservando a separação entre estado, política e evidências, com objetivo/arquivos/critérios aprovados antes de implementar. Incorporar os achados do spike: conferir eventos em vez de confiar só no exit0 do Pi, proteger o diretório de configuração contra rename e manter credenciais fora do sandbox. O gateway descartável com prompt fixo não deve ser promovido diretamente a componente de produto.

Manter assinatura e WSL2 como não executados até provas próprias. Ampliar a conformance quando houver perfil Developer, ferramenta de escrita e gateway de contexto real; não presumir que a prova de transporte sozinha aceita software gerado.
