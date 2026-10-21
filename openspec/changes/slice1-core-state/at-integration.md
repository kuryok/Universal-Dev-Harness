# Slice 1 — integração externa dos AT e selo inicial

Estado: recorte solicitado pelo usuário em 21/10/2026: “Integrar os AT ao controlador externo e definir o selo da primeira versão, antes do Core + SQLite.” Implementar e verificar somente esta preparação, sem produto.

## Objetivo e limites

Executar os mesmos cenários A1–A7/A9/A10 com testes no host confiável e cada CLI candidata em contêiner isolado. Preservar os AT anteriores e T1–T8. A8 continua revisão independente do futuro Core. Não declarar ACCEPTED por falhas esperadas de um stub nem apresentar hash como assinatura ou isolamento contra esta sessão com escrita no host.

Sem dependências, rede, downloads, credenciais, UI, agentes de produto ou arquivos fora do projeto. Usar stdlib, Podman e runtime locais já verificados. O controlador transitório continua sem autoridade sobre estados de produto.

## Transporte dos AT

Autor em contexto distinto cria variante em `bootstrap/slice1/integrated_acceptance/`, sem alterar as assertivas ou os arquivos originais. Única mudança funcional permitida nos testes: usar prefixo argv obrigatório do controlador para invocar CLI, nunca executar/importar o candidato no host; resultado 125 do bridge significa InfrastructureError. A identidade e o diff dessa variante serão revisados independentemente.

O bridge recebe argumentos estruturados, traduz somente o caminho de banco que pertence ao diretório de dados autorizado para /work e executa entrypoint fixo no candidato RO. O entrypoint só importa o pacote candidato dentro do contêiner. AT, configuração, manifesto e auditoria não são montados nele. Diretórios de dados não podem conter symlinks antes/depois da execução; qualquer violação interrompe o transporte antes de os testes inspecionarem SQLite no host.

Resultado funcional mantém stdout/stderr/código coletados externamente. Timeout, saída excessiva, recusa de integridade, término inconclusivo ou falha de runtime não contam como falha comportamental esperada. Auditoria por chamada fica fora dos dados graváveis pelo candidato. Prazos e saída continuam limitados. Concorrência usa chamadas e contêineres distintos sobre o mesmo diretório de dados autorizado.

## Selo da primeira versão

Manifesto formato 1, geração 1, predecessor nulo. Contém caminhos permitidos e SHA-256 por arquivo de testes/cobertura/controlador, identidade dos cenários/spec de origem, aprovação humana deste preparo e referência à revisão independente. `at_version` é SHA-256 do corpo JSON canônico; manifestos posteriores terão predecessor, mas atualização de versão não é implementada agora.

A versão fica em `bootstrap/slice1/seals/` fora das montagens do candidato. Publicação é exclusiva, sem sobrescrever uma versão existente. Repetição com os mesmos bytes verifica/reutiliza; divergência é STALE e exige nova decisão, nunca autoatualização. A evidência de baseline negativo e a revisão devem preceder a publicação final. Durante preparo, usar somente cópias temporárias explicitamente identificadas como não publicadas.

Este é o primeiro selo do **bootstrap**: identificação/aprovação dos testes e componentes da preparação transitória, protegidos do candidato contido. Não é assinatura criptográfica, atestado contra o proprietário do host nem aceite do Core. SQLite operacional e gates do produto ainda não existem.

## Arquivos permitidos

- `bootstrap/slice1/integrated_acceptance/`: somente autor independente dos testes.
- Novos módulos `bootstrap/slice1/seal_version.py`, `cli_bridge.py` e `integrate_acceptance.py`: selo, transporte e coordenação/verificação respectivamente.
- `bootstrap/slice1/seals/`: versão publicada, manifesto e aprovação/revisão vinculados.
- `bootstrap/slice1/evidence/integration.json`: resultados estruturados e histórico de falhas deste recorte.
- Temporários exclusivos `bootstrap/slice1/integration-*`, removidos pela própria execução.
- Documentos desta mudança e MEMORY.md.

Não modificar AT originais, runtime isolado, verificador T1–T8 ou scripts do spike. Não criar testes de aceite do produto pelo implementador. Os testes desta integração são verificações complementares do transporte/selo.

## Critérios e verificação

| ID | Comportamento | Prova |
| --- | --- | --- |
| I1 | Mesmos cenários/assertivas, transporte externo | Diff revisado por contexto independente; contagem de 10 métodos e mapeamento preservados |
| I2 | Baseline negativo real pela nova fronteira | 10 métodos executados contra stub identificado, falhas comportamentais esperadas, zero erros de infraestrutura; nenhuma inferência de aceite |
| I3 | Candidato não alcança testes/auditoria/configuração | Montagens efetivas auditadas e sem store/controlador; dados exclusivos, checagens de symlink e limpeza confirmada |
| I4 | Infraestrutura não parece teste negativo aprovado | Prefixo ausente/inválido e bridge125 recusados; resultado estruturado separa falhas, erros e auditoria de transporte |
| I5 | Selo determinístico e inviolado no escopo declarado | Repetição idêntica, recusa de manifesto/arquivo divergente, symlink e path fora da lista em cópias temporárias |
| I6 | Aprovação e cadeia rastreáveis | Geração1/predecessor nulo, hash canônico verificável, origem/revisão/aprovação registradas; publicação sem sobrescrita |
| I7 | Regressão da fronteira e artefatos anteriores | Rodar T1–T8; hashes dos originais/evidências preservados e diff/sintaxe válidos |

Comando previsto: `PYTHONDONTWRITEBYTECODE=1 python3 bootstrap/slice1/integrate_acceptance.py`. Cada I recebe PASS/FAIL/INFRA_ERROR/NOT_RUN; conclusão positiva exige todos PASS e revisão identificada dos hashes atuais. Guardar erros reais; não enfraquecer cenários para obter resultado positivo. OpenSpec CLI e fast/full do harness continuam não executados porque ausentes.

Depois deste recorte: verificar as pré-condições restantes e iniciar somente a tarefa de Core + SQLite já especificada, sem antecipar policy, agentes ou estado ACCEPTED/DELIVERED.
