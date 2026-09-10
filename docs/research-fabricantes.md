# Fabricantes de PCB — levantamento

**Data:** 12/08/2026 · **Decisão tomada:** só JLCPCB tem regras no repo por ora.

## Escolha

JLCPCB é a única com perfis `.kicad_dru` implementados (`rules/jlcpcb/`).
Os outros ficam documentados aqui como plano B — quando um design não couber
no envelope da JLC ou precisar de material especial.

## Comparativo

### JLCPCB — escolhida

Menor preço em protótipo SMT **quando a BOM é 100% LCSC em estoque**. Serviço
SMT economy popula placas por poucos dólares. Fluxo totalmente automatizado,
self-quote instantâneo.

- ✅ Preço imbatível no caso comum
- ✅ Biblioteca de peças gigante e integrada ao orçamento
- ✅ Capacidades e sobretaxas bem documentadas publicamente
- ✅ Ecossistema KiCad maduro (plugins, regras da comunidade)
- ⚠️ Envelope padrão estreito — sai do trilho e a sobretaxa aparece
- ⚠️ Peças consignadas (fora do LCSC) encarecem e atrasam

Números detalhados e tabela de sobretaxas: [`rules/jlcpcb/README.md`](../rules/jlcpcb/README.md).

### PCBWay — plano B para o que a JLC recusa

Aceita cotar e construir **fora do envelope padrão**: stackups avançados, mais
acabamentos de superfície, materiais especiais. Fluxo com cotação humana em vez
de automação pura. Linha de PCB avançado e montagem mais capaz para pedidos
complexos one-stop.

Use quando: stackup não-padrão, material específico (Rogers, alta Tg), acabamento
que a JLC não lista, ou quando a JLC simplesmente recusou o design.

### NextPCB — terceira cotação

Preço turnkey inclui sourcing + SMT + AOI. Serviço Rev0 permite self-quote
online sem troca de e-mails.

### Elecrow / Seeed Fusion — não avaliados a fundo

Menores. Úteis em flex/rígido-flex e lotes pequenos com stencil. Dados públicos
de capacidade são mais escassos — regras `.kicad_dru` sairiam aproximadas, o que
é pior que não ter regra. Ficam de fora até haver necessidade real.

## O critério que realmente decide

Não existe "melhor fabricante" — existe alinhamento com a sua BOM. Três
variáveis interagem de forma não-óbvia:

1. **Estrutura de custo unitário** — quem ganha em 10 placas pode perder em 100.
2. **Restrição de sourcing da BOM** — o vencedor no preço muda completamente
   quando entram peças consignadas ou tecnologia mista.
3. **Framework de QC** — teste funcional e AOI mudam o custo real.

O fornecedor mais barato numa corrida de 10 placas SMT pode adicionar custo
oculto significativo quando a BOM inclui peças consignadas, tecnologia mista
ou requisito de teste funcional.

## Fontes

- [PCB Manufacturing & Assembly Capabilities — JLCPCB](https://jlcpcb.com/capabilities/pcb-capabilities)
- [In what cases will there be charged extra? — JLCPCB](https://jlcpcb.com/help/article/in-what-cases-will-there-be-charged-extra)
- [JLCPCB vs PCBWay vs NextPCB: PCBA Comparison 2026 — NextPCB](https://www.nextpcb.com/blog/jlcpcb-vs-pcbway-vs-nextpcb-comparison-2026) (fonte interessada — ler com desconto)
- [PCBA Capability Comparison — NextPCB](https://www.nextpcb.com/blog/pcba-capability-comparison)
- [7 Best JLCPCB Alternatives for 2026 — AtlasPCB](https://www.atlaspcb.com/blog/jlcpcb-alternatives/)
- [JLCPCB vs PCBWay: Complete Comparison 2026](https://lead-pcb.com/blog/jlcpcb-vs-pcbway)
