"""Compatibility entry point for the synthetic DEMO evaluation suite.

Uses the same authenticated evaluation flow. May incur API costs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.evaluate_api import main

if __name__ == '__main__':
    raise SystemExit(main())
