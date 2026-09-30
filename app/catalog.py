from __future__ import annotations

import unicodedata
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Municipality


RASTER_EXTENSIONS = {".ecw", ".tif", ".tiff"}


def municipality_key(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name.strip())
    return "".join(character for character in decomposed if not unicodedata.combining(character)).casefold()


def raster_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in RASTER_EXTENSIONS)


def synchronize_municipalities(db: Session, root: Path) -> list[Municipality]:
    existing = {municipality_key(municipality.name): municipality for municipality in db.scalars(select(Municipality)).all()}
    created: list[Municipality] = []
    names = {
        path.relative_to(root).parts[0].strip()
        for path in raster_files(root)
        if path.relative_to(root).parts and path.relative_to(root).parts[0].strip()
    }
    for name in sorted(names, key=municipality_key):
        key = municipality_key(name)
        if key in existing:
            continue
        municipality = Municipality(name=name, active=True)
        db.add(municipality)
        existing[key] = municipality
        created.append(municipality)
    return created


def find_municipality(db: Session, inferred_name: str) -> Municipality | None:
    expected = municipality_key(inferred_name)
    return next(
        (municipality for municipality in db.scalars(select(Municipality)).all() if municipality_key(municipality.name) == expected),
        None,
    )
