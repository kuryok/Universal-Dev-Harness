# Preparo dos AT e selo da consulta de policy

Estado: infraestrutura explicitamente aprovada pelo usuário em 21/10/2026 ("aprovado"), após aprovação do contrato de policy. Inclui autoria e revisão independentes por subagentes e novos componentes protegidos abaixo; preserva v1.

## Objetivo

Viabilizar os cenários P1–P7 aprovados antes da implementação de policy, mantendo integralmente o selo v1 e seu caminho de execução. Sem alteração do Core nesta tarefa.

## Escopo solicitado

- Autor independente, em contexto separado do implementador, cria `bootstrap/slice1/policy_acceptance/test_policy.py` e `coverage.json`, derivados exclusivamente do contrato aprovado. O implementador não modifica esses AT. Solicita-se autorização expressa para essa autoria independente por subagente e posterior revisão independente.
- Novos módulos `bootstrap/slice1/policy_seal.py`, `policy_bridge.py` e `integrate_policy.py` preparam, verificam e executam a geração 2. Nenhum arquivo da geração 1, seus controladores, AT, documentos fixados ou manifestos pode ser alterado.
- `policy_bridge.py` reutiliza o isolamento existente, mas verifica o selo v2; a verificação fixa de v1 não será contornada nem relaxada para aceitar v2. Não executar/importar o Core no host de verificação.
- `policy_seal.py` publica exclusivamente em `bootstrap/slice1/seals/v2`, por publicação exclusiva e sem substituição. Rascunhos ficam em diretórios temporários internos ao projeto. Sem suporte genérico a gerações arbitrárias, plugins ou escolha de testes pelo candidato.
- Evidências novas em `bootstrap/slice1/evidence/policy-*.json` e neste diretório OpenSpec; MEMORY.md permanece curto. Testes complementares da infraestrutura em `tests/test_policy_infrastructure.py` não substituem AT.

## Identidade e confiança

Manifesto canônico versionado inclui generation=2, previous_at_version fixado à identidade v1 aprovada, hashes de todos os AT/controladores usados, hashes dos requisitos aprovados, origem dos testes e referência verificável à aprovação. at_version é SHA-256 do corpo canônico, não assinatura ou credencial. O chamador confiável fornece a identidade esperada; o candidato não escolhe manifesto, baseline, testes ou revisão.

O selo v1 precisa ser verificado pela implementação v1 intacta antes de preparar/publicar v2. Os dez AT v1 continuam executados pelo caminho v1. O conjunto policy acrescenta cobertura; não substitui nem adapta suas asserções. O novo controlador mantém candidato somente leitura, dados de trabalho separados, auditoria privada, rede bloqueada, limites e limpeza comprovada. Falha de infraestrutura nunca conta como falha funcional esperada ou PASS.

A publicação de v2 exige revisão independente PASS vinculada aos hashes exatos, sem achados pendentes, e baseline negativo dos novos AT contra o Core sem policy. O relatório enumera testes executados e resultados esperados por caso: os testes de comportamento novo precisam falhar por ausência de policy, com infraestrutura íntegra; testes de regressão existentes precisam passar. Não exigir falha de todo teste nem aceitar qualquer erro indiscriminadamente. P7 é revisão independente, não um teste funcional fictício.

## Critérios de pronto

1. Autoria independente dos AT cobre matriz, códigos de saída, precedência, hash canônico, estabilidade entre processos, ausência de efeitos e tentativas de alegar autoridade.
2. Baseline negativo reproduzível executado pelo isolamento; candidato e AT identificados por hashes, falhas funcionais classificadas e nenhum erro de infraestrutura tolerado.
3. Selo v2 rejeita alteração, identidade divergente, arquivo extra/ausente, symlink, cadeia inválida, revisão divergente e tentativa de substituir versão publicada; publicação idêntica é verificável/reutilizável.
4. Revisão independente dos novos controladores e testes complementares PASS antes de publicar v2; nenhuma alegação de proteção contra o operador com acesso ao host.
5. Nove testes atuais, dez AT do Core, I1–I7/T1–T8 e integridade v1 passam nesta etapa; resultados não executados são explicitados.
6. Após esse preparo, implementar apenas os arquivos de produto permitidos no contrato de policy já aprovado, executar os novos AT e regressões e obter revisão independente do candidato. Nenhuma transição ou execução de agente é liberada.

## Limites

Sem dependências, downloads, credenciais ou escrita fora do projeto. Não modificar ferramentas protegidas existentes. Se a reutilização do isolamento exigir alteração nelas, parar e apresentar o delta antes de editar. Esta autorização é para infraestrutura transitória de bootstrap e autoria independente; não declara aceite oficial do produto nem encerra Slice 1.
