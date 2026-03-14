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
    image_url: Mapped[str] = mapped_column(String)      # Stores the URL of the listing's main picture

    total_estimated_value: Mapped[Optional[float]] = mapped_column(Float, default=None)

    # status tracking (e.g. "NEW", "IMAGES_DOWNLOADED", "PROCESSED", "PROFITABLE")
    status: Mapped[str] = mapped_column(String, default="NEW")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # relation (One-to-Many): a listing has multiple cards.
    # cascade="all, delete-orphan": delete all cards when listing is deleted
    cards: Mapped[List["Card"]] = relationship(back_populates="listing", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Listing(tutti_id='{self.tutti_id}', title='{self.title}', price={self.asking_price})>"


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(primary_key=True)

    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))

    detected_name: Mapped[Optional[str]] = mapped_column(String)    # e.g. "Glurak"
    set_info: Mapped[Optional[str]] = mapped_column(String)         # e.g. "Base Set 4/102"

    estimated_price: Mapped[Optional[float]] = mapped_column(Float)

    cropped_image_path: Mapped[Optional[str]] = mapped_column(String)

    pricecharting_url = mapped_column(String, nullable=True)
    pricecharting_image_url = mapped_column(String, nullable=True)

    listing: Mapped["Listing"] = relationship(back_populates="cards")

    def __repr__(self) -> str:
        return f"<Card(name='{self.detected_name}', estimated_price={self.estimated_price})>"
