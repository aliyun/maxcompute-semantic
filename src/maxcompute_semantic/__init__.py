"""maxcompute-semantic — semantic-layer-aware MaxCompute CLI."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

# Pulled from the installed distribution so it always matches
# pyproject.toml's [project].version field. Fallback is for the
# rare case of running the source tree without an install.
try:
    __version__ = _pkg_version("maxcompute-semantic")
except PackageNotFoundError:
    __version__ = "0+unknown"

__all__ = ["__version__"]
