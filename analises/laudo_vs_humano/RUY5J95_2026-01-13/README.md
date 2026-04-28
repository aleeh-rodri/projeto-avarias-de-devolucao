# Comparação laudo.json vs PDF humano — RUY5J95

## Totais
- Laudo (soma serviços): **R$ 1015.00**
- Humano (soma itens extraídos): **R$ 2616.74** (hint total no PDF: 2616.74)
- Delta (laudo - humano): **R$ -1601.74**

## Itens faltando no laudo (somente humano)
- (1x) R$ 936.74 — Troca para-brisa dianteiro
- (1x) R$ 370.00 — Recuperação e pintura da caixa de ar do lado esquerdo
- (1x) R$ 340.00 — Pintura porta dianteira esquerda
- (2x) R$ 320.00 — Pintura para-choque dianteiro

## Itens em excesso no laudo (somente laudo)
- (1x) R$ 400.00 — Recuperação e pintura para-choque dianteiro (perito: perito_parachoque)
- (1x) R$ 150.00 — Pintura retrovisor direito (perito: perito_lataria)
- (1x) R$ 135.00 — Troca jogo de calotas (perito: perito_pneus_rodas)
- (1x) R$ 0.00 — Troca para-brisa dianteiro (vidro - não consta na LPU) (perito: perito_vidros)

## Possíveis mismatches (descrição similar, valor diferente)
- sim=0.806 Δ=+80.00 | laudo: R$ 400.00 recuperacao e pintura para choque dianteiro | humano: R$ 320.00 pintura para choque dianteiro

## Próximas oportunidades (hipóteses a validar)
- Vidros: PDF humano cobra **troca do para-brisa** (peça) + mão de obra; laudo atual só trouxe mão de obra.

- Lataria: PDF humano tem **pintura porta dianteira esquerda** e **recuperação/pintura caixa de ar lado esquerdo**; laudo não trouxe esses itens (gap de cobertura/triagem de partes).

- Para-choque: humano tem **2x pintura para-choque dianteiro (R$320)**; laudo tem **recuperação e pintura (R$400)** (pode ser regra/serviço diferente ou duplicidade no humano).

- Pneus/Rodas: laudo cobrou **troca de jogo de calotas**; no PDF humano não aparece cobrança de rodas/calotas (possível falso positivo / mapping de peça).


Arquivos gerados:
- comparison.json (diff estruturado)
- human_items.json / laudo_items.json (itens extraídos)