# mcp-kicad

MCP server para KiCad 10, com as regras de fabricação da JLCPCB embutidas.

Duas camadas de transporte, por decisão de arquitetura ([ADR 0001](docs/adr/0001-ipc-api-mais-kicad-cli.md)):

- **`kicad-cli`** — caminho primário. Headless, sem precisar do KiCad aberto,
  cobre esquemático e todo o export.
- **IPC API (`kipy`)** — caminho secundário. Exige o PCB Editor aberto, serve
  para inspecionar e mutar a sessão viva.

Nada de bindings SWIG do `pcbnew`: elas são **removidas no KiCad 11**.

**Status:** tools de arquivo (DRC, ERC, export), perfis JLCPCB e BOM/CPL com
correção de rotação implementados e **verificados contra o KiCad 10.0.5**
(demo `complex_hierarchy` e um board real de produção). A camada IPC tem só
`board_summary` e ainda não foi exercitada — ver
[probe do nightly](docs/probe-nightly-10.99.md).

## Instalar o KiCad

PPA oficial, KiCad 10.0.5 (build para Ubuntu 26.04 resolute):

```bash
sudo add-apt-repository --yes ppa:kicad/kicad-10.0-releases
```

```bash
sudo apt update && sudo apt install --yes kicad
```

Verificar:

```bash
kicad-cli version
```

> O `kicad` do repositório universe do Ubuntu 26.04 é 9.0.8 — funciona, mas
> perde time-domain tuning, editor gráfico de DRC e design variants do 10.
>
> Flatpak (`org.kicad.KiCad`, também 10.0.5) instala sem sudo, mas o sandbox
> isola o socket IPC e os paths de projeto. Se você usar Flatpak, aponte
> `MCP_KICAD_CLI="flatpak run --command=kicad-cli org.kicad.KiCad"`.

## Instalar o servidor

```bash
cd mcp-kicad && uv sync
```

Registrar no Claude Code:

```bash
claude mcp add kicad -- uv --directory /path/to/mcp-kicad run mcp-kicad
```

Se houver mais de uma instalação do KiCad, fixe qual o MCP usa:

```bash
export MCP_KICAD_CLI=/usr/bin/kicad-cli
```

## Tools

### Ambiente
| Tool | O que faz |
|---|---|
| `kicad_version` | Versão e instalação em uso. Primeira coisa a chamar quando algo falha. |

### Verificação (arquivo → `kicad-cli`)
| Tool | O que faz |
|---|---|
| `pcb_drc` | DRC no `.kicad_pcb`, relatório JSON. Usa o `.kicad_dru` do projeto. |
| `sch_erc` | ERC no `.kicad_sch`, relatório JSON. |

### Export (arquivo → `kicad-cli`)
| Tool | O que faz |
|---|---|
| `pcb_export_gerbers` | Gerbers para um diretório. |
| `pcb_export_drill` | Excellon. |
| `sch_export_bom` | BOM CSV (formato genérico do KiCad). |
| `sch_export_netlist` | Netlist. |

### JLCPCB
| Tool | O que faz |
|---|---|
| `jlcpcb_list_profiles` | Perfis disponíveis e o que cada um assume. |
| `jlcpcb_show_rules` | Conteúdo de um perfil, sem escrever nada. |
| `jlcpcb_apply_rules` | Grava o perfil como `<projeto>.kicad_dru`. **Sobrescreve** o existente (deixa `.bak`). |
| `jlcpcb_export_fab_package` | Gerber + Excellon zipados no layout de upload da JLC. |
| `jlcpcb_export_bom` | BOM de SMT assembly, colunas `Designator,Footprint,Quantity,Value,LCSC Part #`. |
| `jlcpcb_export_cpl` | CPL com correção de rotação por encapsulamento. Ver [`docs/rotacoes-cpl.md`](docs/rotacoes-cpl.md). |
| `jlcpcb_fetch_rotations` | Baixa a tabela de correção de rotação (GPL-3.0, não embarcada) para o cache local. |

### Sessão viva (IPC)
| Tool | O que faz |
|---|---|
| `board_summary` | Contagem de footprints/tracks/vias/nets no board **aberto**. |

## Regras JLCPCB

Três perfis em [`rules/jlcpcb/`](rules/jlcpcb/):

| Perfil | Uso |
|---|---|
| `2layer` | 2 camadas, 1 oz. Default. |
| `4layer` | 4 camadas: internas 0.09 mm, externas 0.127 mm. |
| `absoluto` | Limites físicos da fábrica — **com sobretaxa**. |

Os perfis padrão ficam dentro do envelope **sem sobretaxa**, que é mais estreito
que a capacidade anunciada. A pegadinha principal: a JLCPCB cobra extra por via
com furo < 0.3 mm **e** diâmetro ≤ 0.4 mm — então `0.2/0.45` é grátis e
`0.2/0.40` não é. Tabela completa de sobretaxas e a derivação do anel anular em
[`rules/jlcpcb/README.md`](rules/jlcpcb/README.md).

## Limites conhecidos do KiCad 10

Isto não é bug do servidor, é o estado da IPC API:

| | KiCad 10 | KiCad 11 |
|---|---|---|
| IPC no editor de PCB | ✅ | ✅ |
| IPC no esquemático | ❌ | ❌ |
| Plot/export via IPC | ❌ | ✅ |
| Headless (`kicad-cli api-server`) | ❌ | ✅ |

Por isso tudo que é export e esquemático passa por `kicad-cli`. As funções
`ipc.get_schematic()` e `ipc.export_via_ipc()` existem só para dar erro
explicativo em vez de `AttributeError`.

## Roadmap

- [x] BOM/CPL no formato JLCPCB, com correção de rotação de footprint (ver [`docs/rotacoes-cpl.md`](docs/rotacoes-cpl.md)). Resolução automática de código LCSC (busca por MPN) ainda não implementada — hoje depende do valor já vir preenchido no esquemático.
- [ ] Mutação de board via IPC (mover footprint, editar stackup)
- [ ] Testes com projeto KiCad de fixture
- [ ] Checagem de DFM além do DRC (densidade, acessibilidade de teste)

## Documentação

- [ADR 0001 — IPC API + kicad-cli](docs/adr/0001-ipc-api-mais-kicad-cli.md)
- [Correção de rotação para CPL (JLCPCB)](docs/rotacoes-cpl.md)
- [Probe do nightly 10.99 — o que a IPC já entrega](docs/probe-nightly-10.99.md)
- [Levantamento de integrações KiCad ↔ LLM](docs/research-integracoes.md)
- [Levantamento de fabricantes](docs/research-fabricantes.md)
- [Regras JLCPCB](rules/jlcpcb/README.md)

## Licença

MIT.
