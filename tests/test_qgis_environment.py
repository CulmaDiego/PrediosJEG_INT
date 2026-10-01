from pathlib import Path
from types import SimpleNamespace

import pytest

from app.gis import qgis_command, qgis_environment


@pytest.mark.parametrize("relative_dir", [Path("share/proj"), Path("apps/proj/share/proj")])
def test_qgis_environment_finds_proj_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative_dir: Path) -> None:
    proj_dir = tmp_path / relative_dir
    proj_dir.mkdir(parents=True)
    (proj_dir / "proj.db").touch()
    monkeypatch.setenv("PROJ_DATA", "C:/ruta/incorrecta")

    env = qgis_environment(SimpleNamespace(qgis_root=tmp_path))

    assert env["PROJ_DATA"] == str(proj_dir)
    assert env["PROJ_LIB"] == str(proj_dir)
    assert env["GDAL_DRIVER_PATH"] == str(tmp_path / "apps/gdal/lib/gdalplugins")
    assert env["GDAL_DATA"] == str(tmp_path / "apps/gdal/share/gdal")


def test_qgis_environment_requires_proj_database(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="proj.db"):
        qgis_environment(SimpleNamespace(qgis_root=tmp_path))


def test_qgis_command_uses_bundled_python_for_tiles(tmp_path: Path) -> None:
    python_dir = tmp_path / "apps" / "Python313"
    script_dir = python_dir / "Scripts"
    script_dir.mkdir(parents=True)
    python = python_dir / "python.exe"
    python.touch()
    script = script_dir / "gdal2tiles.py"
    script.touch()

    assert qgis_command(SimpleNamespace(qgis_root=tmp_path), "gdal2tiles") == [str(python), str(script)]
