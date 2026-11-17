"""Print public runtime readiness, never configuration secrets or business data."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from runtime_paths import node_executable
from setup_core import CORE_FONTS

ROOT = Path(__file__).resolve().parents[1]


def checks():
    sys.path.insert(0, str(ROOT / 'tools/python_packages'))
    rows = [{'name': 'Windows 64-bit / Python 3.11+', 'required': True,
             'ok': os.name == 'nt' and sys.maxsize > 2**32 and sys.version_info >= (3,11)}]
    for module in ('PIL', 'yt_dlp'):
        rows.append({'name': module, 'required': True, 'ok': importlib.util.find_spec(module) is not None})
    for name in ('ffmpeg.exe', 'ffprobe.exe'):
        binary = ROOT / 'tools/ffmpeg/bin' / name
        ok = False
        if binary.is_file():
            try:
                ok = subprocess.run([str(binary), '-version'], capture_output=True, timeout=15).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
        rows.append({'name': name, 'required': True, 'ok': ok})
    for name in CORE_FONTS:
        rows.append({'name': 'font: ' + name, 'required': True, 'ok': (ROOT / 'assets/fonts' / name).is_file()})
    rows += [{'name': 'Node.js (needed for reliable YouTube extraction)', 'required': False, 'ok': bool(node_executable())},
             {'name': 'Offline speech model', 'required': False, 'ok': (ROOT / 'models/faster-whisper-base/model.bin').is_file()},
             {'name': 'Local smart clipping environment', 'required': False, 'ok': (ROOT / 'tools/vision_packages/rapidocr_onnxruntime').is_dir()}]
    return rows


if __name__ == '__main__':
    rows = checks()
    for row in rows:
        print(('OK   ' if row['ok'] else 'MISS ') + row['name'] + ('' if row['required'] else ' [optional]'))
    raise SystemExit(0 if all(r['ok'] for r in rows if r['required']) else 1)
