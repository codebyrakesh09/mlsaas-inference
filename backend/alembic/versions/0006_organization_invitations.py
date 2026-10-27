from alembic import op
import sqlalchemy as sa


revision = '0006_organization_invitations'
down_revision = '0005_request_completion_time'
branch_labels = None
depends_on = None


def upgrade():
    if 'organization_invitations' not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            'organization_invitations',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('email', sa.String(length=255), nullable=False),
            sa.Column('role', sa.String(length=20), nullable=False),
            sa.Column('token_hash', sa.String(length=64), nullable=False, unique=True),
            sa.Column('invited_by', sa.Integer(), nullable=False),
            sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['invited_by'], ['users.id']),
        )
        op.create_index(
            'ix_organization_invitations_organization_id',
            'organization_invitations',
            ['organization_id'],
        )
        op.create_index(
            'ix_organization_invitations_email',
            'organization_invitations',
            ['email'],
        )


def downgrade():
    if 'organization_invitations' in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table('organization_invitations')