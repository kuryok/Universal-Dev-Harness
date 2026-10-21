# Slice 1 — fronteira de confiança do bootstrap

Estado: implementação e verificação aprovadas explicitamente pelo usuário em 21/10/2026 (“Sim!”); recorte implementado e verificado, T1–T8 PASS. Não altera os cenários A1–A10.

## Problema e resultado esperado

O runner atual põe testes e candidato no mesmo namespace. Montagens RO impedem algumas escritas, mas não isolam o processo verificador do processo verificado. A próxima tarefa deve provar a separação e a recusa de adulteração, sem chamar essa prova de ACCEPTED do produto.

Ao executar uma fixture que imprime “passou”, tenta alterar arquivos de teste e encerra com erro, o processo externo deve registrar o erro real e confirmar que o snapshot confiável não mudou. Um snapshot divergente do manifesto impede iniciar o candidato.

## Fronteira e limites

- Controlador/verificador transitório no host da VM; candidato em contêiner rootless com namespace de PID próprio, sem rede, socket Podman ou credenciais.
- Testes, manifesto, configuração do runner e evidências ficam no lado do controlador e não são montados no candidato. Só código candidato RO, bibliotecas/runtime RO e diretório exclusivo de dados RW entram no contêiner.
- Store transitória em diretório exclusivo dentro de `bootstrap/slice1/`, fora da árvore que será montada como workspace candidato. Não escrever fora do projeto.
- Isso protege contra o processo contido. **Não protege contra esta sessão de construção com escrita irrestrita no host.** Aprovação humana e futura execução de implementadores somente no sandbox delimitam a passagem ao domínio operacional. Não apresentar chmod/hash/Git como isolamento contra o mesmo proprietário no host.
- Sem executar agentes de produto, sem rede, instalações, download de imagem, privilégios ou outro orquestrador. Runtime já disponível e imagem local por digest, com `--pull=never`.

## Responsabilidades e arquivos permitidos

| Arquivo novo | Responsabilidade |
| --- | --- |
| `bootstrap/slice1/trust_snapshot.py` | Copiar apenas arquivos explicitamente autorizados; conferir hashes e produzir manifesto do snapshot |
| `bootstrap/slice1/isolated_process.py` | Invocar Podman com argumentos fixados pelo controlador; coletar exit code, stdout, stderr e timeout sem importar candidato |
| `bootstrap/slice1/check_boundary.py` | Exercitar fixtures adversas e produzir evidência identificada |
| `bootstrap/slice1/fixtures/` | Payloads sintéticos sem segredos nem código de produto |
| `bootstrap/slice1/evidence/boundary.json` | Resultado observado, comandos, hashes e limitações |

Diretório temporário `bootstrap/slice1/boundary-*` também permitido para snapshots, manifestos e fixtures sintéticas; remover somente o diretório criado pela própria execução ao terminar. Evidência persistente somente no caminho autorizado acima. Documentos desta mudança e MEMORY.md também permitidos. Não modificar AT candidatos, `run_baseline.py`, arquivos de produto ou harness protegido. Integração com os AT existentes requer mudança pelo autor independente em tarefa separada; esta tarefa verifica a fronteira antes dessa integração.

## Snapshot e manifesto transitórios

Entrada: arquivos de testes/cobertura do commit `dc848ba`, conferidos contra os hashes de `bootstrap/slice1/evidence/baseline.json`. Rejeitar symlinks, arquivos ausentes, caminhos fora da lista e divergência de bytes antes de iniciar processo. Não executar/importar código ao copiar ou conferir.

Manifesto contém versão do formato, identificador único da execução, commit de referência, hashes SHA-256 dos arquivos e identificação dos componentes do controlador/runtime usados. Não é ainda `at_version` oficial ou aprovação automática. Origem e referência não podem vir do workspace candidato. Manifesto e evidência ficam fora das montagens do candidato; conferir snapshot antes e depois da execução. Divergência posterior invalida resultado, sem autoatualizar hashes para fazer passar.

## Critérios verificáveis

| ID | Comportamento | Prova complementar prevista |
| --- | --- | --- |
| T1 | Candidato não escreve no snapshot/manifesto/evidência do host | Fixture tenta caminho absoluto e symlink para sentinela exclusiva do teste; arquivo inacessível no namespace e hashes externos preservados |
| T2 | Saída inventada não aprova | Fixture imprime JSON/sucesso falso e retorna 23; controlador registra 23 e resultado negativo |
| T3 | Host não executa código candidato | Fixture contém código de topo que cria sentinela somente ao executar no contêiner; copiar/conferir bytes no host não cria a sentinela. Revisão identificada confirma ausência de import dinâmico e configuração executável do candidato no controlador; entrypoint no contêiner é explícito |
| T4 | Adulteração é recusada antes de executar | Alterar somente cópia temporária sintética do snapshot; verificador recusa e sentinela de execução não aparece |
| T5 | Timeout não deixa execução em andamento | Fixture bloqueia; controlador limita tempo, remove seu contêiner e confirma ausência por identificação única |
| T6 | Isolamento de processos é efetivo | Host compara identidades dos namespaces PID/mount do contêiner e do controlador; sentinela host descartável com marcador exclusivo não aparece no /proc do candidato. Não enviar sinais ao verificador nem usar PID numérico isolado como prova |
| T7 | Controle positivo funciona | Fixture inofensiva retorna 0 e conteúdo conhecido; controlador coleta ambos |
| T8 | Arquivos anteriores preservados | Hashes dos AT/stub e suas evidências anteriores continuam iguais; checagem sintática e diff sem erros |

Tentativas usam apenas fixtures temporárias. Em T6, identificadores numéricos podem colidir entre namespaces: não enviar sinais destrutivos; sobrevivência do verificador sozinha não comprova isolamento. Não alterar AT reais/candidatos para simular adulteração. Cada falha de infraestrutura tem categoria própria e não conta como aprovação de critério. Timeout, sinal, recusa de política e exit funcional são resultados distintos. Código negativo do cliente Podman identifica sinal nesse cliente. Código positivo 128+ é TERMINATION_UNKNOWN, pois pode representar sinal no candidato ou saída explícita: preservar o código bruto, não afirmar origem e nunca tratar incerteza como aprovação. Verificar ambos os casos com sinal somente no contêiner próprio e fixture exit137. Limitar stdout/stderr e tempo; não usar shell nem extrair comandos de saída do candidato.

## Verificação e encerramento

Executar `python3 bootstrap/slice1/check_boundary.py`; cada T recebe PASS, FAIL, NOT_RUN ou INFRA_ERROR. Conclusão positiva exige T1–T8 em PASS, incluindo registro identificado da revisão de T3. Revisão pendente ou resultado parcial impede declarar a fronteira comprovada; não converter NOT_RUN em sucesso. Guardar evidência externa ao contêiner com identidade, parâmetros, códigos, hashes e limites. Rodar `git diff --check`, validar sintaxe Python e solicitar revisão independente do candidato/resultado. Sem harness oficial, fast/full não executados; OpenSpec CLI não executada porque ausente.

Concluído esse recorte, a próxima tarefa é integrar os AT independentes ao controlador externo e definir aprovação/selo da primeira versão. Core + SQLite permanece depois dessa preparação; nenhuma mudança de estado de produto é autorizada por este documento.

Revisão documental independente: ajustados T3 (efeitos de importação), T6 (identidade de namespaces sem sinais destrutivos), estados por critério e escopo/descarte temporário. As fixtures T1–T8 foram executadas; resultado abaixo.

## Resultado verificado — 21/10/2026

- Comando: `PYTHONDONTWRITEBYTECODE=1 python3 bootstrap/slice1/check_boundary.py`, exit 0. T1–T8 em PASS na execução final, com revisão T3 identificada e hashes coincidentes.
- [Evidência](../../../bootstrap/slice1/evidence/boundary.json) contém manifesto transitório, identidade da execução, parâmetros efetivos, resultados por critério, tentativas anteriores e confirmação de descarte da store temporária. [Revisão independente](bootstrap-review.json) registra os arquivos examinados e ausência de achados pendentes.
- T1 recusou seis escritas (caminho absoluto/symlink para snapshot, manifesto e sentinela de evidência); hashes externos preservados. T2 preservou exit23 apesar do falso PASS. T3 confirmou ausência de execução na cópia e entrypoint explícito isolado, com revisão do host.
- T4 recusou cinco adulterações de cópias temporárias antes de lançar processo: bytes, ausência, arquivo extra, symlink e manifesto divergente. T5 confirmou timeout, limite de saída e ausência dos contêineres após limpeza; códigos 137 de SIGKILL controlado e saída explícita ficam TERMINATION_UNKNOWN, sem inventar a causa.
- T6 comprovou namespaces PID/mount distintos e invisibilidade da sentinela host no /proc do candidato, sem sinalizar o verificador. T7 controle positivo exit0; T8 preservou os hashes anteriores e validou sintaxe/diff.
- Falhas durante desenvolvimento preservadas na evidência: cleanup inicial esperava SIGTERM padrão além do prazo (corrigido com `rm --force --time=0`); prova adicional com SIGTERM não encerrava PID1 (substituída por SIGKILL controlado ao contêiner próprio e exit137 explícito). Nenhum critério/AT foi enfraquecido.
- Não houve rede, download, nova dependência, mudança nos AT candidatos ou código de produto. O snapshot não foi mantido como store operacional; o manifesto preservado na evidência não é selo oficial.
- Não executados: gates fast/full (harness oficial ausente), CLI OpenSpec (ausente), AT de produto e review A8 (Core ainda não implementado). A prova não certifica segurança contra o usuário/sessão que escreve no host nem conformidade completa do produto.

Próximo passo: preparar integração dos AT independentes com o controlador externo e aprovação/selo da primeira versão, antes de iniciar Core + SQLite. O autor implementador continua sem autorização para alterar os AT.
