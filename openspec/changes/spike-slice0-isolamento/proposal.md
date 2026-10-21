# Spike Slice 0 — ambiente e isolamento

Estado: recorte A/B aprovado pelo usuário em 21/10/2026; executado parcialmente, com limitações registradas em `SPIKE-SLICE0-RESULTADOS.md`.

## Objetivo

Executar o recorte A/B de `SPIKE-SLICE0-PERGUNTAS.md` no Linux de referência, com Podman rootless, sem código de produto. Não declarar conformidade integral da Slice 0.

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

Grupos C–G, escolha definitiva de agente, inferência real, credenciais, Core, interface, instalador, Judge, memória e novos agentes. Não criar ou alterar testes de aceite.

## Riscos e autorizações

O runtime usa armazenamento e diretórios fora do projeto. Essa operação e eventual download exigem autorização explícita conforme AGENTS.md. Download só depois de identificar imagem e origem; não está autorizado por esta proposta ainda. Não instalar OpenSpec nesta etapa: o executável não foi encontrado; validação pela CLI não executada. Os requisitos deste recorte ficam exclusivamente nesta mudança OpenSpec, sem spec paralela.

## Continuação proposta em 21/10/2026 — C–F e fechamento

Estado deste acréscimo: aguardando aprovação do recorte concreto. O usuário solicitou continuar até finalizar a Slice 0, com auxiliares em paralelo e sem skills globais.

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
