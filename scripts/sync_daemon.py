"""
CareerBridge Sync Daemon — secondary machine.

Sync loop (every 300 s):
  1. Auto-commit any local changes with a timestamped message.
  2. git fetch origin
  3. git pull --rebase origin master
     - Conflicts resolved by authority rules:
         secondary owns: data/*, reports/*, logs/*  → keep local version
         primary owns:   *.py, *.ps1, *.md, CLAUDE.md → keep upstream version
         ambiguous files → invoke Claude via --print --dangerously-skip-permissions
  4. git push origin master
  5. Sleep 300 s, repeat.

Flags:
  --once       Run one sync cycle then exit.
  --dry-run    Print what would happen without making changes (implies --once).
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

REPO_DIR  = Path(os.environ.get("CB_DIR", Path(__file__).resolve().parent.parent))
LOG_FILE  = REPO_DIR / "logs" / "sync_daemon.log"
PYTHON    = Path(os.environ.get("CB_PYTHON", "C:/Python314/python.exe"))
POLL_SECS = 30
BRANCH    = "master"
REMOTE    = "origin"

# File ownership for conflict resolution.
# During git rebase: "ours" = upstream (primary); "theirs" = our local commit (secondary).
SECONDARY_PREFIXES   = ("data/", "reports/", "logs/")
PRIMARY_SUFFIXES     = (".py", ".ps1", ".md")
PRIMARY_EXACT_NAMES  = {"CLAUDE.md"}

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

_fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_fh  = RotatingFileHandler(str(LOG_FILE), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
_fh.setFormatter(_fmt)
_sh  = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)

log = logging.getLogger("sync")
log.setLevel(logging.INFO)
log.addHandler(_fh)
log.addHandler(_sh)

# ── Git helpers ───────────────────────────────────────────────────────────────

def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_DIR), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=check,
    )


def _local_sha() -> str:
    return _git("rev-parse", "HEAD").stdout.strip()


def _fetch_remote_sha() -> str:
    """Fetch from remote and return its HEAD SHA."""
    _git("fetch", REMOTE, BRANCH)
    return _git("rev-parse", f"{REMOTE}/{BRANCH}").stdout.strip()


def _has_local_changes() -> bool:
    return bool(_git("status", "--porcelain").stdout.strip())


def _commit_local(dry_run: bool = False) -> bool:
    if not _has_local_changes():
        return False
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    msg = f"sync: auto-commit from secondary at {now}"
    if dry_run:
        log.info("[dry-run] Would commit: %s", msg)
        return True
    _git("add", "-A")
    r = _git("commit", "-m", msg)
    log.info("Auto-committed: %s", msg)
    if r.stdout.strip():
        log.debug(r.stdout.strip())
    return True


def _conflicted_files() -> list[str]:
    r = _git("diff", "--name-only", "--diff-filter=U", check=False)
    return [f.strip() for f in r.stdout.splitlines() if f.strip()]


def _file_owner(path: str) -> str:
    """Return 'secondary', 'primary', or 'ambiguous'."""
    p = path.replace("\\", "/")
    for prefix in SECONDARY_PREFIXES:
        if p.startswith(prefix):
            return "secondary"
    if Path(p).name in PRIMARY_EXACT_NAMES:
        return "primary"
    for suffix in PRIMARY_SUFFIXES:
        if p.endswith(suffix):
            return "primary"
    return "ambiguous"


def _resolve_conflicts(dry_run: bool = False) -> bool:
    """
    Apply authority rules to all conflicted files.
    Returns True when every conflict is resolved, False if any remain ambiguous.

    Rebase ours/theirs mapping:
      --ours   = upstream commit  = primary's version
      --theirs = replayed commit  = secondary's local version
    """
    files = _conflicted_files()
    if not files:
        return True

    log.warning("Conflicts in %d file(s): %s", len(files), files)
    all_resolved = True

    for f in files:
        owner = _file_owner(f)

        if owner == "secondary":
            log.info("  %s → keeping secondary version (--theirs)", f)
            if not dry_run:
                _git("checkout", "--theirs", f)
                _git("add", f)

        elif owner == "primary":
            log.info("  %s → keeping primary version (--ours)", f)
            if not dry_run:
                _git("checkout", "--ours", f)
                _git("add", f)

        else:
            log.warning("  %s → AMBIGUOUS — invoking Claude", f)
            all_resolved = False
            if not dry_run:
                _claude_resolve(f)

    return all_resolved


def _claude_resolve(filepath: str) -> None:
    """Invoke Claude to settle an ambiguous conflict, then stage the result."""
    prompt = (
        f"You are resolving a git rebase conflict on the CareerBridge secondary machine. "
        f"Conflicted file: {filepath}  Repo: {REPO_DIR}\n"
        f"Authority rules — secondary keeps: data/, reports/, logs/. "
        f"Primary keeps: *.py, *.ps1, *.md, CLAUDE.md. "
        f"This file matched neither rule. Examine both versions in the conflict markers "
        f"and resolve intelligently to produce a coherent file. "
        f"After editing, run: git -C \"{REPO_DIR}\" add \"{filepath}\""
    )
    try:
        log.info("Calling Claude for %s …", filepath)
        subprocess.run(
            ["claude", "--print", "--dangerously-skip-permissions", prompt],
            cwd=str(REPO_DIR),
            timeout=180,
        )
        log.info("Claude finished for %s", filepath)
    except Exception as exc:
        log.error("Claude resolve failed for %s: %s — aborting rebase", filepath, exc)
        _git("rebase", "--abort", check=False)


# ── Sync cycle ────────────────────────────────────────────────────────────────

def sync_once(dry_run: bool = False) -> None:
    log.info("=== Sync cycle start  dry_run=%s ===", dry_run)

    # Step 1 — commit local changes
    committed = _commit_local(dry_run=dry_run)

    # Step 2 — fetch and compare SHAs
    if dry_run:
        # Still need to fetch to get an accurate remote SHA for reporting
        _git("fetch", REMOTE, BRANCH, check=False)

    local_sha  = _local_sha()
    remote_sha = _git("rev-parse", f"{REMOTE}/{BRANCH}").stdout.strip()

    log.info("Local SHA:  %s", local_sha)
    log.info("Remote SHA: %s", remote_sha)

    if local_sha == remote_sha and not committed:
        log.info("Already in sync.")
        log.info("=== Sync cycle end ===")
        return

    if dry_run:
        if local_sha != remote_sha:
            log.info("[dry-run] Would pull --rebase from %s/%s then push.", REMOTE, BRANCH)
        log.info("=== Sync cycle end (dry-run) ===")
        return

    # Step 3 — pull --rebase
    log.info("Pulling with rebase …")
    r = _git("pull", "--rebase", REMOTE, BRANCH, check=False)

    if r.returncode != 0:
        conflicts = _conflicted_files()
        if conflicts or "CONFLICT" in r.stdout or "CONFLICT" in r.stderr:
            log.warning("Merge conflicts detected — applying authority rules.")
            resolved = _resolve_conflicts(dry_run=False)
            if resolved:
                cont = _git("rebase", "--continue", check=False)
                if cont.returncode != 0:
                    log.error("rebase --continue failed:\n%s\n%s", cont.stdout, cont.stderr)
                    _git("rebase", "--abort", check=False)
                    return
            else:
                log.error("Unresolved conflicts remain. Aborting rebase.")
                _git("rebase", "--abort", check=False)
                return
        else:
            log.error("git pull --rebase failed:\n%s\n%s", r.stdout, r.stderr)
            _git("rebase", "--abort", check=False)
            return

    # Step 4 — push
    log.info("Pushing to %s/%s …", REMOTE, BRANCH)
    rp = _git("push", REMOTE, BRANCH, check=False)
    if rp.returncode == 0:
        log.info("Push OK. Sync complete.")
    else:
        log.error("Push failed:\n%s\n%s", rp.stdout, rp.stderr)

    log.info("=== Sync cycle end ===")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="CareerBridge Sync Daemon (secondary)")
    ap.add_argument("--once",    action="store_true", help="Run one cycle and exit")
    ap.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="Simulate without making changes (implies --once)")
    args = ap.parse_args()

    if args.dry_run:
        args.once = True

    log.info("Sync daemon starting. REPO=%s ROLE=secondary", REPO_DIR)

    if args.once:
        sync_once(dry_run=args.dry_run)
        return

    while True:
        try:
            sync_once()
        except Exception as exc:
            log.exception("Sync cycle error: %s", exc)
        log.info("Sleeping %d s …", POLL_SECS)
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()
