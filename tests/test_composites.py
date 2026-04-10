"""Smoke tests for the composite CLI commands: resume, save, install-commands.

These don't exercise the ingest pipeline end-to-end (that would need a
real Claude Code transcript). They verify:

  - cmd_resume runs against a pre-populated snapshot and produces the
    expected section headers without crashing.
  - cmd_install_commands writes the template to a tmp path, respects
    --force, refuses to overwrite by default, --dry-run prints without
    writing.
  - The bundled template file exists and is readable via importlib.resources.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from bellamem.core import Bella, Claim, save
from bellamem.core.embed import HashEmbedder, set_embedder
from bellamem.cli import build_parser, cmd_install_commands, cmd_resume, main


def _populated_snapshot(tmp_path: Path) -> Path:
    """Create a small Bella with content and save it to disk."""
    set_embedder(HashEmbedder())
    bella = Bella()
    bella.ingest(Claim(text="the first thing we decided", voice="user", lr=2.0))
    bella.ingest(Claim(text="the second thing we decided", voice="user", lr=2.0))
    bella.ingest(Claim(text="something we rejected", voice="user", lr=1.5))
    snap_path = tmp_path / "snap.json"
    save(bella, str(snap_path))
    return snap_path


# ---------------------------------------------------------------------------
# Template bundling
# ---------------------------------------------------------------------------


def test_template_is_bundled_and_readable():
    from importlib.resources import files
    template = files("bellamem.templates").joinpath("bellamem.md").read_text()
    assert "description:" in template
    assert "/bellamem" in template
    # New-style template: a single bellamem $ARGUMENTS call, no shell dispatcher.
    assert "!`bellamem $ARGUMENTS`" in template
    # allowed-tools must match the actual first executable for the
    # Claude Code permission check to auto-approve.
    assert "allowed-tools: Bash(bellamem:*)" in template
    # Post-instructions block must be present for Claude to synthesize.
    assert "subcommand of BellaMem" in template
    assert "Synthesize in under 300 words" in template


# ---------------------------------------------------------------------------
# install-commands
# ---------------------------------------------------------------------------


def _install_args(project: bool = False, force: bool = False, dry_run: bool = False) -> argparse.Namespace:
    return argparse.Namespace(project=project, force=force, dry_run=dry_run)


def test_install_commands_project_writes_file(tmp_path: Path, monkeypatch):
    """--project installs into ./.claude/commands/ relative to cwd."""
    monkeypatch.chdir(tmp_path)
    rc = cmd_install_commands(_install_args(project=True))
    assert rc == 0
    target = tmp_path / ".claude" / "commands" / "bellamem.md"
    assert target.exists()
    content = target.read_text()
    assert "!`bellamem $ARGUMENTS`" in content


def test_install_commands_refuses_overwrite(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cmd_install_commands(_install_args(project=True))
    # Second call without --force should fail.
    rc = cmd_install_commands(_install_args(project=True))
    assert rc == 1
    err = capsys.readouterr().err
    assert "already exists" in err
    assert "--force" in err


def test_install_commands_force_overwrites(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / ".claude" / "commands" / "bellamem.md"
    target.parent.mkdir(parents=True)
    target.write_text("stale content")
    rc = cmd_install_commands(_install_args(project=True, force=True))
    assert rc == 0
    assert "!`bellamem $ARGUMENTS`" in target.read_text()


def test_install_commands_dry_run_does_not_write(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = cmd_install_commands(_install_args(project=True, dry_run=True))
    assert rc == 0
    target = tmp_path / ".claude" / "commands" / "bellamem.md"
    assert not target.exists()
    out = capsys.readouterr().out
    assert "would install" in out


def test_install_commands_global_default_writes_home(tmp_path: Path, monkeypatch):
    """Default (--project=False) writes into ~/.claude/commands/.
    We redirect HOME to a tmp dir so the test doesn't touch the real home."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = cmd_install_commands(_install_args(project=False))
    assert rc == 0
    target = tmp_path / ".claude" / "commands" / "bellamem.md"
    assert target.exists()


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


def test_cmd_resume_prints_expected_sections(tmp_path: Path, capsys):
    snap = _populated_snapshot(tmp_path)
    args = argparse.Namespace(
        snapshot=str(snap),
        focus="current state",
        replay_budget=1000,
        expand_budget=800,
        surprise_top=3,
    )
    rc = cmd_resume(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "## Working memory (replay tail)" in out
    assert "## Long-term memory" in out
    assert "## What just mattered (surprises)" in out


def test_cmd_resume_on_empty_memory_returns_error(tmp_path: Path, capsys):
    set_embedder(HashEmbedder())
    empty = Bella()
    snap = tmp_path / "empty.json"
    save(empty, str(snap))
    args = argparse.Namespace(
        snapshot=str(snap),
        focus="x",
        replay_budget=500,
        expand_budget=500,
        surprise_top=3,
    )
    rc = cmd_resume(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "empty memory" in err


# ---------------------------------------------------------------------------
# Argparse shape: recall/why aliases and bare-resume default
# ---------------------------------------------------------------------------


def test_parser_accepts_bare_invocation():
    """`bellamem` with no subcommand parses to cmd=None (main() handles it)."""
    parser = build_parser()
    args = parser.parse_args([])
    assert args.cmd is None


def test_parser_accepts_recall_alias():
    parser = build_parser()
    args = parser.parse_args(["recall", "the", "forgetting", "mechanism"])
    assert args.cmd == "recall"
    assert args.topic == ["the", "forgetting", "mechanism"]
    assert args.budget == 1500


def test_parser_accepts_why_alias():
    parser = build_parser()
    args = parser.parse_args(["why", "the", "slash", "dispatcher"])
    assert args.cmd == "why"
    assert args.topic == ["the", "slash", "dispatcher"]
    assert args.budget == 1500


def test_main_routes_bare_invocation_to_resume(tmp_path: Path, monkeypatch, capsys):
    """main() with no argv should run cmd_resume against the resolved snapshot."""
    # Force the hash embedder regardless of what the user's .env says —
    # the populated snapshot fixture uses HashEmbedder, so loading it
    # with a different embedder would trip the signature check.
    monkeypatch.setenv("BELLAMEM_EMBEDDER", "hash")
    monkeypatch.delenv("BELLAMEM_EMBEDDER_MODEL", raising=False)
    snap = _populated_snapshot(tmp_path)
    monkeypatch.setenv("BELLAMEM_SNAPSHOT", str(snap))
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "## Working memory (replay tail)" in out
