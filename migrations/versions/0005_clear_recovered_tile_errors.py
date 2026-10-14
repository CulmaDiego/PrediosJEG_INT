"""Limpia errores de teselas que ya fueron resueltos."""

import json

from alembic import op
import sqlalchemy as sa


revision = "0005_clear_recovered_tile_errors"
down_revision = "0004_on_demand_tiles"
branch_labels = None
depends_on = None


def _person_id(payload_json: str) -> int | None:
    try:
        return int(json.loads(payload_json).get("person_id"))
    except (AttributeError, TypeError, ValueError):
        return None


def upgrade() -> None:
    connection = op.get_bind()
    jobs = connection.execute(
        sa.text(
            "SELECT id, payload_json, status FROM jobs "
            "WHERE job_type = 'PREPARE_WINDOW' "
            "AND status IN ('ERROR', 'COMPLETADO')"
        )
    ).mappings()
    completed_people: set[int] = set()
    failed_jobs: list[tuple[int, int]] = []
    for job in jobs:
        person_id = _person_id(job["payload_json"])
        if person_id is None:
            continue
        if job["status"] == "COMPLETADO":
            completed_people.add(person_id)
        else:
            failed_jobs.append((job["id"], person_id))

    recovered_people = {
        row[0]
        for row in connection.execute(
            sa.text("SELECT id FROM people WHERE orthophoto_status = 'LISTO'")
        )
    }
    superseded_ids = [
        job_id
        for job_id, person_id in failed_jobs
        if person_id in completed_people and person_id in recovered_people
    ]
    if superseded_ids:
        connection.execute(
            sa.text("UPDATE jobs SET status = 'SUPERADO', progress = 100, error = NULL WHERE id IN :ids").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": superseded_ids},
        )
    if recovered_people:
        connection.execute(
            sa.text("UPDATE people SET status = 'PENDIENTE' WHERE status = 'ERROR' AND orthophoto_status = 'LISTO'")
        )


def downgrade() -> None:
    pass