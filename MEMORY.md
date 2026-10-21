# UDH — retomada curta

Atualizado: 21/10/2026. Código de referência do spike: `518d9b0`.
Nota contextual: conferir Git e fontes; não é requisito, autorização ou aceite.

## Estado
- Slice 0 encerrada para VM Linux/API gratuita; produto completo não certificado.
- Pi e OpenCode receberam resposta real via OpenRouter; chave só no gateway externo, agentes sem egress IP. Custos normais reportados: zero.
- Não há Core, SQLite operacional, harness/gates ou AT implementados.
- Próximo passo: aprovar a [tarefa 1 da Slice 1](openspec/changes/slice1-core-state/proposal.md): proposta revisada de bootstrap SQLite modular, com repetição idempotente. Início bloqueado: AT independentes ou exceção explícita de protótipo; validação OpenSpec ou exceção manual.

## Fontes sob demanda
- [Resultado, limites e reprodução](SPIKE-SLICE0-RESULTADOS.md).
- [Escopo e aprovações do spike](openspec/changes/spike-slice0-isolamento/proposal.md).
- [Evidência real](spike/slice0/evidence/free-models-probe.json).

## Cuidados de retomada
- Pi pode retornar exit0 com erro: conferir eventos. Configuração RO exige proteger também o diretório contra rename e permitir locks.
- Gateway do spike envia somente prompt fixo; não promover diretamente a produto.
- A3/B5 parciais; assinatura e WSL2 não executados. Detalhes no relatório.
- VM Debian com Podman; binários existentes montados RO, sem novas instalações. Não atribuir limitações à máquina física.
- Preferências: sem skills globais; informar próximo passo ao terminar. Não imprimir credenciais nem ambiente.
- Core modular: domínio, armazenamento, serviço e CLI; autoridade única não significa arquivo monolítico. Memorix/ai-memory não instalados.

## Pendências locais
- Arquitetura `UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md` ainda fora do Git; baseline antigo excluído no working tree. Mudanças preexistentes: não restaurar/incorporar silenciosamente. Emenda ADR-018 A pendente.
- Conferir `git status --short` e `git log -5 --oneline`. Resultados anteriores não equivalem a testes nesta sessão; não repetir inferência só para recuperar contexto.
