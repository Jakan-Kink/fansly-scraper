"""Base module for SQLAlchemy declarative models.

This module provides the base class for all SQLAlchemy models in the application.
It uses the modern declarative base approach from SQLAlchemy 2.0+ with async support.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase


class Base(AsyncAttrs, DeclarativeBase):
    """Base class for all SQLAlchemy models.

    This class serves as the declarative base for all database models in the
    application. It inherits from both AsyncAttrs and DeclarativeBase to provide:
    - Async operation support through AsyncAttrs
    - Modern declarative mapping through DeclarativeBase

    All model classes should inherit from this base class to ensure consistent
    behavior and metadata handling, with support for both sync and async operations.

    Example:
        class MyModel(Base):
            __tablename__ = "my_models"
            id: Mapped[int] = mapped_column(primary_key=True)

            # Supports both sync and async operations
            async def async_method(self):
                ...
    """

    pass
