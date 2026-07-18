"""
Drives Ghidra's headless analyzer against a binary and returns the path
to the JSON export produced by ExportAnalysis.java.
"""

import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

from config import (
    GHIDRA_PATH,
    GHIDRA_PROJECT_DIR,
    GHIDRA_PROJECT_NAME,
    GHIDRA_SCRIPT_DIR,
    GHIDRA_JSON_DIR,
    PROJECT_ROOT,
)


def analyze_binary(
    binary_path: str, overwrite: bool = True, verbose: bool = False,
    on_line: Optional[Callable[[str], None]] = None,
) -> str:
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

    # Stream output live instead of buffering it all until the process exits
    # (subprocess.run(capture_output=True) used to do exactly that, so
    # --verbose had nothing to show until Ghidra finished or crashed — on a
    # long-running analysis that's an unmonitorable multi-hour black box).
    # Still captured in full either way, so the existing failure diagnostics
    # below are unaffected. stderr is merged into stdout so a crash trace
    # printed there (e.g. the OutOfMemoryError abort that motivated this
    # fix) shows up in both the live stream and the captured snippet,
    # instead of only being visible in Ghidra's own application.log.
    output_lines = []
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    for line in process.stdout:
        output_lines.append(line)
        if verbose:
            print(line, end="")
        if on_line:
            on_line(line)
    process.wait()
    captured = "".join(output_lines)

    if not verbose and process.returncode != 0:
        print(captured)

    if process.returncode != 0:
        raise RuntimeError(
            f"Ghidra exited with code {process.returncode}. "
            "Re-run with --verbose to see full output."
        )

    if not output_path.exists():
        # Surface Ghidra's output so the user can see if the script errored
        snippet = captured[-3000:]
        raise RuntimeError(
            f"Ghidra completed but export not found: {output_path}\n"
            f"Ghidra output (last 3 KB):\n{snippet}"
        )

    return str(output_path)
