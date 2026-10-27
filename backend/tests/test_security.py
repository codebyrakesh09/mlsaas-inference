import os
from datetime import datetime, timedelta, timezone

import jwt
import pytest

os.environ.setdefault('SECRET_KEY', 'flowdesk-test-secret')
os.environ.setdefault('DATABASE_URL', 'sqlite+pysqlite:///:memory:')

from app import security


def test_non_select_fields_ignore_default_options():
    from app.routers.organizations import FormFieldDefinition

    field = FormFieldDefinition(
        key='f_f1ghhi48wtr7y3tf6dv2upwbpyw95w5',
        label='string',
        field_type='text',
        required=False,
        options=['string'],
    )

    assert field.options is None


def test_decode_token_returns_user_id(monkeypatch):
    monkeypatch.setattr(
        security,
        'SECRET_KEY',
        'test-secret-that-is-long-enough-for-hs256-signatures',
    )
    token = security.create_token(17)

    assert security.decode_token(token) == 17


@pytest.mark.parametrize(
    'claims',
    [
        {'sub': 'not-an-id', 'exp': datetime.now(timezone.utc) + timedelta(hours=1)},
        {'sub': '17', 'exp': datetime.now(timezone.utc) - timedelta(hours=1)},
    ],
)
def test_decode_token_rejects_invalid_claims(monkeypatch, claims):
    test_secret = 'test-secret-that-is-long-enough-for-hs256-signatures'
    monkeypatch.setattr(security, 'SECRET_KEY', test_secret)
    token = jwt.encode(claims, test_secret, algorithm='HS256')

    with pytest.raises(jwt.InvalidTokenError):
        security.decode_token(token)


def test_current_user_endpoint_requires_valid_bearer_token(monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base, get_db
    from app.main import app
    from app.models import User
    from app import security

    engine = create_engine(
        'sqlite+pysqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    with test_session() as db:
        user = User(
            name='Token User',
            email='token@example.test',
            password_hash='never-return-this',
            role='employee',
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        user_id = user.id

    def override_get_db():
        with test_session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(
        security,
        'SECRET_KEY',
        'test-secret-that-is-long-enough-for-hs256-signatures',
    )
    try:
        with TestClient(app) as client:
            assert client.get('/auth/me').status_code == 401
            assert client.get(
                '/auth/me', headers={'Authorization': 'Bearer invalid'}
            ).status_code == 401

            token = security.create_token(user_id)
            response = client.get(
                '/auth/me', headers={'Authorization': f'Bearer {token}'}
            )
            assert response.status_code == 200
            assert response.json() == {
                'id': user_id,
                'name': 'Token User',
                'email': 'token@example.test',
                'role': 'employee',
            }
            assert 'password_hash' not in response.json()
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_requests_are_isolated_by_organization(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base, get_db
    from app.main import app
    from app.models import User
    from app.routers import organizations
    from app.routers.auth import get_current_user

    engine = create_engine(
        'sqlite+pysqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(organizations, 'ATTACHMENT_STORAGE_PATH', tmp_path)
    with test_session() as db:
        test_user = User(
            name='Test Admin',
            email='admin@example.test',
            password_hash='unused',
            role='admin',
        )
        db.add(test_user)
        db.add(
            User(
                name='Team Member',
                email='member@example.com',
                password_hash='unused',
                role='employee',
            )
        )
        db.commit()
        db.refresh(test_user)
        user_id = test_user.id
        member_id = db.scalar(
            select(User.id).where(User.email == 'member@example.test')
        )
    active_user_id = [user_id]

    def override_get_db():
        with test_session() as db:
            yield db

    def override_current_user():
        with test_session() as db:
            return db.get(User, active_user_id[0])

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_current_user
    try:
        with TestClient(app) as client:
            first_org = client.post('/organizations', json={'name': 'First Org'})
            assert first_org.status_code == 201
            first_org_id = first_org.json()['id']

            added_member = client.post(
                '/organizations/members',
                headers={'X-Organization-Id': str(first_org_id)},
                json={'email': 'member@example.com', 'role': 'member'},
            )
            assert added_member.status_code == 201
            member_id = added_member.json()['id']

            invitation_response = client.post(
                '/organizations/members',
                headers={'X-Organization-Id': str(first_org_id)},
                json={'email': 'rakesh@gmail.com', 'role': 'member'},
            )
            assert invitation_response.status_code == 202
            invitation_token = invitation_response.json()['invitation_token']
            pending_invites = client.get(
                '/organizations/member-invitations',
                headers={'X-Organization-Id': str(first_org_id)},
            )
            assert [item['email'] for item in pending_invites.json()] == [
                'rakesh@gmail.com'
            ]
            registration = client.post(
                '/auth/register',
                json={
                    'name': 'Rakesh',
                    'email': 'rakesh@gmail.com',
                    'password': 'valid-test-password',
                    'invitation_token': invitation_token,
                },
            )
            assert registration.status_code == 201
            assert registration.json()['organization_id'] == first_org_id
            assert client.get(
                '/organizations/member-invitations',
                headers={'X-Organization-Id': str(first_org_id)},
            ).json() == []

            members = client.get(
                '/organizations/members',
                headers={'X-Organization-Id': str(first_org_id)},
            )
            assert {item['email'] for item in members.json()} == {
                'admin@example.test',
                'member@example.com',
                'rakesh@gmail.com',
            }

            request_type = client.post(
                '/organizations/request-types',
                headers={'X-Organization-Id': str(first_org_id)},
                json={
                    'name': 'IT Support',
                    'fields': [
                        {
                            'key': 'severity',
                            'label': 'Severity',
                            'field_type': 'select',
                            'required': True,
                            'options': ['low', 'high'],
                        }
                    ],
                },
            )
            assert request_type.status_code == 201

            workflow_type = client.post(
                '/organizations/request-types',
                headers={'X-Organization-Id': str(first_org_id)},
                json={
                    'name': 'Purchase Approval',
                    'approval_steps': [
                        {'name': 'Manager', 'approver_user_id': member_id},
                        {'name': 'Finance', 'approver_user_id': user_id},
                    ],
                },
            )
            assert workflow_type.status_code == 201
            workflow_request = client.post(
                '/organizations/requests',
                headers={'X-Organization-Id': str(first_org_id)},
                json={
                    'request_type_id': workflow_type.json()['id'],
                    'title': 'New monitor purchase',
                },
            )
            assert workflow_request.status_code == 201
            workflow_request_id = workflow_request.json()['id']
            assert workflow_request.json()['assigned_to'] == member_id

            active_user_id[0] = member_id
            member_notifications = client.get(
                '/organizations/notifications',
                headers={'X-Organization-Id': str(first_org_id)},
            )
            assert member_notifications.status_code == 200
            assert member_notifications.json()[0]['request_id'] == workflow_request_id
            marked_read = client.patch(
                f"/organizations/notifications/{member_notifications.json()[0]['id']}/read",
                headers={'X-Organization-Id': str(first_org_id)},
            )
            assert marked_read.json()['is_read'] is True
            first_approval = client.post(
                f'/organizations/requests/{workflow_request_id}/decision',
                headers={'X-Organization-Id': str(first_org_id)},
                json={'status': 'approved'},
            )
            assert first_approval.status_code == 200
            assert first_approval.json()['status'] == 'submitted'
            assert first_approval.json()['assigned_to'] == user_id
            assert first_approval.json()['workflow_step'] == 1

            active_user_id[0] = user_id
            admin_notifications = client.get(
                '/organizations/notifications',
                headers={'X-Organization-Id': str(first_org_id)},
            )
            assert admin_notifications.status_code == 200
            assert any(
                item['request_id'] == workflow_request_id
                for item in admin_notifications.json()
            )
            final_approval = client.post(
                f'/organizations/requests/{workflow_request_id}/decision',
                headers={'X-Organization-Id': str(first_org_id)},
                json={'status': 'approved'},
            )
            assert final_approval.status_code == 200
            assert final_approval.json()['status'] == 'approved'

            missing_form_data = client.post(
                '/organizations/requests',
                headers={'X-Organization-Id': str(first_org_id)},
                json={'request_type_id': request_type.json()['id'], 'title': 'Laptop issue'},
            )
            assert missing_form_data.status_code == 422

            created = client.post(
                '/organizations/requests',
                headers={'X-Organization-Id': str(first_org_id)},
                json={
                    'request_type_id': request_type.json()['id'],
                    'title': 'Laptop issue',
                    'form_data': {'severity': 'high'},
                },
            )
            assert created.status_code == 201
            assert created.json()['form_data'] == {'severity': 'high'}
            request_id = created.json()['id']

            uploaded = client.post(
                f'/organizations/requests/{request_id}/attachments',
                headers={'X-Organization-Id': str(first_org_id)},
                files={'file': ('device.txt', b'device details', 'text/plain')},
            )
            assert uploaded.status_code == 201
            attachment_id = uploaded.json()['id']
            attachments = client.get(
                f'/organizations/requests/{request_id}/attachments',
                headers={'X-Organization-Id': str(first_org_id)},
            )
            assert attachments.json()[0]['filename'] == 'device.txt'
            download = client.get(
                f'/organizations/requests/{request_id}/attachments/{attachment_id}',
                headers={'X-Organization-Id': str(first_org_id)},
            )
            assert download.content == b'device details'

            assignment = client.post(
                f'/organizations/requests/{request_id}/assignment',
                headers={'X-Organization-Id': str(first_org_id)},
                json={'user_id': member_id},
            )
            assert assignment.status_code == 200
            active_user_id[0] = member_id
            inbox = client.get(
                '/organizations/inbox',
                headers={'X-Organization-Id': str(first_org_id)},
            )
            assert [item['id'] for item in inbox.json()] == [request_id]
            active_user_id[0] = user_id

            first_list = client.get(
                '/organizations/requests',
                headers={'X-Organization-Id': str(first_org_id)},
            )
            assert {item['id'] for item in first_list.json()} == {
                request_id,
                workflow_request_id,
            }

            decision = client.post(
                f'/organizations/requests/{request_id}/decision',
                headers={'X-Organization-Id': str(first_org_id)},
                json={'status': 'approved', 'note': 'Looks good'},
            )
            assert decision.status_code == 200
            assert decision.json()['status'] == 'approved'

            report = client.get(
                '/organizations/reports/requests',
                headers={'X-Organization-Id': str(first_org_id)},
            )
            assert report.status_code == 200
            assert report.json()['total_requests'] == 2
            assert report.json()['by_status']['approved'] == 2
            assert report.json()['average_turnaround_hours'] is not None

            history = client.get(
                f'/organizations/requests/{request_id}/history',
                headers={'X-Organization-Id': str(first_org_id)},
            )
            assert [item['action'] for item in history.json()] == [
                'created',
                'attachment_added',
                'assigned',
                'approved',
            ]

            comment = client.post(
                f'/organizations/requests/{request_id}/comments',
                headers={'X-Organization-Id': str(first_org_id)},
                json={'text': 'I will follow up tomorrow.'},
            )
            assert comment.status_code == 201
            comments = client.get(
                f'/organizations/requests/{request_id}/comments',
                headers={'X-Organization-Id': str(first_org_id)},
            )
            assert comments.json()[0]['text'] == 'I will follow up tomorrow.'

            second_org = client.post('/organizations', json={'name': 'Second Org'})
            assert second_org.status_code == 201
            second_org_id = second_org.json()['id']

            cross_org_read = client.get(
                f'/organizations/requests/{request_id}',
                headers={'X-Organization-Id': str(second_org_id)},
            )
            assert cross_org_read.status_code == 404
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()

        