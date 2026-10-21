# Slice 0 — resultado do spike na VM

Data: 21/10/2026. **Experimento descartável; conclusão parcial da fundação.**

A rodada local foi executada e todas as 29 perguntas receberam um resultado. **Não há autorização técnica para declarar a Slice 0 integralmente validada ou avançar automaticamente à Slice 1:** C1 não recebeu uma resposta de modelo real, D2 não exercitou injeção de credencial real pelo gateway e D3 não exercitou assinatura. A comparação WSL2 fica pendente para outro ambiente. Os demais resultados abaixo valem somente para os perfis e casos efetivamente testados.

## Ambiente e identidade

- VM Linux informada pelo usuário: Debian 13, kernel `6.12.111+deb13-amd64`, x86_64, UID/GID 1000. `systemd-detect-virt` retornou `microsoft`; isso não certifica WSL2 nem a máquina física. Ver `environment.json`.
- Podman 5.4.2 rootless, cgroup v2. Memória, processos e CPU foram exercitados; a lista de controladores do `podman info`, isoladamente, não demonstrava todos os limites efetivos.
- Imagem local: Alpine 3.20, digest `sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc`. Nenhuma imagem ou dependência foi baixada.
- Pi 1.2.0 e Node 22.23.3 já instalados; OpenCode 1.18.35 encontrado fora do PATH. Binários/bibliotecas e runtime Pi foram montados somente leitura. Os hashes estão nas evidências. Isso é uma composição experimental sobre a imagem Alpine, não uma imagem final de agente empacotada para produto.
- Sem home pessoal, credenciais reais ou configuração pessoal montados. Apenas fixtures, prompts e capacidade de autenticação inteiramente sintéticos. Nenhum CLI local de modelo foi encontrado entre `ollama`, `llama-server` e `vllm` no PATH; isso não é inventário de todos os serviços possíveis.

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
| B6 | passou | Variável sintética ausente também em /proc/1/environ; arquivo sintético externo inacessível diretamente e por symlink. `results.json`. Não foram lidas credenciais reais. |
| C1 | não executado | Nenhum provedor ou modelo real foi configurado. SPIKE_SYNTHETIC_OK é resposta programada, não inferência. |
| C2 | passou | `agent-probe.json`: rota/método/finalidade/modelo recusados, capacidade incorreta 401, corpo excedente 413, schema/framing inválidos 400. Rota/método/finalidade recusados também pela ponte. Socket 0600, diretório 0700 e peer UID verificado. Mediador sintético; limites adicionais abaixo. |
| C3 | passou | Pi recebeu SPIKE_ e SYNTHETIC_OK em eventos separados; SSE direto mostra partes separadas por aproximadamente 150 ms. OpenCode também completou resposta sintética. `agent-probe.json` e `opencode-probe.json`. |
| C4 | passou | Mediador realmente morto após primeira parte. Pi encerra sem timeout e registra stopReason:error/errorMessage:terminated. **Exit do Pi é 0 mesmo no erro.** `agent-probe.json`, C4-mediator-killed. |
| C5 | parcial | Auditoria por run_id não contém o marcador sintético de autenticação; registra apenas campos permitidos. Redação com chave real/injeção do provedor não exercitada. `gateway-audit.jsonl` e checks do Pi. |
| D1 | passou | Pi e OpenCode consumiram endpoint customizado atrás da ponte/UDS com network=none. Resposta sintética; compatibilidade com provedor real ainda não provada. |
| D2 | parcial | Ambos funcionaram sem chave real no contêiner, usando capacidade sintética. Não houve injeção de credencial real fora do sandbox ou inferência autenticada. |
| D3 | não executado | Sem login/assinatura fornecido ao mediador. Nenhum auth store pessoal foi lido. Não afirmar suporte nem incompatibilidade. |
| D4 | passou | Alternativa mínima descrita: primeiro integrar API pelo gateway com credencial mantida pelo operador fora do sandbox, ou modelo local; assinatura permanece indisponível até prova própria. Descrição, não autenticação validada. |
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

**Primeiro candidato: Pi 1.2.0**, pela prova explícita de endpoint, UDS, streaming, interrupção e configuração protegida neste perfil. É candidato à próxima prova, não agente declarado conforme. OpenCode também completou o transporte sintético; não foi instalado segundo agente de produto.

- **C1/D2/D3:** inferência real e autenticação continuam pendentes. A documentação oficial atual do [OpenCode Console](https://opencode.ai/v2/docs/console/models/) orienta autenticação por chave; listar modelos gratuitos no catálogo local não prova acesso anônimo. Nenhuma chamada paga ou autenticada foi feita.
- **C4/F1:** o adaptador futuro deve conferir os eventos estruturados do Pi; exit0 não demonstra conclusão de inferência. O spike não implementa esse adaptador de produto.
- **E4:** arquivos RO dentro de diretório renomeável não bastam. A configuração experimental corrigida protege o mountpoint e permite locks no workspace. O Pi falhou inicialmente quando todo o diretório de configuração era RO, pois precisa criar settings.json.lock/auth.json.lock; essa falha foi preservada em `pi-readonly-directory-failure.json`.
- **A2/OpenCode:** listagem models falhou com OOM em 256 MiB e passou em 768 MiB; inferência sintética passou em 768 MiB. Pi passou em 384 MiB. Não foi medido mínimo exato nem benchmark comparável de consumo entre agentes.
- **C2:** protótipo com limite de corpo, oito conexões simultâneas e timeout de inatividade de 3 s. Não há prazo absoluto contra envio lento; cliente que pinga bytes pode ocupar slots. Não é gateway pronto para uso com segredos ou tráfego de produção.
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
python3 -m py_compile spike/slice0/*.py
git diff --check
```

Executar sem concorrência entre roteiros: alguns arquivos de evidência são atualizados a cada rodada. O retorno137 de memória, o retorno2 do teste de processos e o OOM do catálogo OpenCode em 256 MiB são resultados esperados dos experimentos, não gates de produto aprovados. A listagem de processos da VM e stores de credenciais pessoais não precisam ser lidas.

Os roteiros foram executados; compilação e checks das evidências passaram. Houve revisão em contexto separado; os achados de configuração, framing e limpeza foram incorporados ou mantidos como limite explícito. O Podman já remove cidfiles na limpeza: falhas iniciais de remoção duplicada foram corrigidas e repetidas com sucesso. O caminho longo de UDS foi corrigido com caminho relativo no processo servidor. Nenhuma dependência foi instalada.

`tools/harness.py` e gates do projeto não existem: fast/full **não executados**. OpenSpec CLI ausente: validação pela CLI **não executada**. A emenda ADR-018 A não foi ratificada e não foi necessária para estes ensaios exclusivos de inferência. Exclusão do baseline antigo e arquivo de arquitetura novo, preexistentes, foram preservados sem entrar nos commits desta tarefa.

## Próximo passo

**Completar C1/D2 com um modelo real atrás do gateway**, mantendo network=none no agente e sem colocar credenciais no workspace. Pode ser endpoint local sem autenticação ou gateway externo configurado pelo operador. Repetir streaming/interrupção nesse caminho, registrar decisão por agente e então avaliar a liberação para Slice 1. D3 e WSL2 exigem provas próprias; eventual escopo Linux/API deve ser assumido explicitamente, sem declarar esses casos como aprovados.
