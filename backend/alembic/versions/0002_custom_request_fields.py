from alembic import op
import sqlalchemy as sa


revision = '0002_custom_request_fields'
down_revision = '0001_organization_scope'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if 'request_types' in tables:
        columns = {column['name'] for column in inspector.get_columns('request_types')}
        if 'form_fields' not in columns:
            op.add_column(
                'request_types',
                sa.Column(
                    'form_fields',
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'[]'"),
                ),
            )

    if 'requests' in tables:
        columns = {column['name'] for column in inspector.get_columns('requests')}
        if 'form_data' not in columns:
            op.add_column('requests', sa.Column('form_data', sa.JSON(), nullable=True))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if 'requests' in tables:
        columns = {column['name'] for column in inspector.get_columns('requests')}
        if 'form_data' in columns:
            op.drop_column('requests', 'form_data')

    if 'request_types' in tables:
        columns = {column['name'] for column in inspector.get_columns('request_types')}
        if 'form_fields' in columns:
            op.drop_column('request_types', 'form_fields')