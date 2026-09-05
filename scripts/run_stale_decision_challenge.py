#!/usr/bin/env python3
"""Explicit opt-in actual-model stale-decision challenge; never run by repo verification."""
from skill_usability.stale_decision import main

if __name__ == '__main__':
    raise SystemExit(main())
