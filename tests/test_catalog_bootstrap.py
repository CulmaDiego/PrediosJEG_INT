from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.models import Job, Municipality, Person, TileConfiguration, TileWindow


def make_settings(tmp_path: Path, radius_m: int) -> Settings:
    data = tmp_path / "data"
    orthos = tmp_path / "orthos"
    orthos.mkdir(exist_ok=True)
    return Settings(
        environment="test",
        data_dir=data,
        orthophoto_root=orthos,
        database_url=f"sqlite:///{(data / 'predios.db').as_posix()}",
        secret_key="catalog-bootstrap-test-secret",
        public_base_url="http://testserver",
        cookie_secure=False,
        qgis_root=Path(r"C:\QGIS"),
        window_radius_m=radius_m,
        tile_min_zoom=15,
        tile_max_zoom=19,
        asset_mode="local",
    )


def test_clean_install_discovers_municipalities_and_queues_catalog(tmp_path: Path):
    settings = make_settings(tmp_path, 600)
    for municipality, filename in (("Cocorna", "rural.ecw"), ("San Rafael", "urbano.tif")):
        path = settings.orthophoto_root / municipality / filename
        path.parent.mkdir(parents=True)
        path.write_bytes(b"raster-placeholder")

    app = create_app(settings, create_schema=True)
    with app.state.Session() as db:
        assert list(db.scalars(select(Municipality.name).order_by(Municipality.name))) == ["Cocorna", "San Rafael"]
        jobs = list(db.scalars(select(Job).where(Job.job_type == "SCAN_CATALOG")))
        assert len(jobs) == 1
        assert jobs[0].status == "PENDIENTE"


def test_environment_default_change_preserves_existing_tile_windows(tmp_path: Path):
    old_settings = make_settings(tmp_path, 350)
    old_app = create_app(old_settings, create_schema=True)
    with old_app.state.Session() as db:
        municipality = Municipality(name="San Rafael")
        db.add(municipality)
        db.flush()
        person = Person(
            municipality_id=municipality.id,
            name="Persona",
            phone="3000000000",
            reference_lon=-75.031,
            reference_lat=6.294,
            orthophoto_status="LISTO",
        )
        db.add(person)
        db.flush()
        db.add(TileWindow(person_id=person.id, relative_dir="old-radius", min_zoom=15, max_zoom=19, radius_m=350, status="LISTO"))
        db.commit()
        person_id = person.id
    old_app.state.engine.dispose()

    new_settings = make_settings(tmp_path, 600)
    new_app = create_app(new_settings)
    with new_app.state.Session() as db:
        person = db.get(Person, person_id)
        window = db.get(TileWindow, person_id)
        assert person.orthophoto_status == "LISTO"
        assert window.status == "LISTO"
        assert window.radius_m == 350
        configuration = db.get(TileConfiguration, 1)
        assert configuration is not None
        assert configuration.radius_m == 350
        jobs = list(db.scalars(select(Job).where(Job.job_type == "PREPARE_WINDOW")))
        assert jobs == []
