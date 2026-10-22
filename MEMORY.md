# UDH — retomada curta

Atualizado: 22/10/2026. Candidato migração: `96bd4dc`; selo v3: `d1362b11`.
Retomada: ler [STATE.md](STATE.md), conferir Git e fontes. Memória não é requisito, autorização ou aceite.

## Estado
- Slice 0 encerrada para VM Linux/API gratuita; produto completo não certificado.
- Pi e OpenCode receberam resposta real via OpenRouter; chave só no gateway externo, agentes sem egress IP. Custos normais reportados: zero.
- Core mínimo + SQLite v1 implementados: cinco módulos, somente PENDING; init/create/show, atomicidade e idempotência. [Verificação](openspec/changes/slice1-core-state/core-verification.json): 9 complementares + 10 AT PASS; A8 PASS.
- Regressão I1–I7/T1–T8 PASS; selo v1 e AT preservados. Core verificado por 49 chamadas isoladas. Não é ACCEPTED/DELIVERED; Slice 1 incompleta. CLI OpenSpec/harness ausentes.

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
- Migração/leitura v2 implementadas; 28 complementares, regressões e smoke PASS. M3 teve INFRA_ERROR; diagnóstico isolado passou, corrida IPC reproduzida. [Estado e próximo passo](STATE.md): corrigir observador, versionar/retestar antes de liberar bloqueio. Não declarar aceite completo. Sem execução de agentes.

## Pendências locais
- Arquitetura `UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md` ainda fora do Git; baseline antigo excluído no working tree. Mudanças preexistentes: não restaurar/incorporar silenciosamente. Emenda ADR-018 A pendente.
- Conferir `git status --short` e `git log -5 --oneline`. Resultados anteriores não equivalem a testes nesta sessão; não repetir inferência só para recuperar contexto.
