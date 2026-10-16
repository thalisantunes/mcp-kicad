# mcp-kicad

<!-- mcp-name: io.github.thalisantunes/mcp-kicad -->

> **Nota sobre o nome no PyPI:** o pacote é distribuído como `mcp-kicad10`.
> O nome `mcp-kicad` no PyPI pertence a [daedalus/mcp-kicad](https://github.com/daedalus/mcp-kicad),
> um projeto diferente. O módulo importável continua `mcp_kicad`.

MCP server para KiCad 10, com as regras de fabricação da JLCPCB embutidas.

Duas camadas de transporte, por decisão de arquitetura ([ADR 0001](docs/adr/0001-ipc-api-mais-kicad-cli.md)):

- **`kicad-cli`** — caminho primário. Headless, sem precisar do KiCad aberto,
  cobre esquemático e todo o export.
- **IPC API (`kipy`)** — caminho secundário. Exige o PCB Editor aberto, serve
  para inspecionar e mutar a sessão viva.

Nada de bindings SWIG do `pcbnew`: elas são **removidas no KiCad 11**.

**Status:** 16 tools. As de arquivo (DRC, ERC, export), os perfis JLCPCB e o
BOM/CPL com correção de rotação estão implementados e **verificados contra o
KiCad 10.0.5**, nos testes de integração que rodam nos demos `cm5_minima` e
`complex_hierarchy`. A camada IPC tem só `board_summary` e ainda não foi
exercitada com um KiCad vivo — ver [probe do nightly](docs/probe-nightly-10.99.md).

## Instalar o KiCad

### macOS

Duas formas, ambas instalam o KiCad 10 em `/Applications/KiCad`:

- o DMG oficial, universal (Intel e Apple Silicon, ~1,4 GB), em
  [kicad.org/download/macos](https://www.kicad.org/download/macos/);
- `brew install --cask kicad`.

O `kicad-cli` fica dentro do app:

```
/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
```

Com o cask do Homebrew existe um symlink dele no PATH; com o DMG não. **Não
precisa configurar nada:** o servidor procura nesse caminho (e em
`~/Applications/KiCad/...`) sozinho quando o `kicad-cli` não está no PATH. Só
defina `MCP_KICAD_CLI` quando houver mais de uma instalação e você quiser fixar
qual delas o MCP usa.

Verificar:

```bash
/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli version
```

### Ubuntu

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

### Flatpak

`org.kicad.KiCad` (também 10.0.5) instala sem sudo, mas o sandbox pode limitar o
acesso a pastas de projeto fora do seu home. Aponte o servidor para ele:

```bash
export MCP_KICAD_CLI="flatpak run --command=kicad-cli org.kicad.KiCad"
```

## Instalar o servidor

Precisa do [uv](https://docs.astral.sh/uv/) (`brew install uv` no macOS).

```bash
git clone https://github.com/thalisantunes/mcp-kicad.git
cd mcp-kicad
uv sync
```

O `uv sync` é **obrigatório antes de registrar**: sem ele o primeiro start baixa
as dependências e o cliente pode estourar o timeout de inicialização.

### Registrar no Claude Code

Dentro da pasta do clone:

```bash
claude mcp add kicad -- uv --directory "$(pwd)" run mcp-kicad
```

O escopo padrão é `local`: o servidor só carrega no diretório onde o comando
rodou. Acrescente `--scope user` para valer em qualquer projeto. Para fixar o
`kicad-cli`, passe a variável ao servidor com `-e` (um `export` no shell só vale
se feito antes de abrir o `claude`):

```bash
claude mcp add kicad -e MCP_KICAD_CLI=/caminho/do/kicad-cli -- uv --directory "$(pwd)" run mcp-kicad
```

Quando o `mcp-kicad10` estiver publicado no PyPI, dispensa o clone:

```bash
claude mcp add kicad -- uvx mcp-kicad10
```

### Registrar no Claude Desktop

Edite o arquivo de configuração (macOS:
`~/Library/Application Support/Claude/claude_desktop_config.json`) e rode
`uv sync` na pasta do clone **antes** de abrir o Desktop:

```json
{
  "mcpServers": {
    "kicad": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["--directory", "/Users/SEU_USUARIO/mcp-kicad", "run", "mcp-kicad"]
    }
  }
}
```

O Desktop não herda o PATH do shell e não expande `~` nem `$HOME`: `command`
precisa ser o caminho **absoluto** do `uv` (descubra com `command -v uv`):
`/opt/homebrew/bin/uv` com Homebrew no Apple Silicon, `/usr/local/bin/uv` no
Intel, `/home/SEU_USUARIO/.local/bin/uv` com o instalador oficial no Linux. O
mesmo vale para `uvx`. Para fixar o `kicad-cli`, acrescente
`"env": { "MCP_KICAD_CLI": "/caminho/do/kicad-cli" }` ao servidor.

## Habilitar a IPC API

Só o `board_summary` usa a IPC; todas as outras tools funcionam sem o KiCad
aberto. A API do KiCad vem **desligada**. Para ligar:

1. No KiCad, abra **Preferences → Plugins** e marque **Enable KiCad API** (é o
   nome em inglês; vale na hora, sem reiniciar).
2. Abra o board no **PCB Editor** — o esquemático não tem IPC.

No macOS o KiCad cria o socket em `/tmp/kicad/api.sock`; no Linux, em
`<tmp>/kicad/api.sock`, normalmente o mesmo caminho. O servidor usa o socket
padrão do kipy, que cobre esse caso (e trata Windows e Flatpak à parte). Uma
segunda instância do KiCad cria `api-<pid>.sock`, e um `TMPDIR` customizado muda
o diretório: nesses casos aponte `KICAD_API_SOCKET` para o socket certo.

Se a conexão falha, o erro `MK-IPC-001` traz a causa, o checklist acima e o
socket que foi tentado.

## Variáveis de ambiente

| Variável | Para que serve | Padrão |
|---|---|---|
| `MCP_KICAD_CLI` | Caminho do `kicad-cli` (com espaço, sem precisar de aspas) ou um comando completo, como o do Flatpak. Vence o PATH. | PATH, depois `/Applications/KiCad/KiCad.app/...`, `~/Applications/KiCad/KiCad.app/...`, `C:\Program Files\KiCad\10.0\bin\kicad-cli.exe` e Flatpak |
| `KICAD_API_SOCKET` | Socket da IPC, por exemplo `ipc:///tmp/kicad/api-1234.sock`. O prefixo `ipc://` é acrescentado se faltar. | o socket padrão do kipy: `ipc:///tmp/kicad/api.sock` no macOS e no Linux (Windows e Flatpak usam outro) |
| `KICAD_API_TOKEN` | Token da sessão da IPC. O KiCad injeta em plugins que ele mesmo lança; fora disso quase nunca é preciso. | vazio |
| `XDG_CACHE_HOME` | Base do cache da tabela de rotação do CPL, em `$XDG_CACHE_HOME/mcp-kicad/`. | `~/.cache` |

## Tools

As tools de arquivo gravam em qualquer caminho que receberem; quem confirma
cada chamada é o cliente MCP. Erros chegam ao cliente como erro de tool
(`isError`), com código `MK-<área>-<n>` e, quando o `kicad-cli` falha, o final
do stderr dele.

### Ambiente
| Tool | O que faz |
|---|---|
| `kicad_version` | Versão e instalação em uso. Primeira coisa a chamar quando algo falha. |

### Verificação (arquivo → `kicad-cli`)
| Tool | O que faz |
|---|---|
| `pcb_drc` | DRC no `.kicad_pcb`. Devolve um **resumo**: `violations`, `by_severity`, `first` (até 20, erros primeiro), `unconnected_items`, `schematic_parity`, `coordinate_units` e `report_path`, o JSON completo do KiCad. Sem `report_path` informado, o JSON vai para um diretório temporário, nunca ao lado do projeto; ele fica lá depois do DRC (e é removido se o DRC falhar). O `report_path` que você passar precisa terminar em `.json`. Usa o `.kicad_dru` do projeto. |
| `sch_erc` | ERC no `.kicad_sch`, no mesmo formato de resumo, com a folha (`sheet`) de cada item. |

### Export (arquivo → `kicad-cli`)
| Tool | O que faz |
|---|---|
| `pcb_export_gerbers` | Gerbers para um diretório. |
| `pcb_export_drill` | Excellon. |
| `pcb_export_step` | STEP do board. Com `subst_models` (padrão) os componentes com modelo VRML entram pelo STEP de mesmo nome. Ver [Fluxo gabinete](#fluxo-gabinete). |
| `sch_export_bom` | BOM CSV (formato genérico do KiCad). |
| `sch_export_netlist` | Netlist. |

### JLCPCB
| Tool | O que faz |
|---|---|
| `jlcpcb_list_profiles` | Perfis disponíveis e o que cada um assume. |
| `jlcpcb_show_rules` | Conteúdo de um perfil, sem escrever nada. |
| `jlcpcb_apply_rules` | Grava o perfil como `<projeto>.kicad_dru` e **sobrescreve** o existente. O original vai para `.kicad_dru.bak` na primeira vez e esse `.bak` nunca é sobrescrito; `backup_preexistente: true` no retorno avisa quando ele já existia. |
| `jlcpcb_export_fab_package` | Gerber + Excellon zipados no layout de upload da JLC, em origem absoluta. Pode rodar de novo no mesmo diretório; o zip anterior só é trocado se a execução terminar bem. |
| `jlcpcb_export_bom` | BOM de SMT assembly, colunas `Designator,Footprint,Quantity,Value,LCSC Part #`. Só agrupa componentes com o mesmo Value, Footprint e LCSC, então códigos diferentes nunca se juntam. Itens com o mesmo Value e Footprint **sem** LCSC compartilham uma linha, separada dos que têm código; `missing_lcsc` conta linhas, não componentes. |
| `jlcpcb_export_cpl` | CPL com correção de rotação por encapsulamento, em origem absoluta. Ver [`docs/rotacoes-cpl.md`](docs/rotacoes-cpl.md). |
| `jlcpcb_fetch_rotations` | Baixa a tabela de correção de rotação (GPL-3.0, não embarcada, fixa num commit) para o cache local. |

Gerber, drill e CPL do pacote usam a **mesma origem, a absoluta** (a aux origin
do board é ignorada de propósito), para que as posições do CPL caiam sobre os
Gerbers. Detalhes em [`docs/rotacoes-cpl.md`](docs/rotacoes-cpl.md).

### Sessão viva (IPC)
| Tool | O que faz |
|---|---|
| `board_summary` | Contagem de footprints/tracks/vias/nets no board **aberto**. Exige [habilitar a IPC API](#habilitar-a-ipc-api). |

## Fluxo gabinete

1. `pcb_export_step` gera o STEP da placa com os componentes.
2. No [mcp-freecad](https://github.com/thalisantunes/mcp-freecad), `doc_inspect` abre o STEP e devolve as dimensões e o `bbox` de cada objeto, limitado aos primeiros 100 (`objects_total` diz quantos existem).
3. Modele o gabinete em volta da placa no FreeCAD.

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

## Desenvolvimento

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest -q                       # tudo
uv run pytest -q -m "not integration"  # o que o CI roda
```

Os testes marcados `integration` usam o `kicad-cli` real e cópias dos demos do
KiCad; pulam sozinhos quando o `kicad-cli` não existe.

## Roadmap

- [x] BOM/CPL no formato JLCPCB, com correção de rotação de footprint (ver [`docs/rotacoes-cpl.md`](docs/rotacoes-cpl.md)). Resolução automática de código LCSC (busca por MPN) ainda não implementada — hoje depende do valor já vir preenchido no esquemático.
- [ ] Mutação de board via IPC (mover footprint, editar stackup)
- [ ] Testes com projeto KiCad de fixture
- [ ] Checagem de DFM além do DRC (densidade, acessibilidade de teste)

## Documentação

- [Changelog](CHANGELOG.md)
- [ADR 0001 — IPC API + kicad-cli](docs/adr/0001-ipc-api-mais-kicad-cli.md)
- [Correção de rotação para CPL (JLCPCB)](docs/rotacoes-cpl.md)
- [Probe do nightly 10.99 — o que a IPC já entrega](docs/probe-nightly-10.99.md)
- [Levantamento de integrações KiCad ↔ LLM](docs/research-integracoes.md)
- [Levantamento de fabricantes](docs/research-fabricantes.md)
- [Regras JLCPCB](rules/jlcpcb/README.md)

## Licença

MIT.
