# Contexto e processo documental do UDH

## Autoridade e escopo

O [baseline normativo rastreado](../Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md) preserva o propósito, invariantes e proveniência. A nota v2.5.1 é derivada para consulta, sem substituí-lo. Aprovações pendentes no baseline continuam pendentes; pareceres de LLM não as ratificam.

O uso de OpenSpec neste repositório é **documental adaptado**: requirements e planos dos incrementos estão em changes aprovados. Não há CLI instalada nem alegação de conformidade com estrutura/validação automática canônica. Este project.md registra o contexto; não declara specs/tasks canônicos criados ou migração para uma ferramenta. Formalização futura não deve reescrever requisitos ou histórico silenciosamente.

Antes de implementar: confirmar escopo/arquivos/critério de pronto no change, registrar aprovação humana e verificar o incremento. Mudança de requisito exige delta explícito. Não duplicar requisitos em README, STATE, MEMORY ou diagnósticos externos.

## Engenharia e prioridade atual

Um Core determinístico decide o estado; SQLite guarda operação; Git guarda código; evidências e testes oficiais têm requisitos próprios de proteção. Componentes permanecem separados por responsabilidade.

Após as correções da auditoria, o próximo marco é migração/leitura v3 e depois desbloqueio administrativo. O bootstrap não recebe novas funcionalidades até a verificação desse marco; correções de inconsistência/compatibilidade devem ter justificativa e testes. Exportação, AT de transporte e selo7 permanecem pendentes; nenhuma condição de publicação é dispensada. Baixo overhead é parte do propósito, não justificativa para enfraquecer aceite.

## Estado, segurança e retomada

Consultar [STATE.md](../STATE.md) e [MEMORY.md](../MEMORY.md) para o estado corrente. O baseline é um snapshot de desenho; seus estados históricos não são evidência de execução atual.

POC-01–04 seguem NOT_RUN. Bootstrap e spike não certificam isolamento/segurança do produto, store oficial protegida ou entrega. Não abrir credenciais reais nem alterar o workspace sensível para “limpar” o diagnóstico. Não presumir backup remoto: nenhuma origem foi configurada; isso não prova inexistência de outras cópias.

[Alinhamento autorizado](changes/slice1-evidence-storage/alignment.md) e [auditoria reproduzida](changes/slice1-evidence-storage/report-audit.md) registram decisões e limites, sem substituir os contratos funcionais aprovados.
