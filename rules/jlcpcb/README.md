# Regras de fabricação — JLCPCB

Três perfis `.kicad_dru`, do mais seguro ao mais agressivo:

| Arquivo | Quando usar |
|---|---|
| `jlcpcb-2layer-standard.kicad_dru` | Placa de 2 camadas, 1 oz. Padrão para quase tudo. |
| `jlcpcb-4layer-standard.kicad_dru` | 4 camadas. Libera internas em 0.09 mm, mantém externas em 0.127 mm. |
| `jlcpcb-limites-absolutos.kicad_dru` | Só quando o design não couber nos anteriores **e** você aceitar sobretaxa. |

## Como aplicar

No PCB Editor: **File → Board Setup → Design Rules → Custom Rules**, colar o
conteúdo do arquivo. O KiCad grava as regras em `<projeto>.kicad_dru` ao lado
do `.kicad_pcb` — ou seja, o arquivo do projeto é sobrescrito, não linkado.

Pela CLI, para rodar DRC com as regras de um projeto:

```bash
kicad-cli pcb drc --severity-error --exit-code-violations placa.kicad_pcb
```

## A distinção que importa: capacidade ≠ preço de tabela

A JLCPCB publica dois conjuntos de números que costumam ser confundidos:

1. **Capacidade de processo** — o que a fábrica consegue produzir.
2. **Envelope sem sobretaxa** — o subconjunto que sai no preço anunciado.

Passar o DRC contra a capacidade não garante o preço. Sobretaxas conhecidas:

| Condição | Custo extra |
|---|---|
| Trilha ou espaço 3.0–3.5 mil, multicamada 4–8 camadas | +20% do pedido |
| Trilha ou espaço 3.0–3.5 mil, 10+ camadas | +30% do pedido |
| Furo de via < 0.3 mm **e** diâmetro ≤ 0.4 mm | taxa de via pequena |
| Trilha < 5 mil em 2 camadas | taxa de trilha fina |

A pegadinha da via: **furo < 0.3 mm só é taxado se o diâmetro for ≤ 0.4 mm**.
Por isso `0.2 mm furo / 0.45 mm diâmetro` é isento e `0.2 / 0.40` não é. Os
perfis padrão travam em 0.2/0.45 exatamente por isso.

## Capacidades de referência (1 oz salvo indicação)

| Parâmetro | Valor |
|---|---|
| Trilha/espaço, 1–2 camadas | 0.10 / 0.10 mm |
| Trilha/espaço, multicamada | 0.09 / 0.09 mm |
| Trilha/espaço, 2 oz (2 camadas) | 0.16 / 0.16 mm |
| Trilha/espaço, 2 oz (multicamada) | 0.15 / 0.15 mm |
| Trilha/espaço, 4.5 oz | 0.30 / 0.30 mm |
| Faixa de broca (multicamada) | 0.15 – 6.3 mm |
| Faixa de broca (1 camada) | 0.30 – 6.3 mm |
| Furo de via | mín 0.15 mm, preferencial 0.20 mm |
| Diâmetro de via | mín 0.25 mm |
| Anel anular PTH | ≥ 0.20 mm (1 oz), ≥ 0.254 mm (2 oz) |
| Anel anular NPTH | ≥ 0.45 mm |
| Camadas | 1 – 32 |
| Espessura | 0.4 – 4.5 mm (padrão: 0.4/0.6/0.8/1.0/1.2/1.6/2.0) |
| Cobre | 0.5 – 4.5 oz |
| Tamanho máximo | 1020 × 600 mm (2 camadas); 656 × 586 mm (6+) |
| Tamanho mínimo | 3 × 3 mm |
| Ponte de máscara | 0.10 mm (1 oz, cores padrão) |
| Silkscreen: linha / altura | ≥ 0.15 mm / ≥ 1.0 mm |
| Folga de borda roteada | ≥ 0.2 mm |
| Folga de V-cut | ≥ 0.4 mm |
| Furo castellated | ≥ 0.5 mm |
| Controle de impedância | ±10% (multicamada) |

## Sobre o anel anular

A JLCPCB publica "anel anular PTH ≥ 0.20 mm". Esse número **não pode ser por
lado**: a via que eles mesmos recomendam como isenta (furo 0.2 / diâmetro
0.45) tem apenas `(0.45 − 0.20) / 2 = 0.125 mm` por lado. O KiCad interpreta
`annular_width` por lado.

Os perfis usam **0.13 mm por lado** (padrão) e **0.05 mm** (absoluto), valores
derivados das vias publicadas, não copiados da tabela. Se você precisar apertar
isso, confirme a base do número com a fábrica antes.

## Fontes

- [PCB Manufacturing & Assembly Capabilities — JLCPCB](https://jlcpcb.com/capabilities/pcb-capabilities)
- [In what cases will there be charged extra? — JLCPCB](https://jlcpcb.com/help/article/in-what-cases-will-there-be-charged-extra)
- [PCB Design Rules and Guidelines — JLCPCB](https://jlcpcb.com/blog/pcb-pricing-breakdown)
- [KiCad DRC rules for JLCPCB (gist darkxst)](https://gist.github.com/darkxst/f713268e5469645425eed40115fb8b49) — base da estrutura de regras
- [MuratovAS/KiCad-DRC-Rules](https://github.com/MuratovAS/KiCad-DRC-Rules) — regras 4 camadas com PCBs de teste PASS/FAIL
- [labtroll/KiCad-DesignRules](https://github.com/labtroll/KiCad-DesignRules)

**Verificado em:** 12/08/2026. A JLCPCB muda capacidade e tabela de sobretaxa
sem aviso — revalidar antes de um lote grande.
