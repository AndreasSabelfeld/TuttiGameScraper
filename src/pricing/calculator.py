from src.db.database import SessionLocal
from src.db.models import Listing, Card


def calculate_arbitrage():
    print("Bot: Starting Arbitrage Calculator...\n")
    db = SessionLocal()

    try:
        # Get all listings that have processed cards
        listings = db.query(Listing).filter(Listing.status == "PROCESSED_VISION").all()

        if not listings:
            print("Bot: No fully processed listings found to calculate.")
            return

        for listing in listings:
            total_value = 0.0

            # Sum up the value of all cards attached to this listing
            for card in listing.cards:
                if card.estimated_price:
                    total_value += card.estimated_price

            # Save the total to the database
            listing.total_estimated_value = round(total_value, 2)

        db.commit()
        print(f"Bot: Calculation complete!")

    except Exception as e:
        print(f"Error during calculation: {e}")
        db.rollback()
    finally:
        db.close()
