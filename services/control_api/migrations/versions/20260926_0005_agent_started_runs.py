"""agent-started runs: the ``agent`` trigger, the parent run, an idempotency key for the start,
and the bounded input the starting agent passed.

Hand-written.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26 09:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('agent_runs', sa.Column('parent_run_id', sa.Uuid(), nullable=True))
    op.add_column('agent_runs', sa.Column('start_key', sa.Text(), nullable=True))
    op.add_column('agent_runs', sa.Column('trigger_input', postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True))
    op.create_foreign_key(
        op.f('fk_agent_runs_parent_run_id_agent_runs'),
        'agent_runs',
        'agent_runs',
        ['parent_run_id'],
        ['id'],
    )
    op.drop_constraint(op.f('ck_agent_runs_trigger'), 'agent_runs', type_='check')
    op.create_check_constraint(
        op.f('ck_agent_runs_trigger'), 'agent_runs', "trigger IN ('manual', 'schedule', 'agent')"
    )
    op.create_index(
        'uq_agent_runs_agent_start',
        'agent_runs',
        ['parent_run_id', 'start_key'],
        unique=True,
        postgresql_where=sa.text('parent_run_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index(
        'uq_agent_runs_agent_start',
        table_name='agent_runs',
        postgresql_where=sa.text('parent_run_id IS NOT NULL'),
    )
    op.drop_constraint(op.f('ck_agent_runs_trigger'), 'agent_runs', type_='check')
    op.create_check_constraint(
        op.f('ck_agent_runs_trigger'), 'agent_runs', "trigger IN ('manual', 'schedule')"
    )
    op.drop_constraint(op.f('fk_agent_runs_parent_run_id_agent_runs'), 'agent_runs', type_='foreignkey')
    op.drop_column('agent_runs', 'trigger_input')
    op.drop_column('agent_runs', 'start_key')
    op.drop_column('agent_runs', 'parent_run_id')
