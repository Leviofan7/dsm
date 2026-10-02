"""add_intentspec_and_outbox_update_scenario

Revision ID: fc65b3534b78
Revises: 30c333b725fc
Create Date: 2026-09-07 00:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fc65b3534b78'
down_revision: Union[str, Sequence[str], None] = '30c333b725fc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Create intent_specs only if it doesn't exist yet
    if not bind.dialect.has_table(bind, 'intent_specs'):
        op.create_table('intent_specs',
            sa.Column('id', sa.String(length=36), nullable=False),
            sa.Column('task_id', sa.String(length=128), nullable=False),
            sa.Column('session_id', sa.String(length=36), nullable=True),
            sa.Column('previous_intent_id', sa.String(length=36), nullable=True),
            sa.Column('version', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(length=255), nullable=True),
            sa.Column('summary', sa.Text(), nullable=True),
            sa.Column('requirements_json', sa.Text(), nullable=True),
            sa.Column('constraints_json', sa.Text(), nullable=True),
            sa.Column('status', sa.String(length=50), nullable=True),
            sa.Column('content_hash', sa.String(length=96), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_intent_specs_task_id'), 'intent_specs', ['task_id'], unique=True)

    # Create outbox_events only if it doesn't exist yet
    if not bind.dialect.has_table(bind, 'outbox_events'):
        op.create_table('outbox_events',
            sa.Column('id', sa.String(length=36), nullable=False),
            sa.Column('event_type', sa.String(length=100), nullable=False),
            sa.Column('payload', sa.Text(), nullable=False),
            sa.Column('status', sa.String(length=50), nullable=True),
            sa.Column('retry_count', sa.Integer(), nullable=True),
            sa.Column('last_error', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        # FIX: index on status — the outbox worker polls WHERE status='PENDING'
        op.create_index(op.f('ix_outbox_events_status'), 'outbox_events', ['status'], unique=False)

    # Check which columns scenario_definitions already has, add only missing ones
    inspector = sa.inspect(bind)
    existing_cols = {c['name'] for c in inspector.get_columns('scenario_definitions')}
    new_cols = {
        'intent_id': sa.Column('intent_id', sa.String(length=36), nullable=True),
        'source_task_id': sa.Column('source_task_id', sa.String(length=128), nullable=True),
        'proposed_by_session_id': sa.Column('proposed_by_session_id', sa.String(length=128), nullable=True),
        'mode': sa.Column('mode', sa.String(length=16), nullable=True),
        'approved_by': sa.Column('approved_by', sa.String(length=128), nullable=True),
        'approved_at': sa.Column('approved_at', sa.DateTime(), nullable=True),
        'previous_scenario_id': sa.Column('previous_scenario_id', sa.String(length=36), nullable=True),
    }
    missing = [col for col in new_cols if col not in existing_cols]
    if missing:
        with op.batch_alter_table('scenario_definitions', recreate="always") as batch_op:
            for col_name in missing:
                batch_op.add_column(new_cols[col_name])
            if 'intent_id' in missing:
                batch_op.create_foreign_key('fk_scenario_definitions_intent_id', 'intent_specs', ['intent_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('scenario_definitions') as batch_op:
        batch_op.drop_constraint('fk_scenario_definitions_intent_id', type_='foreignkey')
        batch_op.drop_column('previous_scenario_id')
        batch_op.drop_column('approved_at')
        batch_op.drop_column('approved_by')
        batch_op.drop_column('mode')
        batch_op.drop_column('proposed_by_session_id')
        batch_op.drop_column('source_task_id')
        batch_op.drop_column('intent_id')
    op.drop_index(op.f('ix_outbox_events_status'), table_name='outbox_events')
    op.drop_table('outbox_events')
    op.drop_index(op.f('ix_intent_specs_task_id'), table_name='intent_specs')
    op.drop_table('intent_specs')
