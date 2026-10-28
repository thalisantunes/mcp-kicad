"""CLI: `serve` (padrão, servidor MCP por stdio) e `validate` (ADR 0002)."""

from __future__ import annotations

import argparse
import json
import sys

from .errors import McpKicadError

STAGE_HELP = "id de check (ex.: pcb.drc) ou estágio (S0..S5)"


def _csv(value: str) -> list[str]:
    return [v for v in value.split(",") if v]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcp-kicad")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="sobe o servidor MCP por stdio (padrão)")

    v = sub.add_parser(
        "validate",
        help="valida o projeto por estágios (S0..S5)",
        description="Exit codes: 0 PASS, 1 FAIL, 2 erro de uso/config, 3 INCOMPLETO, "
        "130 interrompido.",
    )
    v.add_argument("project", help="caminho do .kicad_pro")
    v.add_argument("--profile", choices=["dev", "release"], default="dev")
    v.add_argument("--gate", choices=["stage", "full", "strict"], default="stage")
    v.add_argument("--until", help=STAGE_HELP)
    v.add_argument("--only", type=_csv, help=f"lista separada por vírgula: {STAGE_HELP}")
    v.add_argument("--skip", type=_csv, help=f"lista separada por vírgula: {STAGE_HELP}")
    v.add_argument("--out", help="base das saídas (padrão: <projeto>/.mcp-kicad/validate)")
    v.add_argument("--json", action="store_true", help="imprime o resumo em JSON")
    v.add_argument("--junit", metavar="PATH", help="grava também um junit.xml")
    return parser


def _validate(args: argparse.Namespace) -> int:
    from .validate import run_validate

    try:
        run = run_validate(
            args.project,
            profile=args.profile,
            gate=args.gate,
            until=args.until,
            only=args.only,
            skip=args.skip,
            out=args.out,
            junit=args.junit,
        )
    except McpKicadError as exc:
        print(exc, file=sys.stderr)
        return 2
    summary = run.summary()
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"{summary['verdict']}  (perfil {run.profile}, gate {run.gate})")
        for cid, status in summary["checks"].items():
            print(f"  {status:<8} {cid}")
        print("achados: " + " ".join(f"{k}={n}" for k, n in summary["counts"].items()))
        print(f"relatório: {run.run_dir / 'REPORT.md'}")
    return run.exit_code


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        return _validate(args)
    from .server import main as serve

    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
