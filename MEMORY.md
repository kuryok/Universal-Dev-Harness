# UDH — retomada curta

Atualizado: 21/10/2026. Código de referência do spike: `518d9b0`.
Nota contextual: conferir Git e fontes; não é requisito, autorização ou aceite.

## Estado
- Slice 0 encerrada para VM Linux/API gratuita; produto completo não certificado.
- Pi e OpenCode receberam resposta real via OpenRouter; chave só no gateway externo, agentes sem egress IP. Custos normais reportados: zero.
- Core/SQLite operacional ausentes. [Integração externa](openspec/changes/slice1-core-state/integration-results.md): I1–I7 PASS, regressão T1–T8 PASS e revisão independente.
- Selo bootstrap v1 em `bootstrap/slice1/seals/v1/`; originais preservados, variante independente em integrated_acceptance. Baseline: 10 métodos, 18 falhas esperadas, zero erros; não é aceite. CLI OpenSpec/harness ausentes.

## Fontes sob demanda
- [Resultado, limites e reprodução](SPIKE-SLICE0-RESULTADOS.md).
- [Slice 1: escopo, resultado e bloqueio](openspec/changes/slice1-core-state/proposal.md).
- [Evidência real](spike/slice0/evidence/free-models-probe.json).

## Cuidados de retomada
- Pi pode retornar exit0 com erro: conferir eventos. Configuração RO exige proteger também o diretório contra rename e permitir locks.
- Gateway do spike envia somente prompt fixo; não promover diretamente a produto.
- A3/B5 parciais; assinatura e WSL2 não executados. Detalhes no relatório.
- VM Debian com Podman; binários existentes montados RO, sem novas instalações. Não atribuir limitações à máquina física.
- Preferências: sem skills globais; informar próximo passo ao terminar. Não imprimir credenciais nem ambiente.
- Próximo: Core mínimo + SQLite modular, conforme proposta. Não editar AT/selo; não atualizar hashes silenciosamente. A proteção cobre o candidato contido, não esta sessão no host. Memorix/ai-memory não instalados.

## Pendências locais
- Arquitetura `UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md` ainda fora do Git; baseline antigo excluído no working tree. Mudanças preexistentes: não restaurar/incorporar silenciosamente. Emenda ADR-018 A pendente.
- Conferir `git status --short` e `git log -5 --oneline`. Resultados anteriores não equivalem a testes nesta sessão; não repetir inferência só para recuperar contexto.
