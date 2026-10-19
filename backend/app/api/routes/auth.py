"""Authentication, profile, and session routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.api.deps import CurrentUser, DbSession, auth_rate_limit
from app.core.config import settings
from app.core.security import create_access_token
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    ProfileUpdateRequest,
    RegisterRequest,
    TokenResponse,
    UserRead,
)
from app.services.audit import record_audit
from app.services.auth import authenticate, change_password, register_user, update_profile

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_auth_cookie(response: Response, token: str, expires_in: int) -> None:
    response.set_cookie(
        key=settings.access_token_cookie,
        value=token,
        max_age=expires_in,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=201,
    dependencies=[Depends(auth_rate_limit)],
    summary="Create a professor account",
)
def register(payload: RegisterRequest, db: DbSession, response: Response) -> TokenResponse:
    user = register_user(
        db,
        name=payload.name,
        email=payload.email,
        password=payload.password,
        role=payload.role,
        department=payload.department,
        college_name=payload.college_name,
        timezone=payload.timezone,
    )
    db.flush()
    user, token, expires_in = authenticate(db, email=payload.email, password=payload.password)
    record_audit(
        db,
        user_id=user.id,
        action="auth.registered",
        entity_type="user",
        entity_id=str(user.id),
        detail={"role": str(user.role)},
    )
    db.commit()
    _set_auth_cookie(response, token, expires_in)
    return TokenResponse(
        access_token=token, expires_in=expires_in, user=UserRead.model_validate(user)
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(auth_rate_limit)],
    summary="Sign in and receive a session cookie",
)
def login(payload: LoginRequest, db: DbSession, response: Response) -> TokenResponse:
    user, token, expires_in = authenticate(db, email=payload.email, password=payload.password)
    record_audit(
        db,
        user_id=user.id,
        action="auth.login",
        entity_type="user",
        entity_id=str(user.id),
    )
    db.commit()
    _set_auth_cookie(response, token, expires_in)
    return TokenResponse(
        access_token=token, expires_in=expires_in, user=UserRead.model_validate(user)
    )


@router.post("/logout", status_code=204, summary="Clear the session cookie")
def logout(response: Response) -> None:
    response.delete_cookie(settings.access_token_cookie, path="/")


@router.get("/me", response_model=UserRead, summary="Current account")
def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.patch("/profile", response_model=UserRead, summary="Update my profile")
def update_my_profile(
    payload: ProfileUpdateRequest, user: CurrentUser, db: DbSession
) -> UserRead:
    updated = update_profile(db, user=user, **payload.model_dump(exclude_unset=True))
    record_audit(
        db,
        user_id=user.id,
        action="auth.profile_updated",
        entity_type="user",
        entity_id=str(user.id),
        detail={"fields": sorted(payload.model_fields_set)},
    )
    db.commit()
    return UserRead.model_validate(updated)


@router.post(
    "/change-password",
    response_model=TokenResponse,
    summary="Change my password and rotate the session",
)
def update_password(
    payload: ChangePasswordRequest, user: CurrentUser, db: DbSession, response: Response
) -> TokenResponse:
    change_password(
        db,
        user=user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    token, expires_in = create_access_token(user.id, str(user.role), user.token_version)
    record_audit(
        db,
        user_id=user.id,
        action="auth.password_changed",
        entity_type="user",
        entity_id=str(user.id),
    )
    db.commit()
    _set_auth_cookie(response, token, expires_in)
    return TokenResponse(
        access_token=token, expires_in=expires_in, user=UserRead.model_validate(user)
    )
