# UDH — retomada curta

Atualizado: 27/10/2026. Ler [AGENTS.md](AGENTS.md), [STATE.md](STATE.md) e conferir Git. Memória não aprova requisitos nem substitui evidências.

## Estado confirmado
- Usuário delegou A1/A2: baseline do HEAD restaurado byte a byte como fonte normativa; v2.5.1 preservada e rotulada nota editorial derivada. Nada foi ratificado silenciosamente.
- [Alinhamento A1–A10](openspec/changes/slice1-evidence-storage/alignment.md): docs/ignore curados; métricas novas schema2 distinguem métodos/entradas, mantendo schema1 e histórico intactos. OpenSpec documental adaptado explícito em project.md.
- 91 complementares PASS na reanálise de 27/10; testes anteriores preservados. Selos v1–v6 publicados, v7 pendente; nenhum novo aceite, coleta I/T ou POC neste alinhamento.
- Core SQLite v1/v2 e bloqueio administrativo implementados. v3/unblock aprovados, ainda pendentes; execução de agentes indisponível.

## Próximo passo
- Implementar migração/leitura v3 e depois unblock conforme change aprovado, verificando compatibilidade. Bootstrap limitado a correções até esse marco; exportação/transporte pendentes, sem dispensa dos critérios de publicação.
- [Reanálise](openspec/changes/slice1-unblock-run/next-step-review.md): consolidar checkpoint autorizado e usar SHA novo nos AT; --baseline continua controle negativo fixo. Sem commit autorizado; mudanças continuam no worktree. Não configurar remote/tag sem destino e autorização; ausência de remote não prova ausência de todo backup.

## Cuidados
- Protótipo v7 tem mínimo complementar histórico insuficiente para comprovar suíte corrente; revisar antes de publicar. Nova revisão de infraestrutura acompanha o helper; parecer histórico preservado. Segurança de produto/POCs continuam NOT_RUN; store local não protege contra proprietário do host.
- v2.5.1, relatorio.md e novos documentos permanecem não rastreados até commit autorizado. Relatório externo é diagnóstico histórico, não fonte de requisitos.
- Não abrir credenciais nem limpar dados do spike. Preferências: sem skills globais; commits em português quando autorizados; informar próximo passo.
