"""event dedupe and action claims: ``run_events.client_event_id`` (unique per run, so a
retried SDK event batch never stores an event twice) and ``occurred_at`` (when the agent
emitted it); ``idempotency_actions.claim_token`` (a retried claim from the same SDK call
gets its original ``claimed`` answer instead of ``in_doubt``).

Owner: Nikhil Hiro Ghind (Person 1). Hand-written.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-25 18:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('run_events', sa.Column('client_event_id', sa.Text(), nullable=True))
    op.add_column('run_events', sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        'uq_run_events_client_event',
        'run_events',
        ['run_id', 'client_event_id'],
        unique=True,
        postgresql_where=sa.text('client_event_id IS NOT NULL'),
    )
    op.add_column('idempotency_actions', sa.Column('claim_token', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('idempotency_actions', 'claim_token')
    op.drop_index(
        'uq_run_events_client_event',
        table_name='run_events',
        postgresql_where=sa.text('client_event_id IS NOT NULL'),
    )
    op.drop_column('run_events', 'occurred_at')
    op.drop_column('run_events', 'client_event_id')
