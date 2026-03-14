from src.db.database import init_db, SessionLocal
from src.db.models import Listing, Card


def db_simulated_test():
    init_db()
    db = SessionLocal()

    tutti_id_scraped = "12345678"  # simulated listing

    # check if listing already exists
    existing_listing = db.query(Listing).filter(Listing.tutti_id == tutti_id_scraped).first()

    if not existing_listing:
        print("New listing found. Saving in database...")

        # Create new listing
        new_listing = Listing(
            tutti_id=tutti_id_scraped,
            title="Pokemon Karten Sammlung alt",
            url="https://www.tutti.ch/...",
            asking_price=150.00
        )

        # simulate two cards detected by some sort of AI
        card1 = Card(detected_name="Glurak", estimated_price=120.00, cropped_image_path="data/processed/card1.jpg")
        card2 = Card(detected_name="Pikachu", estimated_price=5.00, cropped_image_path="data/processed/card2.jpg")

        # add cards to the listing in our db
        new_listing.cards.extend([card1, card2])
        db.add(new_listing)
        db.commit()

        print("Successfully added new listing.")
    else:
        print("Listing was already present.")

    db.close()
