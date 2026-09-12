"""
Where FeedbackIQ's files live.

Data and model locations are configured as paths relative to the project root
(see `core/config.py`). Resolving them against the root, rather than against the
current working directory, means the application behaves the same whether it is
started from the repository root, from some other directory, or inside the
container - and it never depends on a particular developer's home directory.
"""

from __future__ import annotations

from pathlib import Path

# A directory containing one of these is the project root. pyproject.toml is
# present both in a source checkout and in the Docker image (which copies it
# next to src/), so it works in both places.
_ROOT_MARKERS = ("pyproject.toml", ".git")


def project_root() -> Path:
    """
    The project root: the nearest directory above this file that contains
    pyproject.toml or .git.

    Falls back to the current working directory, which is what every path in
    this project implicitly assumed before Milestone 2.
    """
    for candidate in Path(__file__).resolve().parents:
        if any((candidate / marker).exists() for marker in _ROOT_MARKERS):
            return candidate

    return Path.cwd()


def resolve(path: str | Path) -> Path:
    """
    Turn a configured location into an absolute path.

    An absolute value is returned unchanged, so an environment variable can point
    anywhere (a mounted volume, a different disk). A relative value - the default
    for every path in the settings - is resolved against the project root.
    """
    candidate = Path(path)

    return candidate if candidate.is_absolute() else project_root() / candidate
