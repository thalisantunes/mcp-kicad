# ADR 0002 — `validate`: grafo de verificações por estágio, local e somente leitura

**Data:** 28/10/2026 · **Status:** aceito

## Contexto

O servidor expõe 16 tools que executam uma ação cada (`sch_erc`, `pcb_drc`, exports, regras JLCPCB). Falta o que um
revisor de hardware faz antes de mandar para a fábrica: rodar as checagens **na ordem certa**, parar no primeiro estágio
que reprova, e devolver um veredito único com os achados graduados e localizados. Hoje quem orquestra isso é o agente que
chama as tools uma a uma, sem gate, sem schema comum de achado e sem relatório que sobreviva à sessão.

Princípios que a decisão precisa respeitar:
- **Local e aberto.** Nada sai da máquina; nenhuma conta, serviço hospedado ou modelo obrigatório. `kicad-cli` é a
  única dependência dura. Licença MIT, como o resto do projeto.
- **Somente leitura.** Validar nunca altera esquema, placa, bibliotecas ou projeto.
- **Modelo opcional.** Revisões por LLM (Claude, Qwen local via Ollama, ou nenhum) entram como plugins que leem o
  mesmo snapshot e devolvem achados no mesmo schema; o grafo funciona sem nenhum deles.
- Regras existentes do repo: arquivo → `kicad_cli.py`, sessão viva → `ipc.py`; erros `MK-<área>-<n>`; números de
  fabricação só com fonte em `rules/jlcpcb/README.md`.

## Decisão

1. **Nova tool MCP `validate(project, profile="dev", gate="stage", until=None, only=None, skip=None, out=None)`** e
   subcomando `mcp-kicad validate <projeto.kicad_pro>` com as mesmas opções, em `src/mcp_kicad/validate/`.
2. **Grafo declarativo** (`validate/graph.py`, com configuração em JSON de duas origens, ver "Desvios" e "Confiança da configuração"):

   | Estágio | Check | Como | Bloqueia |
   |---|---|---|---|
   | S0 intake | `intake.tools` (kicad-cli presente e versão ≥ 10), `intake.files` (pro/sch/pcb existem) | local | tudo |
   | S1 esquema | `sch.erc` | `kicad-cli sch erc --format json` | S2+ |
   | S2 placa | `pcb.drc` (inclui `.kicad_dru` do projeto), `pcb.parity` (paridade esquema/placa do próprio DRC) | `kicad-cli pcb drc --format json` | S3+ |
   | S3 fabricação | `fab.rules` (DRC com o perfil JLCPCB escolhido aplicado em **cópia temporária**, nunca no projeto) | `fab/jlcpcb.py` + DRC | S4 |
   | S4 saída | `fab.gerbers` (export para pasta temporária e checagem de camadas/drill presentes) | `kicad-cli pcb export` | — |
   | S5 revisão | `review.*` (plugins de agente, opcionais, um por preocupação: alimentação, decoupling, conectores, DFM) | plugin local | — |

   Cada check declara `stage`, `requires`, `gate` (`block|warn`) e `builtin|agent`. Dependência de estágio posterior
   é recusada (`MK-VAL-001`); `--only` inclui os pré-requisitos duros dos checks pedidos.
3. **Snapshot somente leitura.** O projeto é copiado para um diretório temporário antes de qualquer check; checks só
   escrevem dentro de `<out>/checks/<id>/`. Um hash do snapshot antes e depois de cada check (inclusive quando ele lança exceção) vira `ERROR` `MK-VAL-002` se
   mudar, e o run deixa de usar o snapshot (os checks restantes ficam `SKIPPED`, `blocked_by` o check que sujou).
   Links simbólicos que resolvam para fora do projeto são recusados (`MK-VAL-002`).
4. **Schema único de achado** (`validate/finding.py`, dataclass + JSON):
   `{"id": "<check>/<nnn>", "severity": "P0|P1|P2|P3", "title", "detail", "locator": {"sheet", "refs": [], "nets": [],
   "layer", "x_mm", "y_mm"}, "evidence", "rule", "suggested_action", "confidence": "high|medium|low", "source": "builtin|agent"}`.
   Mapeamento do KiCad: `error`→P0, `warning`→P1 (DRC de fabricação) ou P2 (ERC informativo), `exclusion`→ignorado.
   P0 = não funciona, inseguro ou a fábrica rejeita; P1 = risco de confiabilidade/montagem; P2 = um revisor experiente
   apontaria; P3 = acabamento.
5. **Perfis e gate.** `dev` reprova em P0/P1 com confiança ≥ média; `release` reprova em P0–P2 com qualquer confiança.
   `gate=stage` termina o estágio atual e para; `full` roda tudo que não depende de um check reprovado; `strict` para
   na primeira reprovação. Um check pulado por qualquer motivo (gate, `--skip`, `--until`, `--only`) nunca libera os
   dependentes: eles ficam `SKIPPED` com `blocked_by`. `ERROR` num check com `gate: warn` conta como `WARN` (um
   plugin que cai não reprova; seus dependentes ficam bloqueados).
6. **Saídas** em `<projeto>/.mcp-kicad/validate/runs/<UTC>-<hash>/` com `latest` apontando para a última:
   `report.json` (schema `mcp-kicad.validate/v1`, veredito, perfil, gate, hash do projeto, versões, resultados),
   `REPORT.md` (tabela por check + achados com Onde/Por quê/Evidência/Regra/Ação), `next_actions.json` (só o primeiro
   estágio reprovado, ≤ 15 ações, pior primeiro, com `checks_blocked_downstream` e `rerun`), `junit.xml` opcional
   (`--junit`), e `checks/<id>/` com os JSON crus do `kicad-cli`.
7. **Códigos de saída** da CLI: 0 PASS (só PASS/WARN), 1 FAIL, 2 erro de uso/config (flag inválida, JSON de configuração inválido,
   ciclo no grafo, check desconhecido), 3 INCOMPLETO (check bloqueante pulado por `--until`/`--only`/`--skip` ou por pré-requisito ausente), 130 interrompido com
   relatório parcial marcado.
8. **Plugins de revisão** (`validate/plugins/`): interface `Reviewer.run(snapshot, context) -> list[Finding]`; um
   adaptador genérico `llm_json` que envia o prompt a um comando local (`claude -p`, `ollama run`) e exige o bloco JSON
   do schema, com até duas rodadas de reparo (`--repeat N`, consenso, previsto para v2). O comando roda em sessão própria (o grupo inteiro é encerrado no timeout ou
   interrupção), com ambiente mínimo (`PATH`, `HOME`, `LANG` e o que a config do usuário listar em `env_passthrough`). Sem plugin configurado, S5 é `SKIPPED`,
   não falha. Nenhum plugin é ativado por padrão.
9. Novos erros em `errors.py`: `MK-VAL-001` grafo ou configuração inválidos (inclui config de projeto que tenta plugin/`command` ou mexer em check builtin), `MK-VAL-002` snapshot alterado por um check ou com link para fora do projeto,
   `MK-VAL-003` plugin sem bloco JSON válido, `MK-VAL-004` saída fora do diretório do projeto (ou via link simbólico).

## Confiança da configuração

O projeto sob validação não é confiável: um `.mcp-kicad/validate.json` num repositório de terceiros não pode fazer o
`validate` executar programas nem afrouxar o gate.

- **Config do usuário** (`$MCP_KICAD_REVIEWERS` com o caminho de um JSON, ou `~/.config/mcp-kicad/reviewers.json`):
  é a única origem de plugins (`kind: agent`, `command`, `env_passthrough`, `timeout_s`) e a única que pode desabilitar
  ou rebaixar (`gate: warn`) um check builtin. `command[0]` é só o nome de um executável do `PATH` (sem separador de
  caminho); `timeout_s` precisa ser inteiro positivo.
- **Config do projeto** (`<projeto>/.mcp-kicad/validate.json`): só `fab_profile`, `skip`/`until` padrão (a linha de
  comando vence) e `{id, gate, enabled}` de checks NÃO-builtin já definidos pela config do usuário. Qualquer
  `command`/`kind: agent`, check novo ou mexida nos builtins (`intake.*`, `sch.erc`, `pcb.drc`, `pcb.parity`,
  `fab.rules`, `fab.gerbers`) é `MK-VAL-001`.
- Todo override aceito, de qualquer origem, vai em `report.json` (`overrides`) e no `REPORT.md`.
- A tool MCP restringe `out` ao diretório do projeto; a CLI mantém `--out` livre.

## Consequências

**Ganhos**
- Um único "a placa está pronta?" com gate, reproduzível em CI (`junit.xml`, exit codes) e sem rede.
- Achados com localizador e evidência tornam útil qualquer modelo, inclusive pequeno e local, porque a resposta é
  verificável e o builtin continua sendo a fonte de verdade.
- Reuso de `kicad_cli.erc_report`/`drc_report`, `pcb_export_*` e dos perfis JLCPCB; nada duplicado entre CLI e IPC.

**Custos**
- Cópia temporária do projeto por execução (projetos com STEP pesados: excluir `3dmodels/` do snapshot por padrão).
- Mais um subcomando na CLI; `__main__.py` passa a ter `serve` (padrão) e `validate`.
- Manutenção do grafo padrão quando o `kicad-cli` mudar formato de JSON.

**Regra de roteamento**
- Checagem de arquivo (ERC, DRC, export): sempre via `kicad_cli.py`, dentro do snapshot.
- Nada de IPC no `validate`: a sessão viva do KiCad não representa o arquivo salvo.
- Revisores LLM só leem o snapshot e os relatórios dos builtins; nunca o projeto original.

## Alternativas descartadas

- **Serviço hospedado de revisão** (projeto sobe para a nuvem de terceiro, conta obrigatória, modelo fixo): fere
  "local e aberto" e custo zero; descartado.
- **Orquestrar no agente** (o Claude chama `sch_erc`, `pcb_drc`, exports em sequência): sem gate, sem relatório
  persistente, resultado varia por sessão.
- **Um único check "tudo" sem estágios**: perde a ordem de diagnóstico (um ERC reprovado invalida o DRC) e mistura
  achados de fabricação com achados elétricos.
- **Formato de achado do KiCad cru**: não tem severidade de produto (P0–P3), evidência nem ação; insuficiente para
  `next_actions`.

## Desvios da implementação (v1)

- **Override em JSON, não YAML**: o PyYAML não é dependência. Arquivo `.mcp-kicad/validate.json`; um `.yaml` ao lado
  gera erro explicando.
- **`--repeat N` (consenso) adiado**: o item 8 prometia; não foi implementado nesta versão.
- **`.mcp-kicad/` fora do snapshot**, assim como `3dmodels/`, `.git/`, `*.step|*.stp|*.wrl`, links simbólicos internos
  para diretório e a pasta de saída do run. Links internos para arquivo viram arquivos comuns.
- **Exceções do hash do snapshot**: `*.kicad_prl`, `*.lck` e `fp-info-cache` (estado local que o `kicad-cli` regera ao
  abrir o projeto), comparados em minúsculas. O hash é por arquivo (caminho, tamanho e sha256), sem ambiguidade de borda.
- **Plugins só pela config do usuário** (ver "Confiança da configuração"); o item 2 original deixava o projeto
  declarar tudo.
- **`ERROR` com `gate: warn` conta como WARN** no veredito, e o que depende desse check fica bloqueado.
- **`--skip` de check bloqueante gera INCOMPLETO** (exit 3), como `--until`/`--only`.
- **`latest` é publicado de forma atômica** (`latest.tmp-<pid>` + `os.replace`); se não der (p.ex. já existe um
  diretório real), vira aviso no relatório e o run segue.
- **`latest` só é substituído se for link simbólico ou não existir**: arquivo comum ou diretório do usuário com esse
  nome é preservado e vira aviso no relatório.
- **Guarda de `.mcp-kicad` simbólico vale também na CLI**: sem `--out`, `.mcp-kicad` e `.mcp-kicad/validate` simbólicos
  são recusados (`MK-VAL-004`) sempre, independente de `restrict_out`.
- **`out` da tool MCP fica sob `<projeto>/.mcp-kicad/`** (não só dentro do projeto): a raiz do projeto e qualquer
  caminho com componente `.git` são recusados (`MK-VAL-004`). A CLI segue livre com `--out`.
