# Relatório de análise do Universal Dev Harness (UDH)

**Data da análise:** 27/10/2026  
**Repositório:** `/home/debian/Área de trabalho/Universal Dev Harness`  
**Commit analisado:** `e6f2355` (`main`)  
**Natureza:** diagnóstico independente. Este documento **não é** especificação, aprovação, aceite, plano de execução ou fonte de requisitos.  
**Método:** leitura estática de código e documentos, inspeção do Git, testes complementares e conferência de relatórios de evidência.  
**Limite:** os AT de bootstrap, as POCs de segurança e os gates completos **não foram reexecutados** nesta análise. Os arquivos `auth.json` ignorados não foram abertos.

---

## 1. Sumário executivo

O UDH tem uma tese coerente e ambiciosa: ser um **harness de processo local-first** que leva uma pessoa que não programa de uma ideia a software funcional, testado e utilizável, usando agentes intercambiáveis sob um Core determinístico, com requisitos em OpenSpec, estado em SQLite, código em Git e evidências independentes.

O que existe hoje é uma **fundação promissora**, não o produto prometido. O Core transacional, o bloqueio administrativo, a migração SQLite e a disciplina de evidências são sólidos no recorte atual. O produto ainda não tem executor de agentes, interface guiada, OpenSpec formal, Memorix, Judge, instalador ou entrega ponta a ponta; as POCs de segurança seguem `NOT_RUN`.

O maior risco não é um bug no Core. É a **fonte de verdade**: o baseline normativo antigo foi removido da árvore de trabalho, a arquitetura v2.5.1 está fora do Git, não há remote/tags, e `STATE.md`/`MEMORY.md` acumulam contagens e estados diferentes. O segundo maior risco é a **assimetria de complexidade**: o bootstrap de verificação já tem cerca de cinco vezes o tamanho do Core.

**Veredito:** sólido no método, frágil na fonte de verdade, incompleto como produto. Não certificaria o UDH como pronto; aprovaria continuar somente depois de fechar a governança documental e timeboxar o aparato de evidências.

---

## 2. Ideia central e objetivos

O UDH não é uma IDE nem apenas um gerador de código. É um sistema que conduz o ciclo:

`DISCOVER → SPEC → PLAN → EXECUTE → VERIFY → REVIEW → DELIVER`

com checkpoints, memória, autorização proporcional ao risco e evidências independentes.

O ativo central é a combinação de:

- **Core determinístico** com autoridade única sobre estados, autorizações e gates;
- **agentes intercambiáveis** (Pi, OpenCode, Codex) sem perda de estado;
- **OpenSpec** como fonte de requisitos;
- **SQLite** como estado operacional;
- **Git** como fonte do código;
- **evidências** coletadas fora do alcance do agente;
- **experiência guiada** para iniciantes, com decisões e bloqueios explicáveis;
- **entrega** executável, testada, documentada e retomável.

O baseline antigo (§0) define dez funcionalidades que “não podem desaparecer do roadmap” e exige que toda revisão contenha a seção 0 e uma **Matriz de Cobertura do Propósito** atualizada. Essa é uma regra de governança relevante para a análise do item A2.

A tese é forte. O ponto de atenção é não confundir a fundação técnica com a realização do propósito: o Core atual governa estado e bloqueio, mas ainda não governa o ciclo de desenvolvimento completo.

---

## 3. Estado atual verificado

### 3.1 O que já existe

| Área | Estado | Evidência |
|---|---|---|
| Slice 0 | Encerrada como experimento Linux/API; produto não certificado | `MEMORY.md`, `SPIKE-SLICE0-RESULTADOS.md` |
| Pi/OpenCode | Resposta real via OpenRouter no spike; chave no gateway externo | `MEMORY.md` |
| Codex | Não encontrei evidência de execução real no estado resumido | `MEMORY.md`; ausência de menção |
| Core | 9 módulos, 704 linhas | `udh/` |
| SQLite | v1/v2, init/create/show/policy, migração explícita | `udh/store.py`, `udh/migrations.py` |
| Bloqueio | PENDING→BLOCKED atômico, journal, replay, autorização, revisão otimista | `ad2bd47`, `STATE.md` |
| Policy | Consulta somente leitura; não concede execução/transição/aceite/entrega | `udh/policy.py` |
| Testes | 85 complementares PASS | execução desta análise |
| AT | Relatórios de migração, bloqueio, policy e v1; 171 chamadas auditadas, 15 checkpoints | `STATE.md`, relatórios em `openspec/changes/` |
| Unblock | Proposta aprovada; DDL v3 refinado; **não implementado no Core** | `openspec/changes/slice1-unblock-run/` |
| Evidências | Codec, gzip, objetos por hash, publicação atômica, gates | `bootstrap/slice1/evidence_store.py`, `e6f2355` |
| Selos | v1–v6 preservados; v7 pendente | documentos de evidência |
| POCs de segurança | `NOT_RUN` | baseline antigo e arquitetura v2.5.1 |

### 3.2 O que ainda não existe

- Executor real de agentes e troca de agente com preservação de estado.
- Interface guiada de descoberta, explicação e bloqueios.
- `openspec/specs`, `project.md`, `tasks.md` e CLI OpenSpec.
- Memorix, Judge consultivo, instalador e entrega ponta a ponta.
- POC-01–04 executadas.
- Aceite oficial do produto e instalação operacional protegida.
- Store de evidências fora do domínio gravável pelo host.

### 3.3 Números atuais

- **85 testes complementares PASS** em 13,069 s, com `PYTHONDONTWRITEBYTECODE=1`.
- Core: **704 linhas** em **9 módulos**.
- Bootstrap: **3.404 linhas** em **15 arquivos `.py`**.
- Relatórios: **13 cenários/AT de migração/bloqueio**, **10 AT v1**, **11 AT policy** (unidades declaradas nos documentos; não reexecutei).
- Evidência: baseline com **16 métodos**, **13 métodos com falha esperada**, **3 PASS**, **150 chamadas** sem infraestrutura.
- O `summary.json` da coleta `78a7d7e9-0fb5-4170-b8e4-827f6e497fbd` registra `counts.failures=28`; a diferença para 13 é explicada no item A5.

---

## 4. O que está sólido

1. **Atomicidade e recuperação.** O bloqueio não é um `UPDATE` solto: há evento, pedido, autorização e estado na mesma transação, com journal, replay e tratamento de concorrência. Isso é acima da média de um protótipo.

2. **Migração e compatibilidade.** A passagem v1→v2 é explícita, a leitura v1 é preservada e a policy não escreve. O desenho evita “migração silenciosa” e mantém os formatos antigos verificáveis.

3. **Disciplina de testes.** Os 85 complementares passam; os relatórios separam testes complementares de AT; há revisões independentes; os limites e resultados `INCOMPLETE` são declarados. Não vi enfraquecimento de testes de aceite para fazer o resultado passar.

4. **Evidência por referência.** O codec usa JSON canônico, gzip, hash, objetos imutáveis, referências e limites de tamanho. A publicação é atômica. O sistema não afirma que hash prova verdade; afirma que prova bytes. Essa distinção é correta.

5. **Honestidade de estado.** O resultado geral permanece `INCOMPLETE`; `integrate_block.py` retorna `exit1` de propósito até regressões, exportação e selo7. O projeto não está fingindo que a Slice 1 acabou.

6. **Higiene básica.** Não encontrei padrões de segredo em arquivos rastreados; os links de `STATE.md`/`MEMORY.md` resolvem; os limites de linhas/palavras estão dentro do combinado.

A melhor qualidade do projeto é o hábito de não declarar sucesso sem evidência. Esse hábito deve ser preservado quando a pressão por entregar aumentar.

---

## 5. Colisões estruturais

### C1. Um Core vs. um bootstrap de 3.404 linhas

O Core tem 704 linhas. O bootstrap tem 15 arquivos e 3.404 linhas, aproximadamente **5×** o Core.

Principais arquivos:

- `bootstrap/slice1/block_seal.py`: 449 linhas
- `bootstrap/slice1/evidence_store.py`: 456 linhas
- `bootstrap/slice1/operator_process.py`: 370 linhas
- `bootstrap/slice1/integrate_acceptance.py`: 331 linhas
- `bootstrap/slice1/check_boundary.py`: 330 linhas

Os documentos chamam o bootstrap de transitório e negam que ele seja um segundo orquestrador. Tecnicamente, ele não decide estados de produto; mas, em tamanho, contratos, gates, selos e revisões, já funciona como um **segundo sistema**. Cada novo gate aumenta o custo de descartá-lo. O risco é o projeto passar a manter o aparato de verificação mais seriamente do que o produto.

**Direção sugerida:** congelar o crescimento do bootstrap, definir um orçamento de complexidade e remover/mergear gates antes de adicionar novos.

### C2. POCs de segurança `NOT_RUN` vs. avanço da fundação

A arquitetura trata as POCs como gates. O Core e a infraestrutura de evidências avançaram sem elas. Isso pode ser aceitável como trabalho de fundação, mas precisa de uma decisão explícita: “segurança ainda não provada” não é o mesmo que “segurança por padrão”.

**Direção sugerida:** registrar uma dispensa temporária e explícita, ou executar POC-01/02/04 antes de qualquer alegação de segurança do produto.

### C3. Propósito de produto vs. recorte implementado

O produto exige agentes reais, OpenSpec, UI, Memorix, Judge e entrega. O recorte atual tem estado, bloqueio, policy e evidências. É uma base legítima, mas ainda não é o ciclo de desenvolvimento prometido.

**Direção sugerida:** definir o próximo marco vertical de produto — por exemplo, adaptador de agente + uma fatia mínima de UI + entrega de um app pequeno — para evitar que a fundação se torne um fim em si.

### C4. Unblock aprovado vs. foco em evidências

Os últimos quatro commits são de armazenamento de evidências:

- `166b5a1` especifica armazenamento
- `4abb116` revisa limites
- `9747b6f` implementa codec
- `e6f2355` integra evidências por referência

O unblock continua pendente. A proposta de evidências diz explicitamente que o recorte não é dependência funcional do unblock e que a ordem podia ser alterada. Portanto, **não é um desvio indevido**; é uma escolha de prioridade. Mas o “próximo passo” real virou exportação/regressões, não o desbloqueio.

**Direção sugerida:** timeboxar o recorte de evidências e retomar v3/unblock em seguida, ou registrar formalmente que o foco mudou.

### C5. OpenSpec como autoridade vs. OpenSpec nominal

Existem 50 arquivos em `openspec/changes`, mas:

- não há `openspec/specs`;
- não há `openspec/project.md`;
- não há `tasks.md` no padrão OpenSpec;
- a CLI `openspec` não está instalada;
- os change docs usam cabeçalhos próprios em português.

Os documentos são úteis e detalhados, mas “OpenSpec é a fonte dos requisitos” ainda é uma intenção arquitetural, não uma propriedade verificável do repositório.

**Direção sugerida:** formalizar a estrutura/CLI ou assumir e documentar que o processo atual é “change docs próprio”, sem chamá-lo de OpenSpec.

### C6. Evidência em domínio confiável vs. store dentro do projeto

`evidence_store.py` é cuidadoso contra escrita parcial e concorrência, mas fica dentro do repositório e não protege contra quem tem escrita no host. O proposal reconhece isso. É armazenamento de bootstrap, não a store protegida prometida pela ADR-016.

**Direção sugerida:** mover a evidência oficial para fora do workspace gravável antes de alegar cumprimento da ADR-016; até lá, rotular o store como “evidência de bootstrap”.

### C7. Interface “depois” vs. interface inegociável

É compatível dizer que a interface vem depois da fundação. Mas o propósito do produto depende dela. Sem um marco concreto, “depois” pode virar “nunca”.

**Direção sugerida:** incluir a interface em um marco de produto datado, mesmo que mínimo.

---

## 6. Achados e inconsistências

### A1 — Fonte normativa dividida e sem backup remoto (Alta)

**Evidência:**

- `git status --short` mostra:
  - `D Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md`
  - `?? UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md`
  - `?? bootstrap/slice1/__pycache__/`
  - `?? tests/__pycache__/`
  - `?? udh/__pycache__/`
- O baseline antigo tem **609 linhas / 69.392 bytes**; a v2.5.1 tem **377 linhas / 48.194 bytes**.
- `git tag -l` e `git remote -v` não retornam nada.
- O baseline antigo ainda está no HEAD e pode ser recuperado com `git show HEAD:Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md`.

**Impacto:** o HEAD e o worktree discordam sobre o documento normativo. A v2.5.1 existe apenas no disco local; não há tag, remote ou aprovação registrada. Um `git add -A` ingênuo apagaria o baseline antigo, adicionaria a v2.5.1 e os `__pycache__` no mesmo commit.

**Correção sugerida:** não rodar `git add -A` nem `git commit -a` antes de decidir. Escolher explicitamente uma das rotas:

1. **Manter o baseline antigo como normativo:** restaurar o arquivo deletado, manter a v2.5.1 como rascunho em `docs/` ou change doc e não tratá-la como substituta.
2. **Substituir formalmente:** aprovar a v2.5.1, restaurar nela a seção 0 e a matriz, commitar a remoção e a adição em um commit explícito, criar uma tag e registrar a decisão.

**Critério de verificação:** `git status` limpo para os arquivos normativos; um único documento normativo no HEAD; tag criada; nenhuma remoção silenciosa.

---

### A2 — A v2.5.1 não cumpre a regra de governança do baseline antigo (Alta)

**Evidência:**

- O baseline antigo, §0, linha 48, diz: “toda revisão deve conter esta seção 0 e uma **Matriz de Cobertura do Propósito** atualizada”.
- A v2.5.1 se declara “revisão editorial e operacional pontual da arquitetura v2.5”.
- A v2.5.1 **não contém** a seção 0 nem a matriz; ela resume o produto em §1.
- A v2.5.1 também diz que as ADRs são apresentadas “sem histórico de ratificação”, removendo a proveniência que o baseline antigo registrava.

**Impacto:** a substituição pode preservar a substância, mas quebra a regra formal do próprio baseline. O propósito inegociável deixa de ter a proteção documental que ele mesmo exigia.

**Correção sugerida:** se a v2.5.1 for o novo normativo, reinserir a seção 0, a matriz atualizada e a ratificação/histórico de aprovação. Se for apenas uma consolidação editorial, marcá-la como derivada e manter o baseline antigo como autoridade.

**Critério de verificação:** o documento normativo contém a seção 0 e a matriz; a decisão de substituição está registrada; a proveniência não foi apagada.

---

### A3 — `STATE.md` e `MEMORY.md` acumulam estados obsoletos (Alta)

**Evidência:**

- `STATE.md:13`: **36** complementares.
- `STATE.md:20`: **85** complementares.
- `STATE.md:34`: **39** complementares.
- `MEMORY.md:10`: **36** complementares.
- `MEMORY.md:23`: **85** complementares.
- Selos: `STATE.md:15` diz `v1–v5`; `STATE.md:34` diz `v6`; `STATE.md:20` diz `1–6`.

Os números correspondem a fases reais — 36 do selo v5, 39 do preparo, 85 atual —, mas não estão rotulados como históricos. O mesmo vale para os selos: o estado atual é v1–v6, com v7 pendente.

**Impacto:** o mecanismo de retomada, que existe para evitar confusão, está produzindo confusão. Um agente que lê `STATE.md` pode começar com a contagem errada ou acreditar que o selo v6 não existe.

**Correção sugerida:** reescrever `STATE.md` e `MEMORY.md` com um único valor atual (85), um único estado de selo (v1–v6; v7 pendente) e uma única próxima tarefa. Mover 36/39 e verificações antigas para os change docs/verification docs. Atualizar a data.

**Critério de verificação:** cada métrica aparece uma vez no estado atual; números históricos estão fora de `STATE.md`/`MEMORY.md`; links resolvem; limites de linhas/palavras continuam respeitados.

---

### A4 — Documentos de verificação se contradizem sem rótulo de precedência (Média)

**Evidência:**

- `openspec/changes/slice1-unblock-run/verification.md:27` ainda recomenda:
  `python3 bootstrap/slice1/integrate_block.py --baseline`
- O parser atual exige `--generation 7` (`bootstrap/slice1/integrate_block.py:133-139`).
- O comando antigo deve sair com exit2 e zero escrita (`openspec/changes/slice1-evidence-storage/proposal.md:63`).
- O fluxo novo é `--baseline --generation 7` (`openspec/changes/slice1-evidence-storage/integration-verification.md:35`).
- `openspec/changes/slice1-evidence-storage/verification.md` descreve o estado anterior e diz que armazenamento/compressão/gates não estavam implementados; `e6f2355` os implementa, exceto selo7.

**Impacto:** quem seguir o link “verificação” pode executar um comando que falha ou concluir que o codec não existe.

**Correção sugerida:** marcar claramente os documentos históricos; apontar `integration-verification.md` como corrente; corrigir o comando antigo para o fluxo atual ou rotulá-lo como histórico.

**Critério de verificação:** nenhum comando antigo sem aviso; o link de `STATE.md` aponta para o documento corrente; a diferença “histórico vs. atual” está explícita.

---

### A5 — Métrica de falhas ambígua: 28 vs. 13 (Média/Baixa)

**Evidência:**

- `bootstrap/slice1/evidence/runs/78a7d7e9-0fb5-4170-b8e4-827f6e497fbd/summary.json` registra:
  - `tests_run: 16`
  - `counts.failures: 28`
- `openspec/changes/slice1-evidence-storage/integration-verification.md:29` fala em **13 falhas funcionais esperadas**.
- O relatório tem 16 métodos, 13 métodos com falha e 3 PASS; o resumo conta as **entradas da lista `failures`** (28), que incluem subtestes/entradas, mas não expõe `failed_methods`.
- `bootstrap/slice1/evidence_store.py:390-398` mostra que o resumo agrega `len(value)` para listas.

**Impacto:** o mesmo run pode parecer contraditório. Para uma evidência auditável, “13 métodos com falha” e “28 entradas de falha” são unidades diferentes.

**Correção sugerida:** adicionar `failed_methods` ao resumo e rotular `failure_entries`/`failures` explicitamente. Não mudar o resultado; apenas representar as duas unidades.

**Critério de verificação:** o resumo exibe `tests_run`, `failed_methods` e `failure_entries`; os documentos usam a mesma unidade ao citar 13.

---

### A6 — Bootstrap muito maior que o produto (Média/Alta)

**Evidência:** 3.404 linhas no bootstrap vs. 704 no Core; 15 arquivos vs. 9 módulos.

**Impacto:** risco de o aparato de verificação se tornar o verdadeiro produto, com custo de manutenção, contratos e gates que o Core não justifica. O rótulo “transitório” não reduz esse custo sozinho.

**Correção sugerida:** congelar novos gates; medir o custo de cada um; remover/mergear o que não protege uma decisão de produto; definir um limite de linhas para o bootstrap.

**Critério de verificação:** nenhum aumento líquido do bootstrap sem remoção equivalente ou justificativa aprovada.

---

### A7 — OpenSpec ainda é nominal (Média)

**Evidência:** 50 arquivos em `openspec/changes`; não há `openspec/specs`, `project.md`, `tasks.md` nem CLI.

**Impacto:** a arquitetura declara OpenSpec como fonte de requisitos, mas o repositório não tem a estrutura que torna essa fonte formal e verificável.

**Correção sugerida:** criar a estrutura canônica e a CLI, ou documentar a adaptação como “change docs” e retirar a alegação de OpenSpec até formalizar.

**Critério de verificação:** `openspec/specs` e `project.md` existem, ou a adaptação está registrada e o vocabulário dos documentos é coerente.

---

### A8 — Segurança do produto ainda não demonstrada (Alta para alegações de produto)

**Evidência:**

- POC-01–04 `NOT_RUN`.
- O store de evidências fica no projeto e não protege contra quem escreve no host.
- Hash prova bytes, não origem/verdade; não há assinatura.
- O bootstrap usa dados sintéticos.
- `spike/slice0/workspace/` tem 57 MB ignorados, 11 `auth.json` e 8 arquivos `.db`/`.log`; não abri o conteúdo.
- Não encontrei padrão de segredo em arquivos rastreados.

**Impacto:** “segurança por padrão” é design, não propriedade demonstrada. As POCs são o gate que falta.

**Correção sugerida:** executar as POCs antes de reivindicar segurança de produto; manter o store oficial fora do workspace gravável; limpar ou isolar o workspace do spike com aprovação.

**Critério de verificação:** evidência reproduzível das POCs; store oficial em domínio protegido; nenhum artefato sensível no caminho de commit.

---

### A9 — Prioridade híbrida: evidências vs. unblock (Média)

**Evidência:**

- `STATE.md:26` manda implementar exportação/regressões e só depois retomar v3/unblock.
- `STATE.md:19` destaca o unblock aprovado.
- Os últimos quatro commits são de evidências.

**Impacto:** não é contradição lógica, mas o estado não tem uma frase única de prioridade. O desbloqueio aprovado continua sem implementação enquanto o escopo de evidências cresce.

**Correção sugerida:** registrar formalmente a ordem atual e o timebox: “fechar exportação/regressões/selo7 até X; depois v3/unblock”. Se a ordem mudar, atualizar o registro.

**Critério de verificação:** `STATE.md` tem uma única “Próxima tarefa”; o change doc correspondente reflete a mesma ordem.

---

### A10 — Higiene menor de repositório (Baixa)

**Evidência:**

- Sem `.gitignore` na raiz.
- `bootstrap/slice1/__pycache__/`, `tests/__pycache__/` e `udh/__pycache__/` aparecem como untracked.
- Não há `README`, `pyproject.toml`, `requirements.txt` ou `tools/harness.py`.

**Impacto:** Baixo para a fundação, mas aumenta o risco de commits acidentais e dificulta a entrada de um novo colaborador. Para um produto que quer ser usável por iniciantes, a ausência de README/entrypoint instalável é uma lacuna de maturidade.

**Correção sugerida:** adicionar `.gitignore` raiz; remover `__pycache__` do índice de trabalho; criar README mínimo e, quando a fase permitir, um entrypoint instalável. Não fazer isso no mesmo commit da correção normativa.

---

## 7. Segurança e confiança

- As POCs de isolamento, autenticação, gateway e runtime não foram executadas. A expressão “segurança por padrão” é design, não demonstração.
- O armazenamento de evidências usa hashes e publicação atômica, mas não assinatura; não prova origem, apenas bytes. A própria documentação reconhece que não protege contra quem escreve no host.
- `spike/slice0/workspace/` ocupa 57 MB ignorados e contém 11 `auth.json` e 8 arquivos `.db`/`.log`; não abri o conteúdo. Não encontrei segredo rastreado, mas esse diretório deve ser tratado como sensível.
- Sem remote/tags, o repositório local é a única cópia. Para um projeto que depende de evidências e histórico, isso é um risco operacional.

---

## 8. Veredito

**Sólido no método, frágil na fonte de verdade, incompleto como produto.**

O núcleo transacional é bom para o estágio; a disciplina de evidência é o melhor ativo; a honestidade sobre `INCOMPLETE` é exemplar. O risco principal não é um bug no Core. É a combinação de:

- documento normativo fora do Git e sem aprovação;
- `STATE.md`/`MEMORY.md` contando histórias diferentes;
- bootstrap crescendo mais que o produto;
- OpenSpec e segurança ainda nominais;
- desbloqueio aprovado, mas adiado.

O projeto não está “quebrado” nem “pronto”. É uma **fundação promissora com governança a fechar antes de expandir**.

---

## 9. Instruções para o Codex

Este relatório é diagnóstico, não especificação. Ao corrigir:

1. **Reproduza cada achado antes de alterar.** Se não for reproduzível no checkout atual, registre como “não confirmado”; não invente uma correção.
2. **Não rode `git add -A` nem `git commit -a` enquanto A1 não estiver resolvido.** Isso pode apagar o baseline antigo, adicionar a v2.5.1 e incluir `__pycache__` no mesmo commit.
3. **Não edite testes de aceite, evidências históricas, selos ou histórico Git** para fazer uma correção passar. Preserve a regra de que o implementador não enfraquece o aceite.
4. **Trate A1/A2 como decisão humana.** Não escolha silenciosamente entre “manter o baseline antigo” e “substituir pela v2.5.1”; apresente as opções e peça aprovação.
5. **Faça mudanças pequenas e commits separados.** Governança documental, curadoria de `STATE.md`/`MEMORY.md`, docs de verificação, resumo de evidências e higiene não devem ir no mesmo commit.
6. **Se a correção mudar requisito ou escopo**, abra/atualize o change OpenSpec correspondente e peça aprovação antes de implementar. Não crie uma especificação paralela.
7. **Verifique com os gates existentes**; não invente um novo gate para contornar uma falha. Para mudanças documentais, rode os 85 complementares, `git diff --check`, conferência de links e limites de `STATE.md`/`MEMORY.md`. Para mudanças em evidências, rode os gates completos aplicáveis.
8. **Relate o que foi corrigido, o que foi rejeitado e por quê.** Resultado parcial é resultado válido; resultado inventado não é.

### Ordem de prioridade sugerida

1. A1 + A2: resolver a fonte normativa e a seção 0/matriz.
2. A3 + A4: curar estado e reconciliar verificações.
3. A5: corrigir a representação das métricas de evidência.
4. A6 + A9: timeboxar o bootstrap e decidir a ordem evidências/unblock.
5. A7 + A8: formalizar OpenSpec e provar/estreitar as alegações de segurança.
6. A10: higiene de repositório, sem misturar com o resto.

---

## 10. Evidências e comandos usados

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" \
  python3 -m unittest discover -s tests
# Ran 85 tests in 13.069s — OK

git status --short
git diff --check
git tag -l
git remote -v

wc -lc UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md
git show HEAD:Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md | wc -lc

find openspec -maxdepth 4 -type f | sort
find openspec -name 'tasks.md' -o -name 'project.md' -o -path '*/specs/*'
command -v openspec
```

Status do worktree antes da criação deste relatório:

```text
 D Universal-Dev-Harness-v2_5-BASELINE-NORMATIVO-CONSOLIDADO-FINAL.md
?? UDH-v2.5-ARQUITETURA-CONSOLIDADA-v2.5.1.md
?? bootstrap/slice1/__pycache__/
?? tests/__pycache__/
?? udh/__pycache__/
```

---

## 11. O que não foi verificado

- Os AT de bootstrap e os gates completos não foram reexecutados nesta análise.
- As POCs de segurança não foram executadas.
- Os `auth.json` e demais arquivos do workspace do spike não foram abertos.
- Não foi feita auditoria de segurança de dependências, rede, containers ou runtime.
- Não foi demonstrada a troca real de agentes Pi/OpenCode/Codex pelo Core.
- Não foi demonstrada a entrega ponta a ponta de um aplicativo por um iniciante.

Essas ausências não invalidam os achados documentais, mas impedem tratar este relatório como certificação técnica ou de segurança.
