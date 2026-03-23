import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from src.db.database import Base


class RicardoListing(Base):
    __tablename__ = 'ricardo_listings'

    id = Column(Integer, primary_key=True)
    # This will be derived from the unique part of the Ricardo URL to prevent duplicates
    original_id = Column(String, unique=True, index=True, nullable=False)
    listing_url = Column(String, nullable=False)
    status = Column(String, default='SCRAPED')  # E.g., 'SCRAPED', 'VISION_PROCESSED', 'PRICED'
    scraper_run = Column(String)  # Timestamp or UUID of the run
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    # Relationship to the game data
    games = relationship("RicardoGame", back_populates="listing", cascade="all, delete-orphan")


class RicardoGame(Base):
    __tablename__ = 'ricardo_games'

    id = Column(Integer, primary_key=True)
    listing_id = Column(Integer, ForeignKey('ricardo_listings.id'), nullable=False)

    # Original data extracted from Ricardo
    original_title = Column(String, nullable=False)
    original_price_chf = Column(Float, nullable=False)
    original_image_url = Column(String)

    # Detected/Estimated fields (same structure as tutti to allow unified processing later)
    detected_name = Column(String, default=None)  # The specific game name from Gemini
    condition = Column(String, default=None)  # New, cib, loose, etc.
    condition_detected = Column(String, default=None)  # Raw Gemini detected condition
    price_estimated_chf = Column(Float, default=None)  # The PriceCharting market value
    pricecharting_url = Column(String, default=None)
    pricecharting_image_url = Column(String, default=None)

    # Traceability
    scrape_source = Column(String, default='ricardo')
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    listing = relationship("RicardoListing", back_populates="games")

