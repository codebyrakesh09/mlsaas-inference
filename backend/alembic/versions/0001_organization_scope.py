from alembic import op
import sqlalchemy as sa


revision = '0001_organization_scope'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    for table in ('requests', 'request_types'):
        if table not in tables:
            continue

        columns = {column['name'] for column in inspector.get_columns(table)}
        if 'organization_id' not in columns:
            op.add_column(
                table,
                sa.Column('organization_id', sa.Integer(), nullable=True),
            )
            op.create_foreign_key(
                f'fk_{table}_organization_id_organizations',
                table,
                'organizations',
                ['organization_id'],
                ['id'],
                ondelete='CASCADE',
            )
            op.create_index(
                f'ix_{table}_organization_id', table, ['organization_id']
            )

    if 'request_types' in tables:
        unique_constraints = inspector.get_unique_constraints('request_types')
        for constraint in unique_constraints:
            if constraint.get('column_names') == ['name']:
                op.drop_constraint(constraint['name'], 'request_types', type_='unique')

        unique_constraints = sa.inspect(op.get_bind()).get_unique_constraints('request_types')
        if not any(
            constraint.get('column_names') == ['organization_id', 'name']
            for constraint in unique_constraints
        ):
            op.create_unique_constraint(
                'uq_request_types_organization_id_name',
                'request_types',
                ['organization_id', 'name'],
            )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if 'request_types' in tables:
        unique_constraints = inspector.get_unique_constraints('request_types')
        for constraint in unique_constraints:
            if constraint.get('column_names') == ['organization_id', 'name']:
                op.drop_constraint(constraint['name'], 'request_types', type_='unique')
        op.create_unique_constraint(
            'uq_request_types_name', 'request_types', ['name']
        )

    for table in ('requests', 'request_types'):
        if table not in tables:
            continue
        columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns(table)}
        if 'organization_id' in columns:
            op.drop_index(f'ix_{table}_organization_id', table_name=table)
            op.drop_constraint(
                f'fk_{table}_organization_id_organizations', table, type_='foreignkey'
            )
            op.drop_column(table, 'organization_id')