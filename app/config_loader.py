import yaml
from pathlib import Path
from typing import List


def load_active_repositories(config_path: str = "config.yaml") -> List[dict]:
    """Load the list of active repositories from a YAML config file."""
    path = Path(config_path)
    if not path.exists():
        return []
    with path.open("r") as f:
        data = yaml.safe_load(f)
    repos = data.get("repositories", [])
    return [r for r in repos if r.get("is_active", True)]
