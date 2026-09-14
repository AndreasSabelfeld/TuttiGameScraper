from typing import List, Optional
from datetime import datetime, timezone
from sqlalchemy import String, Float, DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """
    Base class for all database models.
    """
    pass


class Listing(Base):
    __tablename__ = "listings"

    # primary key in database
    id: Mapped[int] = mapped_column(primary_key=True)

    # ID from tutti
    tutti_id: Mapped[str] = mapped_column(String, unique=True, index=True)

    title: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    asking_price: Mapped[float] = mapped_column(Float)
    image_url: Mapped[str] = mapped_column(String)  # Stores the URL of the listing's main picture

    total_estimated_value: Mapped[Optional[float]] = mapped_column(Float, default=None)

    status: Mapped[str] = mapped_column(String, default="NEW")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # relation (One-to-Many): a listing has multiple games.
    # cascade="all, delete-orphan": delete all games when listing is deleted
    games: Mapped[List["Game"]] = relationship(back_populates="listing", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Listing(tutti_id='{self.tutti_id}', title='{self.title}', price={self.asking_price})>"


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True)

    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))

    detected_name: Mapped[Optional[str]] = mapped_column(String)  # e.g. "Super Mario 64"
    platform: Mapped[Optional[str]] = mapped_column(String)  # e.g. "Nintendo 64"

    condition: Mapped[str] = mapped_column(String, default="loose")  # e.g. "loose", "complete", "new"

    estimated_price: Mapped[Optional[float]] = mapped_column(Float)

    cropped_image_path: Mapped[Optional[str]] = mapped_column(String)

    pricecharting_url: Mapped[Optional[str]] = mapped_column(String)
    pricecharting_image_url: Mapped[Optional[str]] = mapped_column(String)

    listing: Mapped["Listing"] = relationship(back_populates="games")

    def __repr__(self) -> str:
        return f"<Game(name='{self.detected_name}', platform='{self.platform}', condition='{self.condition}', estimated_price={self.estimated_price})>"