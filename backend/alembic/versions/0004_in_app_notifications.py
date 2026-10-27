from alembic import op
import sqlalchemy as sa


revision = '0004_in_app_notifications'
down_revision = '0003_configurable_workflows'
branch_labels = None
depends_on = None


def upgrade():
    if 'notifications' not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            'notifications',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('request_id', sa.Integer(), nullable=True),
            sa.Column('message', sa.String(length=500), nullable=False),
            sa.Column('is_read', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['request_id'], ['requests.id'], ondelete='CASCADE'),
        )
        op.create_index('ix_notifications_organization_id', 'notifications', ['organization_id'])
        op.create_index('ix_notifications_user_id', 'notifications', ['user_id'])
        op.create_index('ix_notifications_request_id', 'notifications', ['request_id'])


def downgrade():
    if 'notifications' in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table('notifications')