"""
Drives Ghidra's headless analyzer against a binary and returns the path
to the JSON export produced by ExportAnalysis.java.
"""

import re
import subprocess
import sys
from pathlib import Path

from config import (
    GHIDRA_PATH,
    GHIDRA_PROJECT_DIR,
    GHIDRA_PROJECT_NAME,
    GHIDRA_SCRIPT_DIR,
    GHIDRA_JSON_DIR,
    PROJECT_ROOT,
)


def analyze_binary(binary_path: str, overwrite: bool = True, verbose: bool = False) -> str:
    binary = Path(binary_path).resolve()
    if not binary.exists():
        raise FileNotFoundError(f"Binary not found: {binary}")

    GHIDRA_JSON_DIR.mkdir(parents=True, exist_ok=True)

    project_dir = PROJECT_ROOT / GHIDRA_PROJECT_DIR
    project_dir.mkdir(exist_ok=True)

    safe_name   = re.sub(r"[^a-zA-Z0-9._-]", "_", binary.name)
    output_path = GHIDRA_JSON_DIR / (safe_name + ".json")

    # Pass the absolute output directory to ExportAnalysis.java via script args.
    # Ghidra's JVM cwd is unpredictable — relative paths in the Java script
    # resolve against Ghidra's install dir, not the project root.
    cmd = [
        str(GHIDRA_PATH),
        str(project_dir),
        GHIDRA_PROJECT_NAME,
        "-import",     str(binary),
        "-overwrite",
        "-scriptPath", str(PROJECT_ROOT / GHIDRA_SCRIPT_DIR),
        "-postScript", "ExportAnalysis.java",
        str(GHIDRA_JSON_DIR.resolve()),   # <-- script arg: absolute output dir
    ]

    # Always capture output so we can surface it on failure.
    result = subprocess.run(cmd, capture_output=True, text=True)

    if verbose or result.returncode != 0:
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"Ghidra exited with code {result.returncode}. "
            "Re-run with --verbose to see full output."
        )

    if not output_path.exists():
        # Surface Ghidra's output so the user can see if the script errored
        snippet = (result.stdout or "")[-3000:]
        raise RuntimeError(
            f"Ghidra completed but export not found: {output_path}\n"
            f"Ghidra output (last 3 KB):\n{snippet}"
        )

    return str(output_path)
