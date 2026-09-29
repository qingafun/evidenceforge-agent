"""PyInstaller entry point; keep multiprocessing setup before app imports."""

from multiprocessing import freeze_support
import os
import sys


if __name__ == "__main__":
    freeze_support()

    # Windowed executables do not have console streams. Some dependencies still
    # write diagnostics to them before application logging is configured.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    from evidenceforge.desktop import main

    raise SystemExit(main())
