# Correção de rotação de footprint no CPL (JLCPCB)

O `kicad-cli pcb export pos` grava no CPL a rotação do footprint como ela
está no `.kicad_pcb`. A JLCPCB (assim como outras montadoras que herdam a
mesma convenção) espera outro ângulo para vários encapsulamentos — SOT-23,
DFN, QFN, SOP... — porque a orientação "0°" da pegada JLCPCB nem sempre bate
com a orientação "0°" do footprint padrão do KiCad. Sem correção, a máquina
de pick-and-place gira o componente errado e a solda sai invertida (pino 1
no lugar do pino oposto, por exemplo).

## De onde vem a tabela

A lógica é uma **porta de ideia**, não uma cópia de código, do
[`matthewlai/JLCKicadTools`](https://github.com/matthewlai/JLCKicadTools)
(usado internamente pelo [`kicad-jlcpcb-tools`](https://github.com/Bouni/kicad-jlcpcb-tools)),
arquivo `jlc_kicad_tools/cpl_rotations_db.csv`, no commit `500a9ff`.

Esse arquivo é **GPL-3.0**. O `mcp-kicad` é MIT, então o CSV **não é
embarcado no repo** — embarcar violaria a licença do projeto. Em vez disso:

- `rotations.fetch_table()` (tool `jlcpcb_fetch_rotations`) baixa o CSV
  direto do GitHub com `urllib.request` (stdlib, sem dependência nova) e
  grava em `$XDG_CACHE_HOME/mcp-kicad/cpl_rotations_db.csv`, com fallback
  `~/.cache/mcp-kicad/` quando `XDG_CACHE_HOME` não está definida.
- `rotations.load_table()` só lê esse cache. Se não existir, o erro
  `MK-FAB-002` (`RotationTableUnavailable`) pede para rodar o fetch antes.
- O CSV baixado **nunca é commitado**. Se aparecer dentro do repo, é bug —
  ele deveria estar só no cache do usuário.

A lógica de matching (`find_correction`) e a matemática de correção
(`corrected_rotation`), em `src/mcp_kicad/fab/rotations.py`, foram escritas
do zero para este projeto.

## A matemática, na ordem exata das operações

Para cada linha do CPL, com `rot` = ângulo cru do `kicad-cli` e `side` ∈
{top, bottom}:

1. **Espelha para bottom, se for o caso**: `rot = (180 - rot) % 360`.
2. **Aplica a correção da tabela, se houve match**: `rot = (rot +
   correction.rotation) % 360`.
3. **Normaliza para `[0, 360)`**, sempre — com ou sem correção, com ou sem
   flip de bottom.

A ordem importa: espelhar depois de corrigir dá um resultado diferente de
corrigir depois de espelhar, porque a correção da tabela é definida em
relação à orientação do footprint como desenhado (before qualquer
espelhamento de montagem).

### Matching contra a tabela

`find_correction(footprint, table)` testa em dois passes:

1. **Ancorado**: cada padrão da tabela é testado como `re.search(f"(?:{padrão})$", footprint)` — o footprint precisa **terminar** exatamente onde o padrão termina. Isso favorece naturalmente a entrada mais específica: `^SOT-23-5$` bate com "SOT-23-5", mas `^SOT-23` sozinho (sem `$` no próprio padrão) só bate aqui se o footprint for literalmente "SOT-23".
2. **Cru** (fallback, só roda se o passe 1 não achou nada): `re.search(padrão, footprint)` sem âncora de fim — pega o caso comum de prefixo/substring, como `^SOT-23` casando com "SOT-23-5".

A primeira entrada que casar em cada passe vence — não acumula correções de
múltiplas linhas. Um padrão com regex inválido é ignorado (não derruba o
matching das demais linhas).

**Regressão verificada:** `^DFN-` (com hífen) **não** casa com
`DFN2510A-10_...` — falta o hífen logo após "DFN". É fácil escrever esse
matching errado com substring simples e aplicar correção onde não deveria.

## Duas armadilhas do `kicad-cli` já verificadas contra o KiCad 10.0.5

Verificadas manualmente antes de escrever este código — não redescobrir:

1. **O `kicad-cli` já inverte o eixo Y e já subtrai a aux origin** quando
   `--use-drill-file-origin` está ativo. Exemplo real: aux origin
   `(78.15, 81.35)`, footprint C503 em `at=(84.61, 74.47)` no
   `.kicad_pcb`, saída do CLI `PosX=6.46, PosY=6.88` — exatamente
   `(84.61−78.15, 81.35−74.47)`. **Não reaplique nenhuma transformação de Y
   por cima disso.** Fazer isso monta a placa espelhada.
2. **O `kicad-cli` NÃO espelha o ângulo do bottom.** Ele exporta o ângulo
   cru do footprint, como gravado no `.kicad_pcb`, mesmo para componentes em
   `B.Cu`. A transformação de bottom (passo 1 da matemática acima) é
   responsabilidade deste módulo, não do `kicad-cli`.

## Valores conferidos contra o board real (`CM5_MINIMA_3`)

| Designator | Package | Side | Rot cru | Correção | Rot final |
|---|---|---|---:|---:|---:|
| U405 | SOT-23-5 | top | 180 | −90 | 90 |
| U701 | SOT-23-6 | top | 180 | −90 | 90 |
| U602 | SOT-353_SC-70-5 | top | 180 | +180 | 0 |
| U702 | DFN-8-1EP_2x2mm... | top | −90 | +270 | 180 |
| D401/D402 | DFN2510A-10... | top | 180 | nenhuma (`^DFN-` não casa) | 180 |
| D403 | DFN2510A-10... | **bottom** | 0 | nenhuma | 180 (só flip de bottom) |

## Limitação conhecida — não corrigida de propósito

O `kicad-cli` usa a **origem do footprint** (o ponto de referência do
footprint no editor) como posição gravada no CPL. Já o
`kicad-jlcpcb-tools` calcula o **centro da bounding box dos pads**. Para a
maioria dos footprints simétricos os dois coincidem, mas para footprints
onde a origem foi deslocada do centro do corpo (comum em conectores e
footprints customizados), os valores de `Mid X`/`Mid Y` divergem entre as
duas ferramentas.

Corrigir isso exigiria parsear o `.kicad_pcb` para achar a geometria dos
pads — o que quebraria o [ADR 0001](adr/0001-ipc-api-mais-kicad-cli.md)
(tudo aqui é arquivo → `kicad-cli`, nunca parsing manual do board). Por
isso: não corrigido, só documentado. Confira manualmente footprints
assimétricos antes de mandar o CPL para montagem.

### Offset da tabela: lido, mas nunca aplicado

Um punhado de linhas do CSV de origem tem offset X/Y além da rotação, por
exemplo:

```
"^USB_C_Receptacle_XKB_U262-16XN-4BVC11",0,1.44,0
"^PinHeader_2x05_P1\.27mm_Vertical",90,2.54,0.635
```

`rotations.Correction` carrega `offset_x`/`offset_y` e `load_table()` os lê
normalmente, mas **`export_cpl` nunca os aplica em `Mid X`/`Mid Y`** — é
decisão deliberada, pela mesma raiz da limitação acima: o
`kicad-jlcpcb-tools` calibrou esses offsets sobre o centro da bounding box
dos pads, e este módulo grava a posição na convenção de origem do
footprint que o `kicad-cli` entrega. Não há confirmação de que os dois
valores usam a mesma convenção — deslocar um componente 1.44 mm com base
em palpite é pior do que não deslocar.

Quando um footprint casa com uma entrada da tabela que tem offset
diferente de zero, `export_cpl` ainda aplica a correção de **rotação**
normalmente, mas marca a entrada correspondente em `corrections_applied`
com `offset_pendente: {offset_x, offset_y}` e o retorno vem com `warning`
nomeando os designators afetados — para deixar explícito que, para eles, só
a rotação foi corrigida e a posição precisa de conferência manual antes da
montagem.
