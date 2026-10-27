# Auditoria do diagnóstico — 27/10/2026

> **Registro da primeira passagem da auditoria.** As restrições e pendências abaixo descrevem o pedido inicial. Depois, o usuário delegou a decisão de A1/A2 e o alinhamento; a resolução vigente está em [alignment.md](alignment.md). Não interpretar a parada ou a proposta A5 deste registro como estado atual. O diagnóstico e a reprodução originais foram preservados.

Base conferida: `e6f2355`, mesmo checkout indicado por relatorio.md. O relatório externo foi tratado como diagnóstico, não aprovação ou requisito. A solicitação humana desta sessão autoriza somente as correções restritas A3/A4/A10 e eventual A5 sem mudança de schema; proíbe commit e alterações normativas silenciosas. Esta reprodução pelo implementador não é nova certificação independente de produto.

## Reprodução antes das edições

| ID | Classificação | Evidência reproduzida e limite |
| --- | --- | --- |
| A1 | Requer decisão humana | Baseline rastreado ausente no worktree; arquitetura local não rastreada. `git tag -l` e `git remote` vazios. Isso confirma ausência de remote configurado, não ausência de qualquer backup externo. |
| A2 | Requer decisão humana | `git show HEAD:Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md`:609 linhas/69.392 bytes, seção0/matriz e regra de preservação. V2.5.1 local:377 linhas/48.194 bytes, sem seção0/matriz e com redação “sem histórico de ratificação”. Nenhuma autoridade substituta foi escolhida. |
| A3 | Confirmado | STATE/MEMORY apresentavam36/39/85 complementares e referências a selos de fases distintas sem rótulo suficiente de histórico. |
| A4 | Confirmado | CLI antiga `integrate_block.py --baseline`: exit2, stdout vazio, inventário de evidências/temporários intacto. Parser atual requer seleção explícita7; docs anteriores ainda recomendavam a chamada antiga e descreviam fase anterior ao codec. |
| A5 | Confirmado | `read_run` validou a coleta78a7d7e9-0fb5-4170-b8e4-827f6e497fbd e seu fechamento:16 métodos,13 outcomes FAIL/3 PASS,13 identificadores em failed_methods e28 registros em failures. Summary schema1 conta registros, não métodos. |
| A6 | Confirmado quanto à dimensão | Contagem de `.py`:15 arquivos/3.404 linhas diretamente em bootstrap/slice1;9 módulos/704 linhas em udh. Relação aproximada4,84. Tamanho não demonstra por si só segunda autoridade nem autoriza remover gates. |
| A7 | Confirmado quanto à estrutura |50 arquivos em openspec/changes; ausentes specs/, project.md, tasks.md e CLI openspec. Os change docs continuam úteis; a ausência da CLI não revoga sua autoridade definida pelo projeto. |
| A8 | Confirmado no alcance declarado | POC-01–04 constam NOT_RUN nos documentos; store reside no projeto e o modelo não protege contra proprietário do host. Metadados do workspace ignorado:449 arquivos/57.460.646 bytes (54,8 MiB),11 nomes auth.json e8 arquivos .db/.log. Nenhum desses conteúdos foi aberto. Não houve nova varredura de segredos nem execução das POCs. |
| A9 | Confirmado quanto à prioridade/timebox | Os quatro commits mais recentes tratam evidências; Core ainda tem apenas migração2/block. O STATE já possuía uma única próxima tarefa e ordem evidências→v3/unblock: não se confirmou contradição de ordem nem falta absoluta de prioridade escrita. Falta timebox aprovado. |
| A10 | Confirmado parcialmente | Sem .gitignore raiz, com três diretórios de cache não rastreados; nenhum bytecode no índice. README/empacotamento/harness ausentes, mas criá-los não pertence à higiene autorizada. |

Comandos usados: status/log/tag/remote/ls-files/check-ignore do Git; contagens com pathlib/AST e leitura seletiva dos fontes; CLI antiga com captura de saída e inventário antes/depois; read_run somente leitura. rg não está instalado; usaram-se alternativas locais sem instalar dependências. Os85 complementares passaram antes das edições em9,042s.

## Correções autorizadas, separadas logicamente

- **A3:** STATE e MEMORY agora contêm uma única contagem corrente de complementares, uma indicação consolidada dos selos e uma próxima decisão. Resultados antigos continuam nos change docs; a data foi atualizada. Nenhuma coleta antiga foi declarada executada novamente.
- **A4:** rótulos históricos em slice1-unblock-run/verification.md, verification.md e codec-verification.md deste change. integration-verification.md identificado como referência posterior da implementação, com suas execuções datadas. A chamada antiga foi explicitamente desativada no procedimento; o comando atual inclui `--generation 7`, grava somente execução nova e pode retornar INCOMPLETE/exit1. A distinção entre métodos e entradas foi esclarecida no texto, sem alterar resumos ou resultados.
- **A10:** .gitignore raiz limita-se a __pycache__/, *.pyc e *.pyo. Nenhum cache foi apagado ou retirado do índice, pois não há bytecode rastreado. Documentos, relatorio.md e evidências continuam visíveis ao Git. Sem README, instalador, configuração de engine ou dependência nova.

Nenhum git add/commit foi executado. Baseline/arquitetura/relatorio.md e caches preexistentes foram preservados. Não foram alterados Core, coletores, testes, selos, objetos, resumos, revisões ou CI.

## A5 — proposta pendente, não implementada

Não há erro aritmético a corrigir:28 entradas incluem subtestes, enquanto13 são métodos distintos com FAIL. `_summary` calcula len(failures); `read_run` exige exatamente os campos e a recomposição do schema1. Inserir campos extras ou renomear counts.failures mudaria esse contrato e poderia invalidar leituras preservadas. O resumo em disco permanece intacto. Uma simulação somente em memória ampliou os campos esperados por _summary: o leitor rejeitou o resumo1 previamente validado, confirmando a incompatibilidade sem escrever arquivos.

Delta proposto para aprovação neste mesmo change: emitir **schema2** somente para resumos novos; preservar leitura/recomposição schema1 e todos os bytes antigos. Representar tests_run=16, failed_methods como lista dos13 identificadores disponíveis na coleta, e counts.failure_entries=28; manter errors/skipped com suas unidades documentadas. Quando os nomes não existirem na coleta, usar failed_methods=null, sem inferi-los de logs. Result, outcomes e evidências não mudam. Nenhuma aprovação de execução pode ser inferida desses contadores.

Após aprovação: implementar ramificação explícita por versão e testar16/13/28 com subtestes, schema1 byte a byte, versões desconhecidas/bool rejeitadas, métricas forjadas recusadas e ausência de alteração dos objetos/selos. Como o helper está medido pela revisão de infraestrutura, a atualização também exigirá revisão dos hashes e gates aplicáveis antes de qualquer coleta nova. Nenhuma revisão antiga será reescrita para acomodar a mudança. Esta sessão apenas confirmou o comportamento vigente; não criou teste para uma alteração ainda não aprovada.

## A6–A9 — planos somente, sujeitos a aprovação

| ID | Proposta para decisão, sem execução |
| --- | --- |
| A6 | Inventariar responsabilidade, tamanho e custo dos gates; propor orçamento de complexidade e justificativa por crescimento. Identificar redundância antes de propor consolidação, preservando critérios/AT. Limites e remoções não são decididos por esta auditoria. |
| A7 | Escolher formalização OpenSpec canônica ou adaptação documental explicitamente ratificada; mapear requisitos/ADRs/change docs antes de qualquer movimentação. Instalação de CLI/dependência e reorganização exigem aprovação; não renomear a autoridade unilateralmente. |
| A8 | Definir recorte aprovado para POCs com dados sintéticos e executor confiável; registrar ameaça e critérios antes de executar. Separar prova parcial do bootstrap de segurança oficial. Qualquer isolamento/migração da store, acesso fora do projeto ou limpeza do workspace sensível precisa de aprovação específica. Não abrir credenciais reais. |
| A9 | Decidir se encerra transporte/exportação antes de v3/unblock ou retoma o Core preservando o transporte como INCOMPLETE. Aprovar marco/timebox explícito, sem inventar data ou prazo nesta sessão; só então alinhar a próxima tarefa nos documentos. |

## A1/A2 — opções e parada

1. **Manter a fonte rastreada:** após autorização, restaurar exatamente o baseline do HEAD e classificar a v2.5.1 como rascunho/derivada, com proveniência explícita. Sem promovê-la silenciosamente a normativa.
2. **Preparar substituição formal:** após autorização, comparar os dois textos, preservar/reintegrar seção0, matriz e registros de ratificação, submeter o delta e somente então autorizar substituição/commit normativo separado. Tag e backup/remote dependem de decisão própria; não configurar rede ou destino por suposição.

Até essa escolha, nenhum dos dois arquivos será restaurado, apagado, movido ou substituído. A auditoria termina com essa decisão pendente; não inicia exportação, unblock ou planos A6–A9.

## Verificação final desta sessão

- Comando solicitado, antes e depois das correções: `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests`. Resultado inicial:85 PASS/9,042s; final:85 PASS/8,466s, zero failures/errors/skips. Testes existentes preservados, sem novos AT ou testes de comportamento não aprovado.
- Links locais, limites de STATE/MEMORY, contagem atual única, regras de ignore e git diff --check: PASS. Índice sem alterações; nenhum commit.
- Comparação com HEAD: Core, testes, bootstrap inteiro (incluindo todos os selos/objetos/resumos), CI e configurações de harness intactos. A deleção do baseline e os arquivos locais v2.5.1/relatorio.md são preexistentes e não foram incorporados nem ocultados pelo ignore.
- AT reais, POCs e gates completos de publicação: **não executados**; nenhuma mudança de código ou schema de evidência foi feita. A conferência read_run validou somente a coleta histórica e seu fechamento; não equivale a reexecutá-la. Harness fast/full e CLI OpenSpec ausentes: **não executados**.
- A1/A2 permanecem para decisão humana. A5 é proposta de schema, A6–A9 são planos; nenhum deles foi implementado.
