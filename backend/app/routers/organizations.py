import os
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from math import isfinite
from typing import Literal
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    History,
    Comment,
    Attachment,
    Notification,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    Request,
    RequestType,
    User,
)
from .auth import get_current_user


router = APIRouter(prefix='/organizations', tags=['organizations'])
ATTACHMENT_STORAGE_PATH = Path(os.environ.get('ATTACHMENT_STORAGE_PATH', '/code/uploads'))
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)


class OrganizationMemberCreate(BaseModel):
    email: EmailStr
    role: Literal['member', 'admin'] = 'member'


class ApprovalStepDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    approver_user_id: int


class RequestTypeCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    fields: list['FormFieldDefinition'] = Field(default_factory=list, max_length=50)
    approval_steps: list[ApprovalStepDefinition] = Field(default_factory=list, max_length=10)

    @field_validator('fields')
    @classmethod
    def fields_have_unique_keys(cls, fields):
        keys = [field.key for field in fields]
        if len(keys) != len(set(keys)):
            raise ValueError('Field keys must be unique')
        return fields


class FormFieldDefinition(BaseModel):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,49}$')
    label: str = Field(min_length=1, max_length=100)
    field_type: Literal['text', 'number', 'boolean', 'select']
    required: bool = False
    options: list[str] | None = None

    @model_validator(mode='after')
    def validate_options(self):
        if self.field_type == 'select':
            if not self.options or len(self.options) != len(set(self.options)):
                raise ValueError('Select fields need non-empty unique options')
        else:
            self.options = None
        return self


class RequestCreate(BaseModel):
    request_type_id: int
    title: str = Field(min_length=3, max_length=200)
    details: str = Field(default='', max_length=10000)
    form_data: dict[str, object] = Field(default_factory=dict)


class RequestDecision(BaseModel):
    status: Literal['approved', 'rejected']
    note: str | None = Field(default=None, max_length=2000)


class RequestAssignment(BaseModel):
    user_id: int


class CommentCreate(BaseModel):
    text: str = Field(min_length=1, max_length=10000)


def get_current_organization(
    x_organization_id: int = Header(),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Organization:
    organization = db.scalar(
        select(Organization)
        .join(OrganizationMembership)
        .where(
            Organization.id == x_organization_id,
            OrganizationMembership.user_id == user.id,
        )
    )
    if organization is None:
        raise HTTPException(status_code=404, detail='Organization not found')
    return organization


def require_organization_admin(
    organization: Organization,
    user: User,
    db: Session,
) -> None:
    role = db.scalar(
        select(OrganizationMembership.role).where(
            OrganizationMembership.organization_id == organization.id,
            OrganizationMembership.user_id == user.id,
        )
    )
    if role != 'admin':
        raise HTTPException(status_code=403, detail='Organization admin role required')


@router.post('', status_code=status.HTTP_201_CREATED)
def create_organization(
    data: OrganizationCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    organization = Organization(name=data.name.strip(), created_by=user.id)
    db.add(organization)
    db.flush()
    db.add(
        OrganizationMembership(
            organization_id=organization.id,
            user_id=user.id,
            role='admin',
        )
    )
    db.commit()
    db.refresh(organization)
    return {
        'id': organization.id,
        'name': organization.name,
        'role': 'admin',
    }


@router.get('')
def list_organizations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(Organization, OrganizationMembership.role)
        .join(OrganizationMembership)
        .where(OrganizationMembership.user_id == user.id)
        .order_by(Organization.id)
    )
    return [
        {'id': organization.id, 'name': organization.name, 'role': role}
        for organization, role in rows
    ]


@router.get('/members')
def list_members(
    organization: Organization = Depends(get_current_organization),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(User, OrganizationMembership.role)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .where(OrganizationMembership.organization_id == organization.id)
        .order_by(User.name, User.id)
    )
    return [
        {'id': member.id, 'name': member.name, 'email': member.email, 'role': role}
        for member, role in rows
    ]


@router.post('/members', status_code=status.HTTP_201_CREATED)
def add_member(
    data: OrganizationMemberCreate,
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_organization_admin(organization, user, db)
    email = str(data.email).lower()
    member = db.scalar(select(User).where(func.lower(User.email) == email))
    if member is None:
        now = datetime.now(timezone.utc)
        pending_invitation = db.scalar(
            select(OrganizationInvitation).where(
                OrganizationInvitation.organization_id == organization.id,
                OrganizationInvitation.email == email,
                OrganizationInvitation.accepted_at.is_(None),
                OrganizationInvitation.expires_at > now,
            )
        )
        if pending_invitation is not None:
            raise HTTPException(
                status_code=409,
                detail='A pending invitation already exists for this email',
            )

        invitation_token = secrets.token_urlsafe(32)
        invitation = OrganizationInvitation(
            organization_id=organization.id,
            email=email,
            role=data.role,
            token_hash=hashlib.sha256(invitation_token.encode()).hexdigest(),
            invited_by=user.id,
            expires_at=now + timedelta(days=7),
        )
        db.add(invitation)
        db.commit()
        db.refresh(invitation)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                'status': 'invitation_pending',
                'email': email,
                'role': data.role,
                'invitation_token': invitation_token,
                'expires_at': invitation.expires_at.isoformat(),
            },
        )

    membership = OrganizationMembership(
        organization_id=organization.id,
        user_id=member.id,
        role=data.role,
    )
    db.add(membership)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail='User is already a member of this organization',
        ) from exc
    return {
        'id': member.id,
        'name': member.name,
        'email': member.email,
        'role': membership.role,
    }


@router.get('/member-invitations')
def list_pending_invitations(
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_organization_admin(organization, user, db)
    invitations = db.scalars(
        select(OrganizationInvitation)
        .where(
            OrganizationInvitation.organization_id == organization.id,
            OrganizationInvitation.accepted_at.is_(None),
            OrganizationInvitation.expires_at > datetime.now(timezone.utc),
        )
        .order_by(OrganizationInvitation.created_at.desc())
    )
    return [
        {
            'id': invitation.id,
            'email': invitation.email,
            'role': invitation.role,
            'expires_at': invitation.expires_at,
        }
        for invitation in invitations
    ]


@router.post('/request-types', status_code=status.HTTP_201_CREATED)
def create_request_type(
    data: RequestTypeCreate,
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_organization_admin(organization, user, db)
    name = data.name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=422, detail='Name must contain at least 2 characters')

    approver_ids = {step.approver_user_id for step in data.approval_steps}
    if approver_ids:
        member_ids = set(
            db.scalars(
                select(OrganizationMembership.user_id).where(
                    OrganizationMembership.organization_id == organization.id,
                    OrganizationMembership.user_id.in_(approver_ids),
                )
            )
        )
        if member_ids != approver_ids:
            raise HTTPException(
                status_code=422,
                detail='Every workflow approver must belong to this organization',
            )

    request_type = RequestType(
        organization_id=organization.id,
        name=name,
        description=data.description,
        form_fields=[field.model_dump() for field in data.fields],
        approval_steps=[step.model_dump() for step in data.approval_steps],
    )
    db.add(request_type)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail='A request type with this name already exists in the organization',
        ) from exc
    db.refresh(request_type)
    return {
        'id': request_type.id,
        'name': request_type.name,
        'description': request_type.description,
        'fields': request_type.form_fields,
        'approval_steps': request_type.approval_steps,
    }


@router.get('/request-types')
def list_request_types(
    organization: Organization = Depends(get_current_organization),
    db: Session = Depends(get_db),
):
    request_types = db.scalars(
        select(RequestType)
        .where(RequestType.organization_id == organization.id)
        .order_by(RequestType.name)
    )
    return [
        {
            'id': item.id,
            'name': item.name,
            'description': item.description,
            'fields': item.form_fields or [],
            'approval_steps': item.approval_steps or [],
        }
        for item in request_types
    ]


def get_request_or_404(
    request_id: int,
    organization: Organization,
    db: Session,
) -> Request:
    request = db.scalar(
        select(Request).where(
            Request.id == request_id,
            Request.organization_id == organization.id,
        )
    )
    if request is None:
        raise HTTPException(status_code=404, detail='Request not found')
    return request


def add_notification(
    db: Session,
    organization_id: int,
    user_id: int,
    request_id: int,
    message: str,
) -> None:
    db.add(
        Notification(
            organization_id=organization_id,
            user_id=user_id,
            request_id=request_id,
            message=message,
        )
    )


@router.post('/requests', status_code=status.HTTP_201_CREATED)
def create_request(
    data: RequestCreate,
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_type = db.scalar(
        select(RequestType).where(
            RequestType.id == data.request_type_id,
            RequestType.organization_id == organization.id,
        )
    )
    if request_type is None:
        raise HTTPException(status_code=404, detail='Request type not found')

    field_definitions = request_type.form_fields or []
    definitions_by_key = {field['key']: field for field in field_definitions}
    unknown_fields = set(data.form_data) - set(definitions_by_key)
    if unknown_fields:
        raise HTTPException(
            status_code=422,
            detail=f'Unknown form fields: {", ".join(sorted(unknown_fields))}',
        )

    for key, definition in definitions_by_key.items():
        value = data.form_data.get(key)
        if value is None or value == '':
            if definition['required']:
                raise HTTPException(status_code=422, detail=f'{key} is required')
            continue

        field_type = definition['field_type']
        valid = (
            isinstance(value, str)
            if field_type == 'text'
            else isinstance(value, (int, float)) and not isinstance(value, bool)
            if field_type == 'number'
            else isinstance(value, bool)
            if field_type == 'boolean'
            else isinstance(value, str) and value in definition['options']
        )
        if (
            field_type == 'number'
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and valid
        ):
            valid = isfinite(value)
        if not valid:
            raise HTTPException(
                status_code=422,
                detail=f'{key} must be a valid {field_type} value',
            )

    approval_steps = request_type.approval_steps or []
    initial_assignee = (
        approval_steps[0]['approver_user_id'] if approval_steps else None
    )
    request = Request(
        organization_id=organization.id,
        type_id=request_type.id,
        created_by=user.id,
        title=data.title,
        details=data.details,
        form_data=data.form_data,
        assigned_to=initial_assignee,
        workflow_step=0,
        status='submitted',
    )
    db.add(request)
    db.flush()
    db.add(
        History(
            request_id=request.id,
            user_id=user.id,
            action='created',
            new_status='submitted',
        )
    )
    if initial_assignee is not None:
        add_notification(
            db,
            organization.id,
            initial_assignee,
            request.id,
            f'Approval needed: {request.title}',
        )
        db.add(
            History(
                request_id=request.id,
                user_id=user.id,
                action='assigned',
                note=f'Assigned to first approver {initial_assignee}',
            )
        )
    db.commit()
    db.refresh(request)
    return {
        'id': request.id,
        'request_type_id': request.type_id,
        'title': request.title,
        'details': request.details,
        'form_data': request.form_data or {},
        'status': request.status,
        'assigned_to': request.assigned_to,
        'workflow_step': request.workflow_step,
        'created_by': request.created_by,
        'created_at': request.created_at,
    }


@router.get('/requests')
def list_requests(
    organization: Organization = Depends(get_current_organization),
    db: Session = Depends(get_db),
):
    requests = db.scalars(
        select(Request)
        .where(Request.organization_id == organization.id)
        .order_by(Request.created_at.desc(), Request.id.desc())
    )
    return [
        {
            'id': item.id,
            'request_type_id': item.type_id,
            'title': item.title,
            'form_data': item.form_data or {},
            'status': item.status,
            'created_by': item.created_by,
            'created_at': item.created_at,
        }
        for item in requests
    ]


@router.get('/reports/requests')
def request_report(
    organization: Organization = Depends(get_current_organization),
    db: Session = Depends(get_db),
):
    total = db.scalar(
        select(func.count(Request.id)).where(
            Request.organization_id == organization.id
        )
    ) or 0
    status_rows = db.execute(
        select(Request.status, func.count(Request.id))
        .where(Request.organization_id == organization.id)
        .group_by(Request.status)
    )
    type_rows = db.execute(
        select(RequestType.name, func.count(Request.id))
        .join(Request, Request.type_id == RequestType.id)
        .where(
            Request.organization_id == organization.id,
            RequestType.organization_id == organization.id,
        )
        .group_by(RequestType.name)
        .order_by(RequestType.name)
    )
    completed = db.execute(
        select(Request.created_at, Request.completed_at).where(
            Request.organization_id == organization.id,
            Request.completed_at.is_not(None),
        )
    )
    durations = [
        (completed_at - created_at).total_seconds() / 3600
        for created_at, completed_at in completed
    ]
    return {
        'total_requests': total,
        'by_status': {request_status: count for request_status, count in status_rows},
        'by_request_type': {name: count for name, count in type_rows},
        'average_turnaround_hours': (
            sum(durations) / len(durations) if durations else None
        ),
    }


@router.get('/inbox')
def list_inbox(
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    requests = db.scalars(
        select(Request)
        .where(
            Request.organization_id == organization.id,
            Request.assigned_to == user.id,
            Request.status == 'submitted',
        )
        .order_by(Request.created_at, Request.id)
    )
    return [
        {
            'id': item.id,
            'request_type_id': item.type_id,
            'title': item.title,
            'status': item.status,
            'created_by': item.created_by,
            'created_at': item.created_at,
        }
        for item in requests
    ]


@router.get('/notifications')
def list_notifications(
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notifications = db.scalars(
        select(Notification)
        .where(
            Notification.organization_id == organization.id,
            Notification.user_id == user.id,
        )
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(100)
    )
    return [
        {
            'id': item.id,
            'request_id': item.request_id,
            'message': item.message,
            'is_read': item.is_read,
            'created_at': item.created_at,
        }
        for item in notifications
    ]


@router.patch('/notifications/{notification_id}/read')
def mark_notification_read(
    notification_id: int,
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notification = db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.organization_id == organization.id,
            Notification.user_id == user.id,
        )
    )
    if notification is None:
        raise HTTPException(status_code=404, detail='Notification not found')
    notification.is_read = True
    db.commit()
    return {'id': notification.id, 'is_read': notification.is_read}


@router.get('/requests/{request_id}')
def read_request(
    request_id: int,
    organization: Organization = Depends(get_current_organization),
    db: Session = Depends(get_db),
):
    request = get_request_or_404(request_id, organization, db)
    return {
        'id': request.id,
        'request_type_id': request.type_id,
        'title': request.title,
        'details': request.details,
        'form_data': request.form_data or {},
        'status': request.status,
        'created_by': request.created_by,
        'assigned_to': request.assigned_to,
        'created_at': request.created_at,
    }


@router.post('/requests/{request_id}/assignment')
def assign_request(
    request_id: int,
    data: RequestAssignment,
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_organization_admin(organization, user, db)
    request = get_request_or_404(request_id, organization, db)
    if request.status != 'submitted':
        raise HTTPException(
            status_code=409,
            detail='Only submitted requests can be assigned',
        )
    request_type = db.get(RequestType, request.type_id)
    if request_type is None:
        raise HTTPException(status_code=404, detail='Request type not found')
    if request_type.approval_steps:
        raise HTTPException(
            status_code=409,
            detail='Requests in an approval workflow cannot be manually assigned',
        )

    is_member = db.scalar(
        select(OrganizationMembership.id).where(
            OrganizationMembership.organization_id == organization.id,
            OrganizationMembership.user_id == data.user_id,
        )
    )
    if is_member is None:
        raise HTTPException(status_code=404, detail='Organization member not found')

    previous_assignee = request.assigned_to
    request.assigned_to = data.user_id
    add_notification(
        db,
        organization.id,
        data.user_id,
        request.id,
        f'Request assigned to you: {request.title}',
    )
    db.add(
        History(
            request_id=request.id,
            user_id=user.id,
            action='assigned',
            note=f'Assigned to user {data.user_id}; previous assignee: {previous_assignee}',
        )
    )
    db.commit()
    return {'id': request.id, 'assigned_to': request.assigned_to}


@router.post('/requests/{request_id}/decision')
def decide_request(
    request_id: int,
    data: RequestDecision,
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request = get_request_or_404(request_id, organization, db)
    if request.status != 'submitted':
        raise HTTPException(
            status_code=409,
            detail='Only submitted requests can be approved or rejected',
        )

    membership_role = db.scalar(
        select(OrganizationMembership.role).where(
            OrganizationMembership.organization_id == organization.id,
            OrganizationMembership.user_id == user.id,
        )
    )
    request_type = db.get(RequestType, request.type_id)
    if request_type is None:
        raise HTTPException(status_code=404, detail='Request type not found')
    approval_steps = request_type.approval_steps or []
    if approval_steps:
        if user.id != request.assigned_to and membership_role != 'admin':
            raise HTTPException(
                status_code=403,
                detail='Only the assigned approver or an organization admin can decide',
            )
    elif membership_role != 'admin':
        raise HTTPException(status_code=403, detail='Organization admin role required')

    old_status = request.status
    action = data.status
    if data.status == 'rejected':
        request.status = 'rejected'
        request.assigned_to = None
    elif approval_steps and request.workflow_step + 1 < len(approval_steps):
        request.workflow_step += 1
        next_approver_id = approval_steps[request.workflow_step].get('approver_user_id')
        if not isinstance(next_approver_id, int):
            raise HTTPException(status_code=500, detail='Workflow approver is invalid')
        request.assigned_to = next_approver_id
        action = 'step_approved'
        add_notification(
            db,
            organization.id,
            next_approver_id,
            request.id,
            f'Approval needed: {request.title}',
        )
    else:
        request.status = 'approved'
        request.assigned_to = None
    if request.status in {'approved', 'rejected'}:
        request.completed_at = datetime.now(timezone.utc)
    db.add(
        History(
            request_id=request.id,
            user_id=user.id,
            action=action,
            old_status=old_status,
            new_status=request.status,
            note=data.note,
        )
    )
    db.commit()
    return {
        'id': request.id,
        'status': request.status,
        'assigned_to': request.assigned_to,
        'workflow_step': request.workflow_step,
    }


@router.get('/requests/{request_id}/history')
def list_request_history(
    request_id: int,
    organization: Organization = Depends(get_current_organization),
    db: Session = Depends(get_db),
):
    request = get_request_or_404(request_id, organization, db)
    history = db.scalars(
        select(History)
        .where(History.request_id == request.id)
        .order_by(History.created_at, History.id)
    )
    return [
        {
            'id': item.id,
            'user_id': item.user_id,
            'action': item.action,
            'old_status': item.old_status,
            'new_status': item.new_status,
            'note': item.note,
            'created_at': item.created_at,
        }
        for item in history
    ]


@router.post('/requests/{request_id}/comments', status_code=status.HTTP_201_CREATED)
def create_comment(
    request_id: int,
    data: CommentCreate,
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request = get_request_or_404(request_id, organization, db)
    text = data.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail='Comment cannot be blank')

    comment = Comment(request_id=request.id, user_id=user.id, text=text)
    db.add(comment)
    db.add(
        History(
            request_id=request.id,
            user_id=user.id,
            action='commented',
            note=text,
        )
    )
    db.commit()
    db.refresh(comment)
    return {
        'id': comment.id,
        'request_id': comment.request_id,
        'user_id': comment.user_id,
        'text': comment.text,
        'created_at': comment.created_at,
    }


@router.get('/requests/{request_id}/comments')
def list_comments(
    request_id: int,
    organization: Organization = Depends(get_current_organization),
    db: Session = Depends(get_db),
):
    request = get_request_or_404(request_id, organization, db)
    comments = db.scalars(
        select(Comment)
        .where(Comment.request_id == request.id)
        .order_by(Comment.created_at, Comment.id)
    )
    return [
        {
            'id': item.id,
            'request_id': item.request_id,
            'user_id': item.user_id,
            'text': item.text,
            'created_at': item.created_at,
        }
        for item in comments
    ]


@router.post(
    '/requests/{request_id}/attachments',
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    request_id: int,
    file: UploadFile = File(...),
    organization: Organization = Depends(get_current_organization),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request = get_request_or_404(request_id, organization, db)
    filename = (file.filename or '').replace('\\', '/').rsplit('/', 1)[-1].strip()
    if not filename or len(filename) > 255 or '\x00' in filename:
        raise HTTPException(status_code=422, detail='Invalid attachment filename')

    content = await file.read(MAX_ATTACHMENT_SIZE + 1)
    if len(content) > MAX_ATTACHMENT_SIZE:
        raise HTTPException(status_code=413, detail='Attachment exceeds the 10 MB limit')

    ATTACHMENT_STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    stored_name = uuid4().hex
    stored_path = ATTACHMENT_STORAGE_PATH / stored_name
    stored_path.write_bytes(content)
    attachment = Attachment(
        request_id=request.id,
        filename=filename,
        stored_name=stored_name,
        uploaded_by=user.id,
    )
    db.add(attachment)
    db.add(
        History(
            request_id=request.id,
            user_id=user.id,
            action='attachment_added',
            note=filename,
        )
    )
    try:
        db.commit()
        db.refresh(attachment)
    except Exception:
        db.rollback()
        stored_path.unlink(missing_ok=True)
        raise
    return {
        'id': attachment.id,
        'request_id': attachment.request_id,
        'filename': attachment.filename,
        'created_at': attachment.created_at,
    }


@router.get('/requests/{request_id}/attachments')
def list_attachments(
    request_id: int,
    organization: Organization = Depends(get_current_organization),
    db: Session = Depends(get_db),
):
    request = get_request_or_404(request_id, organization, db)
    attachments = db.scalars(
        select(Attachment)
        .where(Attachment.request_id == request.id)
        .order_by(Attachment.created_at, Attachment.id)
    )
    return [
        {
            'id': item.id,
            'filename': item.filename,
            'uploaded_by': item.uploaded_by,
            'created_at': item.created_at,
        }
        for item in attachments
    ]


@router.get('/requests/{request_id}/attachments/{attachment_id}')
def download_attachment(
    request_id: int,
    attachment_id: int,
    organization: Organization = Depends(get_current_organization),
    db: Session = Depends(get_db),
):
    request = get_request_or_404(request_id, organization, db)
    attachment = db.scalar(
        select(Attachment).where(
            Attachment.id == attachment_id,
            Attachment.request_id == request.id,
        )
    )
    if attachment is None:
        raise HTTPException(status_code=404, detail='Attachment not found')

    root = ATTACHMENT_STORAGE_PATH.resolve()
    stored_path = (root / attachment.stored_name).resolve()
    if stored_path.parent != root or not stored_path.is_file():
        raise HTTPException(status_code=404, detail='Attachment file not found')
    return FileResponse(
        stored_path,
        filename=attachment.filename,
        media_type='application/octet-stream',
    )