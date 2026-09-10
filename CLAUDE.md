# mcp-kicad

MCP server para KiCad 10. Python, `uv`, layout `src/`.

## Regra de roteamento — a única que não pode ser quebrada

Definida no [ADR 0001](docs/adr/0001-ipc-api-mais-kicad-cli.md):

- Operação que **lê ou escreve arquivo** → `kicad_cli.py` (subprocess `kicad-cli`).
- Operação que precisa do **estado da sessão aberta** do PCB Editor → `ipc.py` (`kipy`).
- **Nunca** expor a mesma capacidade nos dois caminhos.

Nada de bindings SWIG do `pcbnew` — removidas no KiCad 11.

## Limites do KiCad 10 que causam confusão

Antes de "consertar" a camada IPC, confirme que não é limitação da versão:

- IPC existe **só no editor de PCB**. Esquemático não tem IPC nem no KiCad 11.
- **Não há plot/export via IPC** no KiCad 10 (chega no 11).
- **Não há headless** — `kipy.KiCad(headless=True)` depende de
  `kicad-cli api-server`, que só existe no KiCad 11.

`ipc.get_schematic()` e `ipc.export_via_ipc()` são barreiras intencionais: elas
levantam `UnsupportedByVersion` com a orientação certa. Não implemente por cima.

## Números de fabricação

Nunca inventar valor de capacidade. Todo número em `rules/jlcpcb/*.kicad_dru`
precisa de fonte em `rules/jlcpcb/README.md`, com data de verificação.

Distinção que o projeto inteiro depende: **capacidade da fábrica ≠ envelope sem
sobretaxa**. Os perfis `2layer` e `4layer` ficam no envelope isento; o perfil
`absoluto` é capacidade com sobretaxa e diz isso no cabeçalho do arquivo.

Valores derivados (o anel anular, por exemplo) devem estar marcados como
derivados, com a conta no comentário.

## Erros

Toda exceção exposta ao cliente MCP herda de `McpKicadError` e tem código
`MK-<área>-<n>`. As tools passam por `_guard()`, que devolve `{"error": ...}` em
vez de stack trace. Erro novo = classe nova em `errors.py`, com código.

## Convenções

- Comentário só onde a razão não é óbvia no código (ex: por que `check=False`
  no DRC — returncode 5 significa "achou violação", não falha).
- Comunicação e docs em PT-BR. Jargão consagrado em inglês (footprint, netlist,
  stackup, via, silkscreen).
- `ruff` com `line-length = 100`.

## Verificar

```bash
uv run ruff check . && uv run python -c "import mcp_kicad.server"
```

```bash
kicad-cli version
```
