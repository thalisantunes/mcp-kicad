# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
versões seguem [SemVer](https://semver.org/lang/pt-BR/).

## [0.3.0] — 2026-10-28

### Adicionado

- Tool MCP e subcomando `mcp-kicad validate`: grafo de checks por estágio (S0 intake,
  S1 ERC, S2 DRC e paridade, S3 DRC com perfil JLCPCB em cópia, S4 Gerber/drill, S5
  plugins opcionais), perfis `dev`/`release`, gate `stage`/`full`/`strict`, snapshot
  somente leitura e saídas `report.json`, `REPORT.md`, `next_actions.json` e `junit.xml`.
  Exit codes 0/1/2/3/130. Erros `MK-VAL-001/002/003`. Ver ADR 0002.
- Config do `validate` em duas origens: plugins de agente (`kind: agent`/`command`) e rebaixamento de
  checks builtin só pela config do usuário (`$MCP_KICAD_REVIEWERS` ou
  `~/.config/mcp-kicad/reviewers.json`); o `.mcp-kicad/validate.json` do projeto só ajusta
  `fab_profile`, `skip`/`until` e checks não-builtin. Overrides aceitos vão em `report.json`/`REPORT.md`.
- `kicad_cli.erc_report` e `kicad_cli.drc_report` (relatório cru), usados por `sch_erc`/`pcb_drc` e pelo `validate`.
- Adaptador de revisão `llm_json` (comando local, bloco JSON, até 2 reparos), inativo por padrão; roda em
  sessão própria com ambiente mínimo e encerra o grupo no timeout.
- Segurança do snapshot: links simbólicos para fora do projeto são recusados (`MK-VAL-002`); `latest` é
  publicado de forma atômica; na tool MCP, `out` fica restrito ao diretório do projeto.

### Alterado

- `mcp-kicad` agora tem subcomandos `serve` (padrão, comportamento anterior) e `validate`.

## [0.2.0] — 2026-10-16

### Atenção ao atualizar

- **CPL em origem absoluta.** `jlcpcb_export_cpl` deixou de usar a aux origin do
  board. Os valores de `Mid X`/`Mid Y` mudam em boards com aux origin diferente
  de zero (e `Mid Y` sai com o sinal do Gerber, negativo abaixo da origem da
  página). Regere o CPL e o pacote juntos.
- **`pcb_drc` e `sch_erc` devolvem um resumo**, não o relatório inteiro. O JSON
  completo fica em `report_path`.
- **Erros chegam como erro de tool** (`isError=true`, mensagem `[MK-...]`), não
  mais como `{"error": ...}` com sucesso.
- **`jlcpcb_export_bom` pode ter mais linhas**: componentes com LCSC diferente
  (ou sem LCSC) deixam de ser agrupados com os que têm outro código.

### Corrigido

- Pacote JLCPCB: Gerber e drill saíam em origem absoluta e o CPL na aux origin,
  então em boards com aux origin diferente de zero o CPL não caía sobre os
  Gerbers do próprio pacote. Agora os três saem na origem absoluta.
- `jlcpcb_export_fab_package` falhava na segunda execução no mesmo diretório e,
  se só um arquivo fosse "novo", sobrescrevia o zip bom por um zip parcial. Agora
  exporta para um subdiretório novo, zipa só o que gerou e troca o zip de uma vez.
- `jlcpcb_apply_rules` perdia as regras originais do usuário na segunda chamada
  (o `.bak` era sobrescrito). O `.bak` só é criado se não existir e o retorno traz
  `backup` e `backup_preexistente`.
- O stderr do `kicad-cli` nunca chegava ao agente. Falhas agora incluem as
  últimas 15 linhas, e exceções que não são do projeto viram `[MK-INT-001]` com
  tipo e mensagem em vez de "Error executing tool".
- BOM: o agrupamento só por Value e Footprint juntava LCSC diferentes em
  `"C15850,C99999"` e escondia um item sem LCSC dentro do grupo. Agora o LCSC
  também separa os grupos: itens sem código ficam numa linha separada da dos que
  têm código, e essa linha entra em `missing_lcsc` (que conta linhas, não
  componentes).
- DRC/ERC gravavam `.drc.json`/`.erc.json` ao lado do projeto (e falhavam em
  pasta somente leitura) e devolviam centenas de KiB. O relatório vai para um
  diretório temporário, que fica depois do sucesso (o resumo devolve o caminho) e
  é removido se a execução falhar, ou para `report_path`. Esse caminho precisa
  terminar em `.json` e não pode ser o arquivo de entrada, que o `kicad-cli`
  sobrescreveria.
- IPC: a mensagem de diagnóstico de `connect()` nunca aparecia, porque o `kipy`
  conecta de forma preguiçosa, e toda falha virava "nenhum board aberto". A
  conexão agora é forçada; `KICAD_API_SOCKET` sem `ipc://` ganha o prefixo.
- `MCP_KICAD_CLI` com espaço no caminho era quebrado em dois argumentos. O
  `kicad-cli` ausente (`OSError`) agora vira `MK-CLI-001`, o filho não herda
  mais o stdin do servidor e a saída dele é lida em UTF-8.

### Adicionado

- macOS: o `kicad-cli` é achado em `/Applications/KiCad/KiCad.app/...` e
  `~/Applications/...` sem configurar o PATH (também o caminho padrão do Windows).
  A mensagem de "não encontrado" lista onde procurou e dá um exemplo.
- Tool `pcb_export_step`, a ponte para o CAD mecânico (gabinete no FreeCAD). Uma
  saída que já existe é sobrescrita (`--force`).
- Comando `mcp-kicad10`, para `uvx mcp-kicad10` (o `mcp-kicad` continua).
- CI de testes (`.github/workflows/test.yml`) e testes de integração com o
  `kicad-cli` real, marcados `integration`, que pulam sozinhos sem o KiCad.
- README: instalação no macOS, registro no Claude Code e no Claude Desktop,
  como habilitar a IPC API, variáveis de ambiente e o fluxo do gabinete.

### Alterado

- Dependências com teto: `mcp>=2.0.0,<3` e `kicad-python>=0.7.1,<1`.
- A tabela de rotação do CPL é baixada do commit documentado
  (`500a9fff5998edb23f9bc36920a6f8f598f4ff67`), não de `master`.
- `uv.lock` regenerado para o nome `mcp-kicad10`.

## [0.1.0]

Primeira versão: tools de arquivo via `kicad-cli`, `board_summary` via IPC,
perfis JLCPCB e BOM/CPL com correção de rotação.
