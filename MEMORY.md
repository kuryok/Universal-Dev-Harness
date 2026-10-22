# UDH — retomada curta

Atualizado: 22/10/2026. Candidato bloqueio: `ad2bd47`; selo v5: `182d2ba7`.
Retomada: ler [STATE.md](STATE.md), conferir Git e fontes. Memória não é requisito, autorização ou aceite.

## Estado
- Slice 0 encerrada para VM Linux/API gratuita; produto completo não certificado.
- Pi e OpenCode receberam resposta real via OpenRouter; chave só no gateway externo, agentes sem egress IP. Custos normais reportados: zero.
- Core SQLite v1/v2: init/create/show/policy; migração explícita e bloqueio administrativo atômico/idempotente. Nove módulos pequenos; [verificação](openspec/changes/slice1-block-run/block-verification.md).
- Verificados: 36 complementares, 13 AT migração/bloqueio, 10 AT v1, 11 AT policy, I1–I7/T1–T8 e revisão PASS. Não é ACCEPTED/DELIVERED; Slice 1 incompleta. CLI OpenSpec/harness ausentes.

## Fontes sob demanda
- [Resultado, limites e reprodução](SPIKE-SLICE0-RESULTADOS.md).
- [Slice 1: contrato de bloqueio](openspec/changes/slice1-block-run/proposal.md).
- [Evidência real](spike/slice0/evidence/free-models-probe.json).

## Cuidados de retomada
- Pi pode retornar exit0 com erro: conferir eventos. Configuração RO exige proteger também o diretório contra rename e permitir locks.
- Gateway do spike envia somente prompt fixo; não promover diretamente a produto.
- A3/B5 parciais; assinatura e WSL2 não executados. Detalhes no relatório.
- VM Debian com Podman; binários existentes montados RO, sem novas instalações. Não atribuir limitações à máquina física.
- Preferências: sem skills globais; informar próximo passo ao terminar. Não imprimir credenciais nem ambiente.
- [Estado e próximo passo](STATE.md): especificar desbloqueio/retomada de BLOCKED, revisões e checkpoints; obter aprovação antes de nova transição. Sem execução de agentes ou aceite oficial.

## Pendências locais
- Arquitetura `UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md` ainda fora do Git; baseline antigo excluído no working tree. Mudanças preexistentes: não restaurar/incorporar silenciosamente. Emenda ADR-018 A pendente.
- Conferir `git status --short` e `git log -5 --oneline`. Resultados anteriores não equivalem a testes nesta sessão; não repetir inferência só para recuperar contexto.
