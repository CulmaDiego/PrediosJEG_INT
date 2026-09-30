from __future__ import annotations

from pathlib import Path

from app.importer import normalize_phone, parse_coordinates, validate_import


def test_csv_is_fully_validated(tmp_path: Path):
    path = tmp_path / "personas.csv"
    path.write_text("NOMBRE;CELULAR;COORDENADAS;OBSERVACION\nAna;300 123 4567;6.294,-75.031;Ok\nSin celular;;6.2,-75.0;Error\n", encoding="utf-8")
    rows, errors = validate_import(path, "personas.csv")
    assert len(rows) == 1
    assert rows[0]["phone"] == "3001234567"
    assert errors[0]["fila"] == 3


def test_phone_and_coordinates_normalization():
    assert normalize_phone("+57 300-123-4567") == "3001234567"
    assert parse_coordinates("6.294,-75.031") == (-75.031, 6.294)

