"""Windows filesystem paths that do not depend on the system long-path setting."""

import os
from pathlib import Path


def filesystem_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    value = os.path.abspath(path)
    if value.startswith("\\\\?\\"):
        return Path(value)
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)
