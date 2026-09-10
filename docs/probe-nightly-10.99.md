# Probe do nightly 10.99 — o que a IPC API já entrega

**Medido em:** 12/08/2026 · **Build:** `kicad-nightly 202608111403+1e10f6df80~189~ubuntu26.04.1` (10.99.0)
· **Estável ao lado:** 10.0.5 · **kipy:** 0.7.1 (PyPI)

Motivação: a doc atribui ao "KiCad 11" o export via IPC e o modo headless — os
dois motivos pelos quais o [ADR 0001](adr/0001-ipc-api-mais-kicad-cli.md) tem
duas camadas de transporte. Se já estivessem na master, valeria simplificar.

**Conclusão: não vale. O ADR 0001 fica como está.**

## O que existe no nightly e não no estável

`kicad-cli` do estável 10.0.5:

```
{fp,jobset,pcb,sch,sym,version}
```

`kicad-cli-nightly` 10.99.0:

```
{api-server,fp,gerber,import,jobset,mergetool,pcb,sch,sym,version}
```

Quatro subcomandos novos: **`api-server`** (servidor IPC headless), `gerber`
(visualizar/comparar Gerber existente), `import` (Allegro/PADS/gEDA para projeto
KiCad novo) e `mergetool` (driver de merge three-way para `git mergetool`, com
resolução na GUI para conflito que não resolve sozinho).

O `mergetool` é interessante por fora do escopo deste MCP: resolve o problema de
`.kicad_pcb` em PR.

```
Usage: kicad-cli api-server [--help] [--socket SOCKET_PATH] PROJECT_OR_FILE
  PROJECT_OR_FILE  path opcional para .kicad_pro, .kicad_pcb ou .kicad_sch
```

## O que funcionou

Servidor headless sobe **sem display** e cria o socket:

```bash
kicad-cli-nightly api-server --socket /caminho/api.sock projeto.kicad_pro
```

Conexão pelo `kipy` funciona — com uma pegadinha: o `socket_path` exige o
esquema **`ipc://`**. Sem ele, `pynng` devolve `ConnectionError: Invalid
argument`, que não sugere em nada qual é o problema real.

```python
KiCad(socket_path="ipc:///caminho/api.sock", client_name="probe")
```

Resultados:

| Chamada | Resultado |
|---|---|
| `ping()` | OK |
| `get_version()` | `10.99.0 (10.99.0-unknown-1e10f6df80~189~ubuntu26.04.1)` |
| `get_api_version()` | `10.0.1 (10.0.1-0-g2db9e5a72b)` |
| `check_version()` | `FutureVersionError` — KiCad mais novo que a API do kipy |

## O que falhou, e por quê

```
get_board() -> ApiError: KiCad returned error:
  no handler available for request of type kiapi.common.commands.GetOpenDocuments
```

O `get_board()` do kipy passa por `GetOpenDocuments`, e o headless não tem
handler para isso — coerente, porque sem GUI não existe "documento aberto".
O fluxo headless correto seria `open_document(path, type)`, que **não existe no
kipy 0.7.1**.

Instalar o `kicad-python` da branch main não resolve hoje: o build falha
(`setup.py build` retorna 1 — falta a toolchain de protobuf para gerar as
bindings).

## Síntese

| Recurso | Estável 10.0.5 | Nightly 10.99 | Alcançável do Python hoje |
|---|---|---|---|
| IPC no editor de PCB (GUI) | ✅ | ✅ | ✅ |
| `api-server` headless | ❌ | ✅ | ⚠️ conecta, mas sem `open_document` não dá para pegar o board |
| Export/plot via IPC | ❌ | não testado (bloqueado pelo acima) | ❌ |
| Esquemático via IPC | ❌ | ❌ (`get_schematic` ausente no kipy 0.7.1) | ❌ |

O gargalo mudou de lugar: **não é mais o KiCad, é o `kipy`**. O binário já
oferece headless; a biblioteca Python publicada ainda não sabe usá-lo.

## Quando revisitar

Dois gatilhos, qualquer um deles:

1. `kicad-python` no PyPI passar de 0.7.1 com `open_document` na superfície do
   `KiCad` — daí o headless fica utilizável e vale testar export via IPC.
2. O nightly se identificar como `11.0.0-rc1-…` em vez de 10.99 — o KiCad não
   publica beta, o RC é o nightly com o número trocado.

Até então, `kicad-cli` continua sendo o caminho primário.
