from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, UniqueConstraint
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)
    team = Column(String, nullable=False, default="service_ops")
    role = Column(String, nullable=False, default="viewer")
    force_password_change = Column(Boolean, nullable=False, default=True)


class UserPermission(Base):
    __tablename__ = "user_permissions"
    __table_args__ = (UniqueConstraint("user_id", "section"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    section = Column(String, nullable=False)
    can_view = Column(Boolean, nullable=False, default=False)
    can_download = Column(Boolean, nullable=False, default=False)
    can_create = Column(Boolean, nullable=False, default=False)
    can_edit = Column(Boolean, nullable=False, default=False)
    can_excel_upload = Column(Boolean, nullable=False, default=False)
    can_slack_import = Column(Boolean, nullable=False, default=False)
    can_file_upload = Column(Boolean, nullable=False, default=False)
    can_import = Column(Boolean, nullable=False, default=False)
    can_delete = Column(Boolean, nullable=False, default=False)
    can_approve = Column(Boolean, nullable=False, default=False)