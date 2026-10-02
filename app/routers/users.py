from fastapi import APIRouter, HTTPException, UploadFile, status
from sqlalchemy import select

from app.deps import CurrentUser, DbSession
from app.models import User
from app.schemas.users import PasswordChange, UserOut, UserUpdate
from app.security import hash_password, verify_password
from app.services import storage

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
def get_me(user: CurrentUser) -> User:
    return user


@router.patch("/me", response_model=UserOut)
def update_me(payload: UserUpdate, user: CurrentUser, db: DbSession) -> User:
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "phone" in changes and changes["phone"] != user.phone:
        taken = db.scalar(select(User.id).where(User.phone == changes["phone"], User.id != user.id))
        if taken:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="This phone number is already in use")
    for field, value in changes.items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: PasswordChange, user: CurrentUser, db: DbSession) -> None:
    """Changing the password logs out every other session (existing tokens stop working)."""
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    user.token_version += 1
    db.commit()


@router.put("/me/avatar", response_model=UserOut)
def upload_avatar(file: UploadFile, user: CurrentUser, db: DbSession) -> User:
    data, ext = storage.read_image(file)
    stored = storage.save_image(data, ext, "avatars")
    old_key = user.profile_image_key
    user.profile_image_url, user.profile_image_key = stored.url, stored.key
    db.commit()
    db.refresh(user)
    storage.delete_image(old_key)
    return user
