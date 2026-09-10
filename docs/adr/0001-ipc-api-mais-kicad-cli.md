# ADR 0001 — Construir sobre IPC API + `kicad-cli`, não sobre SWIG

**Data:** 12/08/2026 · **Status:** aceito

## Contexto

Existem três formas de um processo externo falar com o KiCad, e três MCP servers
públicos já ocupam o espaço. Detalhamento completo em
[`docs/research-integracoes.md`](../research-integracoes.md).

Resumo do que restringe a decisão:

- As bindings SWIG do `pcbnew` são **removidas no KiCad 11**.
- A IPC API (Protobuf sobre NNG) é a interface declarada estável, mas no
  **KiCad 10 só existe no editor de PCB** — não exporta nada e não vê
  esquemático. Headless (`kicad-cli api-server`) só chega no KiCad 11.
- O `kicad-cli` cobre justamente o que falta: export de Gerber/drill/BOM/netlist,
  DRC e ERC headless, com `--exit-code-violations` para automação.
- Os MCP servers existentes são, na prática, parsing de arquivo + `kicad-cli`.
  Nenhum usa a IPC API.

## Decisão

Servidor novo, em Python, com **duas camadas de transporte**:

1. **`kicad_cli.py`** — wrapper de subprocess sobre `kicad-cli`. É o caminho
   primário. Funciona headless, funciona sem o KiCad aberto, cobre esquemático
   e todo o export. Todas as tools de manufatura e verificação passam por aqui.
2. **`ipc.py`** — wrapper sobre `kipy`. Caminho secundário, exigindo sessão viva
   do PCB Editor. Serve para inspecionar e mutar o board que o usuário está
   olhando: footprints, tracks, vias, nets, stackup.

Nenhum código toca SWIG `pcbnew`.

## Consequências

**Ganhos**

- Não morre no KiCad 11. Quando a IPC ganhar export e headless, a camada 2
  absorve responsabilidade da camada 1 sem quebrar as tools.
- Funciona hoje no KiCad 10, incluindo esquemático (via CLI).
- Automação real: CI pode rodar DRC/ERC sem display.

**Custos**

- Duas camadas para manter, com sobreposição parcial de responsabilidade.
- Mais trabalho inicial que forkar o `lamaalrajih/kicad-mcp`.
- A camada IPC fica ociosa em uso headless, e a camada CLI fica redundante
  quando o KiCad 11 chegar. Aceito: a alternativa é escolher uma das duas e
  perder metade dos casos de uso.

**Regra de roteamento** (para não virar bagunça): se a operação **lê ou escreve
arquivo**, vai por `kicad_cli`. Se a operação **precisa do estado da sessão
aberta**, vai por `ipc`. Nada de expor a mesma capacidade nos dois caminhos.

## Alternativas descartadas

- **Forkar `lamaalrajih/kicad-mcp`** (MIT, ~495 ★) — atalho grande, mas herda
  arquitetura de parsing de arquivo e as decisões dele. Fica como referência.
- **Camada fina delegando a `circuit-synth`/`atopile`/SKiDL** — forte para gerar
  circuito do zero, fraco para editar board existente, que é o caso de uso
  principal aqui (boards de eletrônica embarcada já existentes).
