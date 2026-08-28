from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from astock_hub.forum import analyze_records, analyze_records_with_llm, load_records, render_markdown
from astock_hub.registry import DEFAULT_CONFIG, PROJECT_ROOT, inspect_projects, load_config


def _doctor(config_path: Path) -> int:
    config = load_config(config_path)
    statuses = inspect_projects(config)
    print(f"Personal A-stock Hub: {config['name']} ({config['version']})")
    print(f"Objective: {config['objective']}")
    print()
    print(f"{'PROJECT':<28} {'ROLE':<16} {'STATE':<10} {'BRANCH':<12} LICENSE")
    print("-" * 84)
    missing = 0
    for status in statuses:
        if not status.exists:
            state = "missing"
            missing += 1
        elif not status.is_git_repo:
            state = "not-git"
        elif status.dirty:
            state = "dirty"
        else:
            state = "ready"
        print(f"{status.project_id:<28} {status.role:<16} {state:<10} {status.branch:<12} {status.license_name}")
    print()
    print(f"Registered: {len(statuses)}, missing: {missing}")
    return 1 if missing else 0


def _forum_analyze(args: argparse.Namespace) -> int:
    input_path = args.input.resolve()
    records = load_records(input_path, args.source)
    if args.engine == "llm":
        summary = analyze_records_with_llm(
            records,
            api_key=os.environ.get("ASTOCK_LLM_API_KEY", ""),
            base_url=os.environ.get("ASTOCK_LLM_BASE_URL", "https://api.openai.com/v1"),
            model=os.environ.get("ASTOCK_LLM_MODEL", "gpt-4o-mini"),
            batch_size=args.batch_size,
        )
    else:
        summary = analyze_records(records)
    output = args.output
    if output is None:
        output = PROJECT_ROOT / "docs" / "by-requirement" / "个人A股投研中枢" / "reports" / f"{args.symbol}-forum-sentiment.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(args.symbol, summary, input_path), encoding="utf-8")
    print(f"Engine={args.engine}, analyzed {summary.total_analyzed} records, score={summary.score:+.2f}, confidence={summary.confidence:.2f}%")
    print(f"Report: {output.resolve()}")
    return 0


def _xueqiu_workspace(path: Path | None) -> Path:
    return (path or PROJECT_ROOT / "var" / "xueqiu" / "workspace").resolve()


def _xueqiu_venv_python() -> Path:
    if os.name == "nt":
        return PROJECT_ROOT / "var" / "venvs" / "xueqiu" / "Scripts" / "python.exe"
    return PROJECT_ROOT / "var" / "venvs" / "xueqiu" / "bin" / "python"


def _xueqiu_setup(_args: argparse.Namespace) -> int:
    repo = PROJECT_ROOT / "repos" / "xueqiu-crawler"
    python = _xueqiu_venv_python()
    if not python.exists():
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(python.parent.parent)],
            cwd=PROJECT_ROOT,
            check=False,
        )
        if result.returncode:
            return result.returncode
    result = subprocess.run(
        [str(python), "-m", "pip", "install", "-r", str(repo / "requirements.txt")],
        cwd=repo,
        check=False,
    )
    if result.returncode:
        return result.returncode
    if os.name == "nt":
        chrome_candidates = (
            Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        )
        if not any(path.is_file() for path in chrome_candidates):
            print("Google Chrome is required for Xueqiu interactive login.", file=sys.stderr)
            return 1
    print(f"Xueqiu runtime ready: {python}")
    return 0


def _run_xueqiu(arguments: list[str]) -> int:
    repo = PROJECT_ROOT / "repos" / "xueqiu-crawler"
    if not repo.is_dir():
        print(f"Missing upstream repository: {repo}", file=sys.stderr)
        return 1
    python = _xueqiu_venv_python()
    if not python.exists():
        print("Xueqiu runtime is missing. Run: python -m astock_hub forum xueqiu-setup", file=sys.stderr)
        return 1
    result = subprocess.run([str(python), "-m", "xq", *arguments], cwd=repo, check=False)
    return result.returncode


def _xueqiu_init(args: argparse.Namespace) -> int:
    workspace = _xueqiu_workspace(args.workspace)
    return _run_xueqiu(["init", "--workspace", str(workspace), "--json"])


def _xueqiu_login(args: argparse.Namespace) -> int:
    workspace = _xueqiu_workspace(args.workspace)
    if not workspace.exists() and _run_xueqiu(["init", "--workspace", str(workspace), "--json"]):
        return 1
    return _run_xueqiu(["auth", "login", "--workspace", str(workspace), "--json"])


def _xueqiu_symbol(stock_code: str) -> str:
    code = stock_code.strip().upper()
    if code.endswith(".SH"):
        return f"SH{code[:-3]}"
    if code.endswith(".SZ"):
        return f"SZ{code[:-3]}"
    return code


def _xueqiu_collect(args: argparse.Namespace) -> int:
    workspace = _xueqiu_workspace(args.workspace)
    if not workspace.exists() and _run_xueqiu(["init", "--workspace", str(workspace), "--json"]):
        return 1
    code = _run_xueqiu(
        ["crawl", "stock-posts", args.symbol, "--workspace", str(workspace), "--pages", str(args.pages), "--json"]
    )
    if code or args.no_analyze:
        return code
    input_path = workspace.parent / "data" / "stock_posts" / f"{_xueqiu_symbol(args.symbol)}.jsonl"
    return _forum_analyze(
        argparse.Namespace(
            input=input_path,
            source="xueqiu",
            engine=args.engine,
            batch_size=args.batch_size,
            output=args.output,
            symbol=args.symbol.split(".")[0],
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Personal A-share research hub")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check registered upstream projects")
    doctor.set_defaults(handler=lambda args: _doctor(args.config))

    forum = subparsers.add_parser("forum", help="Forum data tools")
    forum_subparsers = forum.add_subparsers(dest="forum_command", required=True)
    analyze = forum_subparsers.add_parser("analyze", help="Analyze normalized forum sentiment")
    analyze.add_argument("--symbol", required=True, help="A-share code, for example 600519")
    analyze.add_argument("--input", required=True, type=Path, help="JSONL, JSON, CSV or TXT input")
    analyze.add_argument("--source", choices=("auto", "xueqiu", "eastmoney", "unknown"), default="auto")
    analyze.add_argument("--engine", choices=("lexicon", "llm"), default="lexicon")
    analyze.add_argument("--batch-size", type=int, default=20)
    analyze.add_argument("--output", type=Path)
    analyze.set_defaults(handler=_forum_analyze)

    xueqiu_setup = forum_subparsers.add_parser("xueqiu-setup", help="Install the isolated Xueqiu crawler runtime")
    xueqiu_setup.set_defaults(handler=_xueqiu_setup)

    xueqiu_init = forum_subparsers.add_parser("xueqiu-init", help="Initialize the hub-owned Xueqiu workspace")
    xueqiu_init.add_argument("--workspace", type=Path)
    xueqiu_init.set_defaults(handler=_xueqiu_init)

    xueqiu_login = forum_subparsers.add_parser("xueqiu-login", help="Open a browser and save Xueqiu login cookies locally")
    xueqiu_login.add_argument("--workspace", type=Path)
    xueqiu_login.set_defaults(handler=_xueqiu_login)

    collect = forum_subparsers.add_parser("collect-xueqiu", help="Collect Xueqiu stock posts and optionally analyze them")
    collect.add_argument("--symbol", required=True, help="Use exchange suffix, for example 600519.SH")
    collect.add_argument("--pages", type=int, default=1)
    collect.add_argument("--workspace", type=Path)
    collect.add_argument("--engine", choices=("lexicon", "llm"), default="lexicon")
    collect.add_argument("--batch-size", type=int, default=20)
    collect.add_argument("--output", type=Path)
    collect.add_argument("--no-analyze", action="store_true")
    collect.set_defaults(handler=_xueqiu_collect)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)
