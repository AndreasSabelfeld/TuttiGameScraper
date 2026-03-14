import os
import httpx
from src.db.database import SessionLocal
from src.db.models import Listing


IMAGE_DIR = "data/raw/listings"


async def download_image(client: httpx.AsyncClient, url: str, filepath: str) -> bool:
    """Downloads a single image and saves it to the hard drive."""
    try:
        response = await client.get(url, timeout=10.0)
        response.raise_for_status()                             # Throw error if download fails

        with open(filepath, 'wb') as f:
            f.write(response.content)
        return True
    except Exception as e:
        print(f"Error downloading {url}: {e}")
        return False


async def run_downloader():
    print("Bot: Starting Image Downloader...")

    os.makedirs(IMAGE_DIR, exist_ok=True)

    db = SessionLocal()
    try:
        listings_to_process = db.query(Listing).filter(         # Query the database for listings that need their images downloaded
            Listing.status == "NEW",
            Listing.image_url != "NO_IMAGE"
        ).all()

        if not listings_to_process:
            print("Bot: No new listings found that need image downloads.")
            return

        print(f"Bot: Found {len(listings_to_process)} images to download.")

        async with httpx.AsyncClient() as client:
            success_count = 0

            for listing in listings_to_process:
                print(f"Downloading image for listing {listing.tutti_id}...")

                # name the file using the Tutti ID so it matches the database
                filepath = os.path.join(IMAGE_DIR, f"{listing.tutti_id}.jpg")

                success = await download_image(client, listing.image_url, filepath)

                if success:
                    listing.status = "IMAGES_DOWNLOADED"
                    success_count += 1
                else:
                    listing.status = "IMAGE_ERROR"

            db.commit()
            print(f"\nBot: Successfully downloaded {success_count} images to {IMAGE_DIR}/.")

    finally:
        db.close()
