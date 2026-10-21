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
