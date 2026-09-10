# Integrações KiCad ↔ LLM — levantamento

**Data:** 12/08/2026 · **Contexto:** decidir sobre o que construir o `mcp-kicad`.

## O terreno: três formas de falar com o KiCad

### 1. IPC API (kicad-python / `kipy`) — a interface estável

Introduzida no KiCad 9.0. Protobuf sobre socket NNG — Unix domain socket em
Linux/macOS, named pipe no Windows. Projetada para **não quebrar** quando os
internos do KiCad são refatorados, e não exige que o KiCad seja compilado
contra Python.

Conexão:

```python
from kipy import KiCad

kicad = KiCad()          # lê KICAD_API_SOCKET e KICAD_API_TOKEN do ambiente
board = kicad.get_board()
```

Variáveis de ambiente que o KiCad injeta ao lançar um plugin:

| Variável | Conteúdo |
|---|---|
| `KICAD_API_SOCKET` | caminho do socket/pipe (default `api.sock` no tmpdir; PID sufixado se houver múltiplas instâncias) |
| `KICAD_API_TOKEN` | token de autenticação |

Superfície do `Board`: `get_tracks()`, `get_footprints()`, `get_vias()`,
`get_nets()`, `create_items()`, `update_items()`, `remove_items()`,
`get_stackup()`, `get_design_settings()`. Mutação é transacional via `Commit`.

> ⚠️ A doc oficial de `kicad-python` descreve a branch **main**, que está à
> frente do PyPI. No `kipy` 0.7.1 instalado, `KiCad` **não tem**
> `get_schematic`, `open_document` nem `close_document` — só
> `check_version`, `from_client`, `get_api_version`, `get_board`,
> `get_kicad_binary_path`, `get_open_documents`, `get_plugin_settings_path`,
> `get_project`, `get_text_as_shapes`, `get_text_extents`, `get_version`,
> `ping`, `run_action`. Medido em 12/08/2026 — ver
> [`probe-nightly-10.99.md`](probe-nightly-10.99.md).

**Limitações por versão — isso define a arquitetura:**

| | KiCad 9 | KiCad 10 | KiCad 11 |
|---|---|---|---|
| Editor de PCB | ✅ | ✅ | ✅ |
| Editor de esquemático | ❌ | ❌ | ❌ (pendente) |
| Plot / export via IPC | ❌ | ❌ | ✅ |
| Modo headless (`kicad-cli api-server`) | ❌ | ❌ | ✅ |

Ou seja: **no KiCad 10, IPC não exporta nada e não vê esquemático.** O
`kipy.KiCad(headless=True)` existe na biblioteca, mas depende do api-server
que só chega no 11.

### 2. `kicad-cli` — headless, o que fecha o buraco

Seis subcomandos: `fp`, `jobset`, `pcb`, `sch`, `sym`, `version`. Cobre
exatamente o que a IPC não cobre no KiCad 10:

```bash
kicad-cli pcb export gerbers placa.kicad_pcb
kicad-cli pcb drc --severity-error --exit-code-violations placa.kicad_pcb
kicad-cli sch erc --format json --exit-code-violations placa.kicad_sch
kicad-cli sch export netlist placa.kicad_sch
kicad-cli sch export bom placa.kicad_sch
```

O `--exit-code-violations` é o que torna DRC/ERC usável em automação: o
processo retorna código de erro quando há violação.

### 3. SWIG `pcbnew` — legado, tem data de morte

As bindings SWIG do editor de PCB ainda existem no KiCad 9 e 10, e são
**removidas no KiCad 11**. Qualquer coisa construída em cima disso vira
dívida com prazo. É por isso que os plugins consagrados (ex: `kicad-jlcpcb-tools`)
declaram suporte a 7/8/9 e não além.

## MCP servers existentes

| Projeto | Stack | Abordagem | Notas |
|---|---|---|---|
| [lamaalrajih/kicad-mcp](https://github.com/lamaalrajih/kicad-mcp) | Python 3.10+, MIT, ~495 ★ | parsing de arquivo + `kicad-cli` | O mais adotado. Projetos, análise de PCB, netlist, BOM, DRC com histórico, visualização, reconhecimento de padrões de circuito. Read-heavy. |
| [mixelpixx/KiCAD-MCP-Server](https://github.com/mixelpixx/KiCAD-MCP-Server) | — | MCP spec 2025-06-18 | Schemas de tool abrangentes, estado de projeto em tempo real. |
| [MJP-Sys/kicad-mcp-server](https://github.com/MJP-Sys/kicad-mcp-server) | — | fork/variante do acima | |
| [circuit-synth/mcp-kicad-sch-api](https://github.com/circuit-synth/mcp-kicad-sch-api) | Python | manipulação de esquemático | Ataca justamente o ponto cego da IPC. |
| KiCad MCP Pro (`oaslananka`) | — | tools + resources + prompts | Cobre schematic, PCB, validação, DFM e export de manufatura. |

Nenhum deles, pelo que apareceu no levantamento, é construído sobre a IPC API.
Todos operam por parsing de arquivo e/ou `kicad-cli`. Isso é consistente com o
fato de a IPC API não fazer export e não ver esquemático — o caminho por CLI
resolve mais casos com menos esforço.

**A consequência prática:** a IPC API não é um substituto do que esses projetos
fazem, é um complemento. Ela ganha em **editar a sessão viva** do PCB Editor
(mover footprint, roteamento, mexer em stackup com o KiCad aberto). Perde em
tudo que envolva export ou esquemático.

## Alternativas code-first (não-MCP)

Vale como referência de design, e possivelmente como camada delegada:

- **[atopile](https://github.com/atopile/atopile)** — linguagem `.ato` + compilador. Declarativo, validação profunda, módulos reutilizáveis, captura de intenção com equações, escolha paramétrica automática de discretos. Nativo com KiCad.
- **[circuit-synth](https://github.com/circuit-synth/circuit-synth)** — circuitos definidos em Python, posicionado explicitamente com Claude Code como parceiro de design. Exporta arquivos KiCad prontos para manufatura. Tem [skill de Claude Code](https://mcpmarket.com/tools/skills/ai-pcb-circuit-designer).
- **[SKiDL](https://devbisme.github.io/skidl/)** — biblioteca Python: declara partes, conecta pinos a nets, gera netlist KiCad. Tem [skills de Claude Code](https://github.com/nickkraakman/skidl-skills).

Todos são fortes em **gerar circuito do zero** e fracos em **editar board
existente**. O inverso do que a IPC API oferece.

## Plugins de fabricação (referência para o módulo `fab/`)

- **[Bouni/kicad-jlcpcb-tools](https://github.com/Bouni/kicad-jlcpcb-tools)** (MIT, KiCad 7/8/9) — o mais completo: gera Gerber, Excellon, BOM e CPL no formato JLCPCB, consulta a base de peças da JLCPCB de dentro do KiCad, atribui código LCSC direto no footprint, busca datasheet, gerencia correções de rotação de footprint, camadas `JLC_*` customizadas, hooks pré/pós-geração.
- **Fabrication Toolkit** (via Plugin and Content Manager) — um clique gera pasta `production/` com Gerber `.zip`, `bom.csv`, `positions.csv`, lista de designators e netlist IPC, já nas colunas que a JLCPCB espera.
- [wokwi/kicad-jlcpcb-bom-plugin](https://github.com/wokwi/kicad-jlcpcb-bom-plugin), [BOMKit Fab](https://forum.kicad.info/t/bomkit-fab-v0-1-0-kicad-plugin-for-jlcpcb-ready-bom-cpl-export/68155) — alternativas mais enxutas.

A correção de rotação de footprint é o detalhe que morde: a orientação que o
KiCad grava no CPL não é a que a montadora da JLCPCB espera para muitos
encapsulamentos. O `kicad-jlcpcb-tools` mantém uma tabela de correções — vale
portar essa tabela em vez de redescobrir na prática.

## Fontes

- [KiCad IPC API — dev docs](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/index.html)
- [For Add-on Developers — dev docs](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/index.html)
- [kicad-python (docs)](https://docs.kicad.org/kicad-python-main/kicad.html) · [PyPI](https://pypi.org/project/kicad-python/)
- [KiCad CLI 10.0](https://docs.kicad.org/10.0/en/cli/cli.html)
- [Version 10.0.0 Released](https://www.kicad.org/blog/2026/03/Version-10.0.0-Released/) · [KiCad 10.0.5](https://www.kicad.org/blog/2026/07/KiCad-10.0.5-Release/)
