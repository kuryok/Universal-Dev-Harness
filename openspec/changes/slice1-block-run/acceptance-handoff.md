# Handoff proposto — migração e bloqueio

Estado: extensão de infraestrutura explicitamente aprovada pelo usuário em 21/10/2026 ("Aprovado, se não há regressão faça!"). A aprovação não dispensa o contrato específico de observação M3/B4 previsto abaixo. Não modifica o contrato aprovado ou arquivos v1/v2 protegidos.

## Arquivos e responsabilidades

- Autor independente: novos `bootstrap/slice1/block_acceptance/test_migration.py`, `test_block.py` e `coverage.json`. Derivados de proposal/design, sem importar Core no host. O implementador não cria/edita esses AT.
- Controlador transitório novo: `bootstrap/slice1/operator_process.py`, `operator_bridge.py`, `block_seal.py` e `integrate_block.py`. Reutilizar bytes do isolamento v1 quando cabível; não alterar seus arquivos/manifestos. Se algum limite da implementação existente impedir reutilização, implementar apenas a variante aprovada ou apresentar delta antes de tocar nos originais.
- Fixtures confiáveis novas em `bootstrap/slice1/block_fixtures/`: seeds SQLite v1, sondas adversárias e launcher fixo. Nenhuma é importada do candidato no host. Fixtures geradas só em diretórios temporários internos ao projeto.
- Testes complementares em `tests/test_block_infrastructure.py`; evidências em `bootstrap/slice1/evidence/block-*.json`; novo selo exclusivamente `bootstrap/slice1/seals/v3`.
- Requisitos, design, revisão e mapeamento de critérios permanecem neste change OpenSpec. MEMORY.md continua ≤35 linhas/300 palavras.

Nenhum arquivo existente de AT/controlador/selo v1/v2, tools/harness.py, CI ou configurações de engines está autorizado a mudar. Sem novas dependências, imagens baixadas, credenciais, rede ou escrita fora do projeto.

## Versão e autoria

Geração3 aponta para `sha256:438ce43f70e0137115f2c30920623eabf5bfd042035958951fdaafa151e41f3f`, identifica todos os AT/controladores/fixtures e hashes de proposal/design/handoff. Os selos v1/v2 são verificados com seus verificadores intactos. Publicação exclusiva, sem substituição; repetição idêntica reutilizável; adulteração/arquivo extra/symlink/cadeia inválida/revisão ou baseline de outros bytes recusados.

A revisão independente vincula os hashes exatos e o baseline negativo ao relatório real. Não aceitar apenas status PASS. Cada caso é classificado pelo autor: NEW_FAILURE funcional específica contra o Core atual sem operator/migração, EXISTING_PASS para compatibilidade, ou CHECK independente explicitamente documentado. Erro de runtime/transporte, timeout e limpeza incompleta nunca satisfazem baseline negativo. Cenário sem AT ou com apenas erro de infraestrutura impede implementação correspondente.

## Runtime a verificar

- Contêiner administrativo: rede none, rootfs RO, capacidades removidas, no-new-privileges, limites de tempo/saída/CPU/memória/PIDs. Apenas Core/launcher/control RO e banco sintético privado RW.
- Contêiner adversário separado: isolamento já vigente; nenhum mount do estado/controle administrativo, seus pais, sockets do runtime ou FDs herdados. Criar banco próprio não prova acesso ao oficial.
- Host: só controlador/AT confiáveis; nenhuma importação do candidato. Inspeção independente de mount/PID/configuração, leitura SQLite confiável após encerrar processo e validação de caminhos/arquivos especiais antes de ler dados candidatos.
- Cada sessão fixa um commit/digest do Core, cópia RO identificada e pins de manifesto/configuração/launcher. AT executam o candidato em runtime, não no host; somente após verificação e revisão pertinentes há qualquer proposta de promoção operacional. Aqui todos os dados são sintéticos.
- Classificar falhas de lançamento, terminação ambígua, limite de saída e limpeza como INFRA_ERROR; manter código bruto. Para testes deliberados de kill, observador confiável precisa provar o ponto atingido e a interrupção/reabertura. Um timeout genérico não prova atomicidade.

## Interrupção determinística: gate explícito

M3/B4 exigem evidência de que a interrupção atingiu um ponto entre gravações. Não introduzir flags de teste na CLI administrativa nem aceitar hooks do candidato como autoridade. Antes de implementar esses AT, o autor independente deve declarar como observará cada ponto (instrumentação de teste protegida ou mecanismo SQLite/processo verificável) e quais bytes executados diferem do candidato, se houver. Aprovar esse contrato e registrar os limites antes de alegar teste black-box. Até lá, M3/B4 são NOT_RUN e bloqueiam a entrega da transição; outros testes não os substituem.

## Critérios de pronto do preparo

1. DDL derivado revisado e origem dos pins/launcher/mounts sem ambiguidade; controles negativos testáveis, sem alegação de autenticação por hash.
2. AT independentes cobrem M1–M3/B1–B5 e regressões R1; R2 é revisão, não teste funcional fictício. Compatibilidade v1, leitor v2 e ausência de efeitos são verificados separadamente.
3. Baseline reproduzido contra o commit do Core atual, identificado por SHA completo e mapa dos módulos; distinguir ausência esperada de feature de erro de infraestrutura.
4. Complementares, revisão independente e publicação v3 PASS nos hashes exatos; v1/v2 preservados. Só então iniciar o incremento de migração, seguido pelo bloqueio autorizado.
5. Relatar testes não executados sem reduzir os critérios. Nenhum aceite oficial de produto ou Slice1 completa é declarado por este bootstrap.

Esta autorização, se concedida, é para o preparo transitório nomeado. Não autoriza instalar o Core fora do projeto nem tocar infraestrutura protegida antiga. A implementação de produto continua delimitada por proposal.md e design.md.
