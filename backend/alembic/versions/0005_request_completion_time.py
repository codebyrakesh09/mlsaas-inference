from alembic import op
import sqlalchemy as sa


revision = '0005_request_completion_time'
down_revision = '0004_in_app_notifications'
branch_labels = None
depends_on = None


def upgrade():
    columns = {
        column['name']
        for column in sa.inspect(op.get_bind()).get_columns('requests')
    }
    if 'completed_at' not in columns:
        op.add_column(
            'requests',
            sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        )


def downgrade():
    columns = {
        column['name']
        for column in sa.inspect(op.get_bind()).get_columns('requests')
    }
    if 'completed_at' in columns:
        op.drop_column('requests', 'completed_at')