# Estado do projeto

Atualizado: 22/10/2026. Fase: fundação da Slice 1.

Este arquivo orienta a retomada; não aprova requisitos nem substitui OpenSpec, SQLite, Git ou evidências. Consulte [AGENTS.md](AGENTS.md) para regras e [MEMORY.md](MEMORY.md) para contexto curto.

## Funciona

- Core modular: SQLite guarda estado; Git guarda código; specs ficam em OpenSpec.
- Migração explícita v1→v2, leitura v2 e formatos v1 preservados.
- PENDING→BLOCKED implementado/verificado no candidato `ad2bd47`: entrada administrativa protegida, evento/pedido/autorização/estado atômicos, revisão otimista, replay e conflitos concorrentes.
- Consulta comum de policy continua somente leitura; não concede execução, transição, aceite ou entrega.
- 36 complementares, todos os 13 AT de migração/bloqueio, dez AT v1, onze AT policy e I1–I7/T1–T8 PASS. Revisão independente PASS.
- 171 chamadas auditadas, 15 checkpoints de interrupção: onze na migração e quatro no bloqueio. Zero failures/errors/skips nos AT v5.
- Nove módulos pequenos, 704 linhas totais; selos v1–v5 preservados/verificados. AT protegidos intactos.

## Pendente

- [Desbloqueio BLOCKED/1→PENDING/2](openspec/changes/slice1-unblock-run/proposal.md) e preparo nomeado aprovados. DDL v3 refinado; novos AT independentes e perfil administrativo preparados. Core ainda sem migração/leitura v3 e sem unblock.
- Codec, gate referenciado e novas saídas implementados:85 complementares PASS e revisão independente PASS. I1–I7/T1–T8 PASS; baseline16 com13 falhas esperadas/3 PASS,150 chamadas sem infraestrutura. Geral INCOMPLETE: regressões completas, exportação/AT de transporte e selo7 pendentes; selos1–6/histórico intactos.
- Executor de agentes, coordenação automática até entrega e interface guiada ainda não implementados. OpenSpec é usado documentalmente; sua CLI e o harness fast/full não estão disponíveis.
- Instalação operacional protegida e aceite oficial do produto não demonstrados. Bootstrap usa dados sintéticos e não protege contra quem escreve no host. Slice 1 incompleta.

## Próxima tarefa

Implementar/testar fechamento e exportação offline conforme o [contrato](openspec/changes/slice1-evidence-storage/proposal.md); preparar AT independentes de transporte e regressões completas antes de publicar7. [Resultado dos gates/saídas](openspec/changes/slice1-evidence-storage/integration-verification.md). Depois retomar v3/unblock. Não integrar agentes.

## Verificação

- Complementares: `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests -v`.
- Isolamento: `PYTHONDONTWRITEBYTECODE=1 python3 bootstrap/slice1/integrate_acceptance.py`.
- [Contrato](openspec/changes/slice1-block-run/proposal.md), [resultado e reprodução](openspec/changes/slice1-block-run/block-verification.md), [resumo verificável](openspec/changes/slice1-block-run/block-verification.json).
- [AT v5](bootstrap/slice1/evidence/block-candidate.json) e [revisão](openspec/changes/slice1-block-run/block-review.json).
- Preparo atual: 39 complementares PASS; baseline novo 16 métodos (13 falhas funcionais esperadas, três PASS), 150 chamadas sem infraestrutura/errors/skips. Selo v6 `7196e5f4` publicado/verificado, revisão PASS. [Verificação](openspec/changes/slice1-unblock-run/verification.md). Não demonstra desbloqueio funcionando.
