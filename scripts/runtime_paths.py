"""Runtime discovery without a developer-specific username or drive."""
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def node_executable():
    choices = [os.environ.get('PIANKE_NODE'), ROOT / 'tools/node/node.exe', shutil.which('node'),
               Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe']
    return next((str(p) for p in choices if p and Path(p).is_file()), '')


def sample_config(root=ROOT):
    legacy = root / 'configs/neumann_u87ai.json'
    return legacy if legacy.is_file() else root / 'configs/defaults.json'
