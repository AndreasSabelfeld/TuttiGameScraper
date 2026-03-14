import os
from typing import Any, Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from src.db.models import Base

os.makedirs("data/processed", exist_ok=True)

DATABASE_URL = "sqlite:///data/processed/pokemon_db.sqlite"

engine = create_engine(DATABASE_URL, echo=False)                            # set echo to True for debugging

# SessionLocal is a factory, which creates our database sessions
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Creates all database tables, if they don't already exist."""

    Base.metadata.create_all(bind=engine)
    print("Database and tables successfully initialized.")


def get_session() -> Generator[Session, Any, None]:
    """
    Helper method to get a session from the database.
    :return: Session
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()