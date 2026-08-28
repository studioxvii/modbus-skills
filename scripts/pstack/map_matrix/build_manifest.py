#!/usr/bin/env python3
"""Build or refresh the map-matrix manifest from private/modbus-maps/."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MAPS = ROOT / "private" / "modbus-maps"
OUT = Path(__file__).resolve().parent / "manifest.json"
WORKING_SUFFIXES = {".pdf", ".xlsx"}


def slugify(name: str) -> str:
    base = Path(name).stem
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower()
    return slug[:80] or "map"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if not MAPS.is_dir():
        print(f"missing maps dir: {MAPS}", file=sys.stderr)
        return 1

    originals = {}
    originals_dir = MAPS / "_originals"
    if originals_dir.is_dir():
        for path in originals_dir.iterdir():
            if path.is_file():
                originals[path.stem] = {
                    "filename": path.name,
                    "format": path.suffix.lstrip(".").lower(),
                    "sha256": sha256(path),
                    "bytes": path.stat().st_size,
                }

    maps = []
    used_ids: set[str] = set()
    for path in sorted(MAPS.iterdir()):
        if not path.is_file() or path.suffix.lower() not in WORKING_SUFFIXES:
            continue
        if path.name.startswith("."):
            continue
        base_id = slugify(path.name)
        map_id = base_id
        n = 2
        while map_id in used_ids:
            map_id = f"{base_id}-{n}"
            n += 1
        used_ids.add(map_id)
        fmt = path.suffix.lstrip(".").lower()
        original = originals.get(path.stem)
        maps.append(
            {
                "id": map_id,
                "filename": path.name,
                "relative_path": f"private/modbus-maps/{path.name}",
                "format": fmt,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "converted_from": original,
                "pipeline_class": "structured" if fmt == "xlsx" else "pdf",
            }
        )

    payload = {
        "schema_version": "pstack-map-matrix-manifest/v1",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "maps_dir": "private/modbus-maps",
        "count": len(maps),
        "maps": maps,
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"maps": len(maps), "manifest": str(OUT.relative_to(ROOT))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
