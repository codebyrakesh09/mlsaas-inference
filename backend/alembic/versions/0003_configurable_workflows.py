from alembic import op
import sqlalchemy as sa


revision = '0003_configurable_workflows'
down_revision = '0002_custom_request_fields'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if 'request_types' in tables:
        columns = {column['name'] for column in inspector.get_columns('request_types')}
        if 'approval_steps' not in columns:
            op.add_column(
                'request_types',
                sa.Column(
                    'approval_steps',
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'[]'"),
                ),
            )

    if 'requests' in tables:
        columns = {column['name'] for column in inspector.get_columns('requests')}
        if 'workflow_step' not in columns:
            op.add_column(
                'requests',
                sa.Column('workflow_step', sa.Integer(), nullable=False, server_default='0'),
            )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if 'requests' in tables:
        columns = {column['name'] for column in inspector.get_columns('requests')}
        if 'workflow_step' in columns:
            op.drop_column('requests', 'workflow_step')

    if 'request_types' in tables:
        columns = {column['name'] for column in inspector.get_columns('request_types')}
        if 'approval_steps' in columns:
            op.drop_column('request_types', 'approval_steps')