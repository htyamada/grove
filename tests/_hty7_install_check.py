"""Fail fast when the installed hty7 package is stale relative to its
source tree — see check_hty7_install_freshness() for why this exists.

Not a test module itself (unittest discovery only picks up test*.py), so
it never runs as a test; tests/__init__.py imports and calls it once,
before any real test module is discovered.
"""

from pathlib import Path

_DEFAULT_SOURCE_ROOT = Path.home() / 'src' / 'hty7' / 'python3' / 'lib' / 'hty7'


def _installed_hty7_root() -> Path | None:
    try:
        import hty7
    except ImportError:
        return None  # Not importable at all; the real ImportError will fire on its own.
    return Path(hty7.__file__).resolve().parent


def check_hty7_install_freshness(
    source_root: Path = _DEFAULT_SOURCE_ROOT,
) -> None:
    """Raise `RuntimeError` if any hty7 source file is newer than its
    installed (site-packages) counterpart, or missing there entirely.

    Grove deliberately depends on the *installed* hty7, not the source
    tree directly (see this repository's AGENTS.md) — the right boundary
    for a real deployment, but it means a source-tree edit that hasn't
    been synced with `make install-libraries` yet is invisible to Grove's
    tests until it is. Left unchecked, that produces a confusing
    AttributeError or ImportError deep inside whichever test happens to
    touch the new code first — indistinguishable from a real bug (this
    happened during Task 11's own development). Fail immediately, here,
    with an unambiguous message instead.

    A no-op — not an error — when the hty7 source tree isn't checked out
    on this machine, or when hty7 is not yet importable at all (its own
    failure will surface directly, this check would only obscure it).
    """
    if not source_root.is_dir():
        return
    installed_root = _installed_hty7_root()
    if installed_root is None or installed_root == source_root.resolve():
        return

    stale: list[str] = []
    for source_path in source_root.rglob('*.py'):
        if '__pycache__' in source_path.parts:
            continue
        rel = source_path.relative_to(source_root)
        installed_path = installed_root / rel
        if not installed_path.exists():
            stale.append(f'{rel} (missing from the installed copy)')
        elif source_path.stat().st_mtime > installed_path.stat().st_mtime:
            stale.append(str(rel))

    if not stale:
        return
    stale.sort()
    shown = '\n  '.join(stale[:10])
    more = f'\n  ... and {len(stale) - 10} more' if len(stale) > 10 else ''
    raise RuntimeError(
        f'{len(stale)} file(s) in the installed hty7 package '
        f'({installed_root}) are older than their source-tree copy '
        f'({source_root}). Run:\n'
        f'  cd ~/src/hty7/python3 && make install-libraries\n'
        f'before running Grove\'s tests. Stale file(s):\n  {shown}{more}'
    )
