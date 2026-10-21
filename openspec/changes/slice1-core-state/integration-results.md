# Resultado — integração dos AT e primeiro selo do bootstrap

Data: 21/10/2026. Recorte: [especificação](at-integration.md). Execução final: `6043ba9c6fa64f3f89c1a2f3ce7f16f3`.

## Resultado

I1–I7 PASS; comando `PYTHONDONTWRITEBYTECODE=1 python3 bootstrap/slice1/integrate_acceptance.py`, exit 0. Revisão independente sem pendências em [integration-review.json](integration-review.json).

- Dez métodos, mesmos cenários/assertivas dos originais; somente transporte alterado por autor independente.
- Baseline identificado por commit `dc848ba`: 18 falhas comportamentais esperadas, zero erros de infraestrutura, 38 chamadas auditadas fora do candidato. Isso demonstra detecção do stub incompleto, não aprovação de produto.
- Testes, configuração, auditoria e manifesto ficaram fora das montagens. Só dados em /work graváveis; dados com symlink recusados. Erro 125 do bridge vira InfrastructureError e não pode contar como teste negativo esperado.
- Seis corrupções sintéticas recusadas; repetição idêntica preservou identidade. Publicação exclusiva não substitui versão existente. Não houve nova dependência, rede, download ou escrita fora do projeto.
- T1–T8 executados novamente em PASS. Originais de AT, stub e scripts anteriores preservados; a evidência boundary.json foi atualizada pela regressão, mantendo histórico.

## Versão inicial

`at_version`: `sha256:e6056d5bec095d4023b1a30286dc978d000e3d0b3e8ab19f4642da8c84bab704`.

Geração 1, predecessor nulo, formato canônico. [Manifesto e arquivos selados](../../../bootstrap/slice1/seals/v1/manifest.json) vinculam SHA-256 de testes/cobertura/controlador, referências de spec, instrução humana e revisão com baseline negativo. Alterações nos documentos referenciados ou nos componentes exigem reconciliação; nunca atualizar silenciosamente esta versão. O relato de execução fica neste arquivo separado para preservar os bytes da especificação selada.

[Evidência estruturada](../../../bootstrap/slice1/evidence/integration.json) preserva também a execução preliminar DRAFT anterior à revisão/publicação, com sua identidade. Store v1 persiste no host e não entra no contêiner; temporários e contêineres foram removidos.

## Limites e próximo passo

É o selo do bootstrap, protegido contra o candidato contido. Não é assinatura nem proteção contra a sessão/proprietário com escrita no host; não registra ACCEPTED/DELIVERED. Não há Core de produto ou SQLite operacional. A8 e o idioma das mensagens ainda precisam de revisão sobre o futuro candidato real.

Não executados: CLI OpenSpec e fast/full do harness, ausentes. A execução deste comando é a verificação da integração contra o stub, não uma suíte de aceite de Core já implementado.

Próximo passo: implementar o recorte Core mínimo + SQLite da [proposta](proposal.md), mantendo o selo e os AT fora da lista de escrita do implementador; usar o transporte externo para verificar o candidato e revisão independente para A8.
