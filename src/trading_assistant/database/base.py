"""SQLAlchemy declarative base for future persisted models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base metadata registry; no application data models exist at this stage."""
