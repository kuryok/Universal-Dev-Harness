# Alinhamento após auditoria — 27/10/2026

Autorização: o usuário delegou a escolha de A1/A2 e solicitou corrigir/alinhar as inconsistências segundo o propósito do projeto. A proibição anterior de commit continua vigente. O diagnóstico externo permanece diagnóstico; nenhuma recomendação de agente é requisito automático.

## Decisões e limites

- **A1/A2:** restaurar o baseline rastreado exatamente como HEAD, mantendo seção0, matrizes e proveniência. Não promover a v2.5.1 incompleta; preservá-la no mesmo caminho como nota editorial derivada, sem precedência normativa. Não ratificar automaticamente ADR-018 A/B1–B4 pendentes. Sem tag, remote ou publicação presumidos.
- **A3/A4/A10:** manter a curadoria, precedência temporal e ignore já aplicados. README mínimo aponta para o baseline e para o estado corrente, sem instalador/empacotamento novo.
- **A5:** implementar a proposta compatível antes pendente: novos resumos schema2 declaram failed_methods (lista conhecida, ou null quando indisponível) e counts.failure_entries; errors/skipped conservam suas unidades. Leitura schema1 usa exatamente sua receita antiga. Resultado, outcomes, objetos e bytes históricos não mudam; métricas não aprovam tarefas.
- **A6/A9:** encerrar a expansão funcional do bootstrap por ora; somente correções de inconsistência/compatibilidade são justificadas neste recorte. Próximo marco é migração/leitura v3 e depois unblock, já aprovados. Exportação/AT de transporte/selo7 ficam pendentes sem relaxar condições de publicação. Não inventar prazo de calendário: o limite operacional é não adicionar funcionalidades de bootstrap antes de verificar esse marco do Core. Não remover ou fundir gates nesta correção.
- **A7:** registrar explicitamente o uso documental adaptado de OpenSpec em project.md, sem alegar estrutura canônica/validação automática nem instalar CLI/dependência. Baseline define propósito/invariantes; changes aprovados especificam os incrementos; STATE/MEMORY somente orientam retomada.
- **A8:** declarar provas parciais como bootstrap, sem segurança oficial, sem equivalência às POCs NOT_RUN e sem waiver. Não executar POCs completas nem mover store/abrir credenciais/limpar workspace sensível nesta correção.

## Arquivos e verificação

Permitidos neste alinhamento: restauração do baseline; rótulo/metadados de precedência da nota v2.5.1 preservando seu corpo técnico; AGENTS/README/project.md/STATE/MEMORY; helper evidence_store.py e complementares tests/test_evidence_storage.py para A5; seleção explícita da revisão atual em check_boundary.py e novo parecer versionado, sem sobrescrever o histórico; documentação deste change. Manter as correções documentais e ignore da auditoria. Não alterar Core, AT, selos, evidências históricas, revisões antigas, CI ou dependências.

Pronto: fonte normativa inequívoca; termos/documentos coerentes; escrita nova schema2 e leitura histórica schema1 com testes das unidades16/13/28, ausência de nomes, versões inválidas, métricas adulteradas e preservação de bytes. Preservar os85 complementares existentes, sem flexibilizá-los. Conferir links, limites e diff --check. Nenhuma coleta nova com helper medido por revisão antiga: antes dela será necessária revisão atual dos hashes, sem sobrescrever o parecer histórico.

## Matriz do propósito preservado

| Área do baseline | Estado factual após alinhamento |
| --- | --- |
| Interface guiada e iniciante | Permanece no propósito; não implementada. |
| SDLC/OpenSpec/tickets | Core parcial e changes documentais; ciclo completo/canonicalização pendentes. |
| Agentes intercambiáveis/handoff | Sem executor de produto; spike histórico não equivale a conformance. |
| Skills/MCPs mínimos | Requisito preservado; não instalados/ativados por esta correção. |
| Memória semântica | Requisito preservado; STATE/MEMORY são retomada documental, não Memorix. |
| Loop/testes/Judge | Complementares e bootstrap parciais; Judge não implementado e permanece consultivo. |
| Segurança/recuperação | SQLite parcial e provas sintéticas; POCs de produto NOT_RUN, sem waiver. |
| Entrega funcional explicável | Ainda não demonstrada; nenhum aceite final é inferido. |

O baseline foi restaurado byte a byte, sem atualizar seus estados históricos para PASS. Esta matriz dá contexto corrente sem substituir o texto normativo nem promover a nota editorial. Remotes/tags não são criados sem commit autorizado/destino definido; ausência de remote não foi convertida em afirmação de ausência de todo backup.

## Resolução da auditoria

A1/A2 resolvidos pela restauração exata e precedência explícita, com nota derivada preservada. A3/A4/A10 mantêm curadoria, rótulos históricos e ignore; README melhora a entrada sem instalador. A5 implementa métricas versionadas. A6/A9 resolvem a prioridade operacional limitando expansão e retomando o Core. A7 explicita a adaptação documental, sem alegar formalização concluída. A8 corrige o alcance das alegações, não os resultados de POCs ainda não executadas. Features pendentes não são marcadas como bugs corrigidos ou evidência de produto concluído.

Verificação deste alinhamento será registrada após os testes finais. Nenhum commit, stage, novo selo, credencial, dependência, limpeza de dados sensíveis ou execução de agentes é autorizado por inferência.

## Verificação executada

- Seis novos complementares de métricas foram escritos antes da correção e falharam com a implementação antiga; depois passaram. O ensaio identificou também newline faltante na fixture dos resumos; a fixture foi corrigida para exigir exatamente os bytes canônicos originais, sem afrouxar o leitor.
- Rodada completa:91 testes PASS em8,718s, preservando os85 anteriores e acrescentando seis. Resumo2 usa16 métodos/13 identificadores/28 entradas no caso de subtestes; leitor1 recompõe o resumo histórico byte a byte e não regrava objetos.
- [Revisão independente versionada](infrastructure-review-2026-10-27.json): PASS, seis novos testes executados pelo revisor, mapa exato14 componentes; parecer anterior intacto. O seletor de revisão foi atualizado explicitamente para não reutilizar hashes antigos com código novo.
- Sem novo selo, coleta I/T/AT/POC, dependência, configuração de engine/CI ou credencial. Apenas complementares e verificações de integridade/links/limites; não há claim de segurança oficial.

Conferência final: baseline idêntico ao blob do HEAD; prefixo completo dos85 testes antigos preservado; links locais, limites STATE/MEMORY, sintaxe, hashes da revisão atual e git diff --check PASS. Core, AT, selos/objetos/resumos e revisões históricas intactos. Índice sem alterações e nenhum commit. A arquitetura derivada continua visível como arquivo não rastreado, sem ser tratada como normativa; relatorio.md não foi reescrito.
