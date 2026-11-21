"""Where private inputs live. They stay outside Git and outside any temp/cache folder.

Default: `data/` of the MAIN checkout (also when running from a git worktree), which is
git-ignored. Override with the NE_BIKING_PRIVATE environment variable.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main_checkout(root=ROOT):
    marker = root / '.git'
    if marker.is_file():  # linked worktree: "gitdir: <main>/.git/worktrees/<name>"
        text = marker.read_text(encoding='utf-8').strip()
        if text.startswith('gitdir:'):
            gitdir = Path(text[7:].strip())
            if gitdir.parent.name == 'worktrees':
                return gitdir.parents[1].parent
    return root


def private_dir():
    override = os.environ.get('NE_BIKING_PRIVATE')
    return Path(override) if override else main_checkout() / 'data'


def resolve(base, value):
    """Paths in settings.json are relative to the settings file unless absolute."""
    path = Path(value)
    return path if path.is_absolute() else Path(base) / path
