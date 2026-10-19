"""Authentication routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, auth_rate_limit
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserRead
from app.services.audit import record_audit
from app.services.auth import authenticate, register_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=201,
    dependencies=[Depends(auth_rate_limit)],
    summary="Create a professor account",
)
def register(payload: RegisterRequest, db: DbSession) -> TokenResponse:
    user = register_user(
        db,
        name=payload.name,
        email=payload.email,
        password=payload.password,
        role=payload.role,
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
    return TokenResponse(
        access_token=token, expires_in=expires_in, user=UserRead.model_validate(user)
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(auth_rate_limit)],
    summary="Sign in and receive an access token",
)
def login(payload: LoginRequest, db: DbSession) -> TokenResponse:
    user, token, expires_in = authenticate(db, email=payload.email, password=payload.password)
    record_audit(
        db,
        user_id=user.id,
        action="auth.login",
        entity_type="user",
        entity_id=str(user.id),
    )
    db.commit()
    return TokenResponse(
        access_token=token, expires_in=expires_in, user=UserRead.model_validate(user)
    )


@router.get("/me", response_model=UserRead, summary="Current account")
def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
