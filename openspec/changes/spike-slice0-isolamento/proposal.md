# Spike Slice 0 — ambiente e isolamento

Estado: recortes experimentais A–F aprovados pelo usuário em 21/10/2026. Rodada Linux/API gratuita encerrada com 29 perguntas classificadas; C1/D2 reais comprovados na continuação aprovada e assinatura/WSL2 ainda não executados, conforme `SPIKE-SLICE0-RESULTADOS.md`.

## Objetivo

Executar o spike de `SPIKE-SLICE0-PERGUNTAS.md` na VM Linux de referência com Podman rootless, sem código de produto. O recorte inicial A/B foi ampliado para C–F conforme a aprovação registrada abaixo. Não declarar conformidade integral sem inferência e autenticação reais.

## Decisões e escopo

- Usar a arquitetura consolidada v2.5.1 como referência de desenho, sem ratificar a emenda ADR-018 A.
- Diagnosticar primeiro o erro observado: `set sticky bit on: chmod /run/user/1000/libpod: read-only file system`. Distinguir restrição da sessão de falha do host; não alterar permissões do host automaticamente.
- Inventariar imagens locais antes de propor download. Identificar a imagem efetivamente usada por digest. Nenhuma dependência Python nova.
- Não ler credenciais reais. Usar apenas sentinelas e variáveis sintéticas nos experimentos.
- Manter as alterações preexistentes do usuário intactas.

## Arquivos permitidos

- `openspec/changes/spike-slice0-isolamento/`: proposta e eventuais esclarecimentos deste recorte.
- `spike/slice0/`: scripts descartáveis, fixtures sintéticas e evidências reproduzíveis.
- `SPIKE-SLICE0-RESULTADOS.md`: atualização de preparação e linhas A/B, com referência às evidências.

## Critérios verificáveis

- Registrar versão do runtime, ambiente, rootless e cgroups com saída e código de retorno → verificação: comandos de diagnóstico registrados.
- A1: executar contêiner trivial como usuário comum → verificação: saída, código de retorno e inspeção efetiva do runtime.
- A2: exercitar memória, processos e CPU separadamente, com tempo e recursos limitados → verificação: eventos/contadores e inspeção dos limites. CPU deve demonstrar limitação de taxa; throttling não será descrito como encerramento.
- A3 e B1–B3/B5–B6: testar mounts, sentinela externa ao workspace do contêiner, symlink, socket, escrita e dados sintéticos → verificação: tentativas negativas e configuração inspecionada, sem segredos reais.
- B4: verificar isolamento de rede e tentativas controladas por nome/IP/portas → verificação: configuração e resultados individuais; falha de DNS isolada não prova ausência de egress.
- A4/A5: registrar que Windows/WSL2 não foram exercitados neste Linux → verificação: identificação do ambiente; não contar como passou.
- Cada conclusão tem estado honesto e evidência; restrições da sessão ficam explícitas → verificação: conferência entre relatório, logs e códigos de saída.

## Sequência e verificação

1. Diagnosticar runtime e inventariar imagens; registrar evidências em `spike/slice0/`. Se o acesso continuar bloqueado, registrar o bloqueio e interromper a execução dependente.
2. Com runtime utilizável e imagem disponível, preparar e executar experimentos A/B em `spike/slice0/`, sem montar a raiz do repositório com escrita no contêiner. Conferir cada saída e configuração.
3. Atualizar `SPIKE-SLICE0-RESULTADOS.md`, conferir referências e fazer commit apenas dos arquivos deste recorte.

Não existe `tools/harness.py` nem configuração de lint/testes neste repositório. Scripts Python, se necessários, serão verificados por compilação sintática e execução dos experimentos pertinentes; Markdown por `git diff --check`. Isso não constitui aceite independente nem prova de segurança do produto.

## Fora de escopo

Core de produto, interface, instalador, Judge, memória, novos agentes instalados e testes de aceite reais. C–F foram incluídos como experimentos descartáveis na continuação aprovada. Inferência API real foi incluída na continuação gratuita aprovada abaixo. Autenticação por assinatura e comparação WSL2 não foram executadas por falta dos respectivos recursos.

## Riscos e autorizações

O runtime usa armazenamento e diretórios fora do projeto. Essa operação e eventual download exigem autorização explícita conforme AGENTS.md. Download só depois de identificar imagem e origem; não está autorizado por esta proposta ainda. Não instalar OpenSpec nesta etapa: o executável não foi encontrado; validação pela CLI não executada. Os requisitos deste recorte ficam exclusivamente nesta mudança OpenSpec, sem spec paralela.

## Continuação proposta em 21/10/2026 — C–F e fechamento

Estado deste acréscimo: aprovado pelo usuário em 21/10/2026, ao autorizar trabalho e análises completas da Slice 0 na VM isolada, sem credenciais nos agentes. Auxiliares em paralelo autorizados; sem skills globais.

Objetivo: preencher todas as linhas da matriz com resultado e evidência, experimentando o Pi 1.2.0 já instalado, sem credenciais reais. Fechar o relatório não significa passar todos os critérios nem autorizar Slice 1.

Arquivos permitidos adicionais: scripts, configuração sintética, fixtures e evidências em `spike/slice0/`; seções C–G e decisão em `SPIKE-SLICE0-RESULTADOS.md`. A arquitetura e testes de aceite reais permanecem intocados.

- Completar B4/B6 com preflight de ferramentas, receptores locais controlados IPv4/IPv6 TCP/UDP, fixture de credencial sintética e configuração somente leitura. Verificar eventos nos receptores, não apenas exit codes dos clientes.
- C2–C5: experimentar mediador descartável de inferência por Unix socket com allowlist de rota/método/finalidade, limites de entrada e tempo, fluxo SSE sintético, interrupção e auditoria sem cabeçalhos sensíveis. Verificar recusa de rota indevida, partes entregues, encerramento limitado e ausência do marcador sintético nos logs. Sem comunicação externa.
- D1/D2 e E: rodar Pi existente em contêiner `network=none`, com configuração sintética e runtime somente leitura, sem home real, sem MCP/extensões/skills/atualização. Usar endpoint local ligado ao Unix socket. Verificar que uma resposta sintética chega, que tools externas ficam desativadas e que o arquivo de configuração não pode ser alterado. Registros de processo e configuração, sem tratar flags isoladas como prova.
- F1–F3: falsificar sucesso no workspace e contrastar com código de saída externo; tentar adulterar arquivo protegido SINTÉTICO montado somente leitura e conferir hash externo; interromper gravação sintética e recuperar último checkpoint íntegro. Não criar/editar AT reais; não construir Core/SQLite de produto.
- C1, D2 real e D3: sem chamada real ao provedor não podem passar. Não acessar autenticação existente do usuário. Solicitar caminho seguro operado pelo usuário se necessário; fixtures não substituem inferência real ou assinatura.
- G/A4/A5: comparação WSL2 permanece não executada nesta VM. Fornecer roteiro reproduzível; não atribuir restrições da VM à máquina física.
- Fechamento: síntese de agente candidato versus comprovado, riscos e decisão de avançar/bloquear ligada aos IDs; commit pequeno com verificações sintáticas, experimentos e `git diff --check`.

Sem dependências novas nem download previstos. Se Pi e seu runtime instalado forem incompatíveis com a imagem local, registrar e propor alternativa antes de baixar. Uso de fixtures/modelo falso será identificado em cada conclusão.


## Encerramento da rodada local

Experimentos A–F possíveis sem credenciais executados; todas as perguntas A–G classificadas no relatório, com casos não executados justificados. Pi e OpenCode consumiram resposta sintética por UDS, sem rede externa no contêiner. Foram preservadas falhas de configuração e OOM; não houve instalação de dependências. Evidências e limites em `SPIKE-SLICE0-RESULTADOS.md`. Ao final da rodada sintética, C1/D2 reais ainda estavam pendentes; foram exercitados na continuação gratuita abaixo. D3 e comparação WSL2 seguem não executados; nenhum selo de produto.


## Continuação aprovada — modelos gratuitos reais

O usuário solicitou explicitamente usar modelos free em OpenCode e Pi e autorizou usar a credencial OpenRouter já configurada, se existir. Os requisitos permanecem no mesmo OpenSpec: provar C1/D2/C3/C5 com resposta real mantendo agentes network=none e credenciais somente no mediador externo.

Arquivos permitidos: roteiros/gateway descartáveis e evidências em `spike/slice0/`, esta proposta e relatório de resultados. Sem alterações em AT, arquitetura, harness ou credenciais/configuração pessoal. Sem novas dependências.

- Selecionar apenas endpoint/modelo gratuito confirmado em fonte primária/catálogo. Sem fallback para modelo pago, sem cadastro, compra ou assinatura.
- No Pi, o gateway externo pode resolver a entrada OpenRouter existente em memória sem imprimir, copiar, devolver ao agente ou registrar a chave. Usar o resolvedor oficial do Pi em modo somente leitura/no-refresh, capturando a resposta apenas no gateway; não criar scripts de credencial nem expor valores. Se ausente/inválida, registrar somente esse estado sem abrir outros stores.
- No OpenCode, experimentar acesso anônimo aos modelos free disponibilizados pelo provedor. Respeitar recusas, sem simular identidade ou contornar restrições. Pode usar o mesmo provedor free autenticado externamente como alternativa técnica, identificando-a no resultado.
- Gateway com destino HTTPS fixo em allowlist, TLS validado, timeouts/limites de chamadas, tokens e tamanho; agente só recebe capacidade local. Requisições reais usam somente prompt sintético, sem código ou dados do projeto.
- Evidência deve separar origem do modelo, resposta real, erro do provedor, streaming, identidade do run e ausência da chave em logs/mounts/ambiente do agente. Testar retorno proibido e evitar registrar corpo de erro bruto que possa conter cabeçalhos.
- Sem login por assinatura ou teste WSL2 nesta VM. A autorização de uso da credencial não é autorização para exibi-la nem alterá-la.


## Resultado da continuação gratuita

Pi e OpenCode receberam resposta real do modelo gratuito nvidia/nemotron-3-ultra-550b-a55b:free via OpenRouter, com network=none nos agentes. O catálogo foi verificado antes da rodada; não há fallback pago e os custos reportados das chamadas normais foram zero. Credencial existente resolvida pelo CLI oficial Pi auth check --credentials --no-refresh só em memória externa, sem alteração de stores/configuração pessoal ou instalação de outra cópia dos agentes.

Auditoria por agente, término normal, código de saída e marcador foram conferidos juntos. Corte real entregue após texto parcial encerrou o Pi com erro sem timeout, ainda com exit0. Respostas públicas403 do provedor nativo OpenCode e tentativas anteriores insuficientes foram preservadas. C1/D2/C3/C5 agora possuem prova real no escopo descrito. D3/WSL2 permanecem não executados e justificados; recomendação é especificar Slice1 para Linux/API, sem certificar produto nem perfil Developer.
