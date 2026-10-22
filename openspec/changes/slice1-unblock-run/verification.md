# Verificação do preparo de desbloqueio

Contrato e preparo nomeado aprovados pelo usuário em 22/10/2026. Este incremento prepara o contrato executável; não implementa migração v3 nem desbloqueio no Core. O candidato permanece `ad2bd478fa8a07a06b6e1a1b832b1795fafe1c4d`.

## Executado nesta sessão

- DDL final e renames/FKs refinados em [design.md](design.md). Experimento sintético SQLite em memória: bases vazia/PENDING/BLOCKED preservadas. Não substitui AT do produto.
- Perfil administrativo schema2 com quatro operações e perfil legado schema1 preservado; ambos medidos/pinados pelo controlador confiável. Sonda negativa isolada testa contexto ausente/parcial, substituição de configuração e bloqueio de override do banco antes do runtime.
- 39 testes complementares PASS: 36 existentes e três do preparo. A primeira rodada teve um erro porque design.md ainda estava em autoria; documento concluído e rodada completa passou, sem alterar asserções existentes.
- Regressão externa no candidato fixo: dez AT v1, onze AT policy e 13 AT v5 PASS, sem failures/errors/skips. I1–I7/T1–T8 PASS. V5: 171 chamadas auditadas, zero falhas de infraestrutura. [Resumo](../../../bootstrap/slice1/evidence/unblock-regression.json).
- Os 68 arquivos dos selos v1–v5 permaneceram byte a byte iguais; Core e testes de aceite anteriores não foram alterados.

## Baseline e revisão

Dois baselines preliminares foram preservados (`unblock-baseline-initial.json` e `unblock-baseline-before-timestamp-review.json`). Ambos discriminaram ausência funcional, mas a revisão encontrou lacunas que o Core antigo não expunha porque falhava antes: flag --db interceptada pela fronteira, hashes sem comparação, insuficiência de casos negativos e calendário da data não validado. A autoria independente corrigiu os AT; o transporte não passou a tratar exit125 como sucesso. A sonda adicional demonstra apenas os contextos negativos ensaiados e ausência de chamadas sqlite3.connect nesse experimento, sem alegar prova universal de syscalls ou antiforja de contexto completo.

Baseline definitivo [unblock-baseline.json](../../../bootstrap/slice1/evidence/unblock-baseline.json): PASS como discriminação negativa, exatamente 16 métodos (13 falhas funcionais esperadas e três PASS), 150 chamadas auditadas, zero erros de infraestrutura/errors/skips. Fontes16 e specs2 correspondem aos hashes executados; os nove módulos do candidato permanecem inalterados. Nenhum resultado negativo significa que unblock funciona.

[Revisão independente](preparation-review.json): PASS, sem achados pendentes, vinculada aos arquivos e ao hash canônico do relatório `a932aae4bdc26185cb69e4ecca5d4f8f73d9ada99505d2b738376efec546d6c2`. O revisor também reproduziu o experimento DDL nas três bases sintéticas. Parecer de preparo, sem aceite de produto.

Publicado e verificado duas vezes pelo publicador exclusivo: selo v6 `sha256:7196e5f4eea1fc78806efcdbd2b1e231446b2632937d5aff69c4f2f8bf0d9b37`, encadeado ao v5. Repetir a publicação com a mesma revisão confirmou identidade; nenhum snapshot anterior foi alterado. A publicação não habilita operação no Core.

## Reprodução e limites

Complementares: `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 -m unittest discover -s tests -v`.

Baseline: `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/bootstrap/slice1" python3 bootstrap/slice1/integrate_block.py --baseline`. O comando prepara rascunho protegido e snapshot Git fixo; não importa o Core no host. Reexecutá-lo substitui o artefato corrente de baseline, cujas auditorias incluem nomes efêmeros de contêineres.

Harness fast/full e CLI OpenSpec: não executados, pois ausentes. Sem dependências, imagens, rede ou credenciais novas. Observação de interrupção permanece instrumentada; este preparo não demonstra durabilidade de hardware, instalação operacional, checkpoint Git/spec/artefatos, ACCEPTED/DELIVERED ou execução de agentes. Slice 1 permanece incompleta.

Próximo incremento já abrangido pelo contrato aprovado: implementar e verificar migração explícita/leitura v3, preservando v1/v2; somente depois habilitar unblock e replay histórico, com as verificações respectivas.
