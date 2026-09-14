"""Acquire observation packages without altering source files."""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ingest_observations(
    source: str,
    dataset_id: str,
    raw_dir: str | Path,
    manifest_path: str | Path,
    *,
    source_url: str | None = None,
    licence: str | None = None,
    retrieval_date: str | None = None,
) -> dict[str, Any]:
    """Copy a local package or download a URL into ``raw_dir`` and write a manifest.

    The source is written once to a temporary sibling and then atomically renamed.
    Existing raw files are never overwritten. ``data_available_time`` is intentionally
    absent: ingestion does not fabricate historical availability timestamps.
    """
    raw_path = Path(raw_dir)
    raw_path.mkdir(parents=True, exist_ok=True)
    source_path = Path(source)
    filename = source_path.name if source_path.exists() else Path(source.split("?", 1)[0]).name
    if not filename:
        raise ValueError("source must be a local file or URL with a filename")
    destination = raw_path / filename
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing raw file: {destination}")
    temporary = destination.with_name(destination.name + ".part")
    try:
        if source_path.exists():
            shutil.copyfile(source_path, temporary)
        else:
            with urllib.request.urlopen(source, timeout=60) as response, temporary.open("wb") as handle:
                shutil.copyfileobj(response, handle)
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()

    record: dict[str, Any] = {
        "dataset_id": dataset_id,
        "source": source_url or str(source),
        "retrieval_date": retrieval_date or date.today().isoformat(),
        "filename": filename,
        "path": str(destination),
        "sha256": _sha256(destination),
        "licence": licence,
        "data_available_time": None,
    }
    manifest = Path(manifest_path)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"records": [record]}, indent=2) + "\n", encoding="utf-8")
    return record
