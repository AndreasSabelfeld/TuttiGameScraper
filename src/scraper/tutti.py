import asyncio
import json
import os
import re
from playwright.async_api import async_playwright
from src.db.database import SessionLocal, init_db
from src.db.models import Listing


def parse_price(price_str: str) -> float:
    if not price_str or "gratis" in price_str.lower():
        return 0.0
    cleaned = re.sub(r'[^\d.,]', '', price_str).replace(',', '.')
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


async def run_hybrid_scraper(max_listings: int = None) -> None:
    """
    Scrapes Tutti for Pokémon listings and saved them to the database.
    :param max_listings: specifies the maximum number of listings to scrape. Defaults to all listings possible.
    :return: None
    """

    print(f"Bot: Starting scraper. Target: {'ALL listings' if max_listings is None else f'{max_listings} listings'}")
    init_db()
    db = SessionLocal()

    scraped_ids = set()     # Keep track of every ID we see in this run
    page_num = 1
    has_more_pages = True
    new_listings_count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            while has_more_pages:
                print(f"Bot: Loading Page {page_num}...")

                url = f"https://www.tutti.ch/de/q/suche/Ak65wb2tlbW9uIGthcnRlbsCUwMDAwA?sorting=newest&page={page_num}&query=pokemon+karten"

                await page.goto(url)
                await page.wait_for_load_state("domcontentloaded")

                try:
                    next_data_script = await page.locator('#__NEXT_DATA__').inner_text()
                    data = json.loads(next_data_script)
                    queries = data["props"]["pageProps"]["dehydratedState"]["queries"]

                    # Depending on how the Next.js cache loads, the listings might be in query[0] or query[1]
                    listings = []
                    for query in queries:
                        if "data" in query["state"] and "listings" in query["state"]["data"]:
                            listings = query["state"]["data"]["listings"]["edges"]
                            break

                except Exception as parse_error:
                    print(f"Bot: Failed to parse page {page_num} data. Stopping pagination. ({parse_error})")
                    break

                # If the page is empty, we reached the end of Tutti
                if not listings:
                    print("Bot: Reached the last page of results.")
                    break

                # Process the listings on this page
                for edge in listings:
                    # Stop if we hit our requested limit
                    if max_listings is not None and len(scraped_ids) >= max_listings:
                        has_more_pages = False
                        break

                    node = edge.get("node", {})
                    listing_id = node.get("listingID")

                    if not listing_id:
                        continue

                    scraped_ids.add(listing_id)

                    # Extract data
                    title = node.get("title", "")
                    price_str = node.get("formattedPrice", "0")
                    asking_price = parse_price(price_str)

                    image_url = "NO_IMAGE"
                    thumbnail = node.get("thumbnail")
                    if thumbnail and "retinaRendition" in thumbnail:
                        image_url = thumbnail["retinaRendition"].get("src", "NO_IMAGE")

                    full_url = f"https://www.tutti.ch/vi/{listing_id}"

                    # Check if it already exists in DB
                    existing_listing = db.query(Listing).filter(Listing.tutti_id == listing_id).first()

                    if existing_listing:
                        # Optional: Update the price if it changed
                        if existing_listing.asking_price != asking_price:
                            existing_listing.asking_price = asking_price
                    else:
                        new_listing = Listing(
                            tutti_id=listing_id,
                            title=title,
                            url=full_url,
                            asking_price=asking_price,
                            image_url=image_url,
                            status="NEW"
                        )
                        db.add(new_listing)
                        new_listings_count += 1
                        print(f"  + New: {title[:30]}... | CHF {asking_price}")

                db.commit()
                page_num += 1

                # wait 1 second between page loads
                await asyncio.sleep(1)

        except Exception as e:
            print(f"An error occurred during scraping: {e}")
        finally:
            await browser.close()

        print(f"\nBot: Scraping finished. Added {new_listings_count} new listings.")
        print(f"Bot: Total unique listings verified in this run: {len(scraped_ids)}")

        if max_listings is None:        # Only perform deletion if we scraped the ENTIRE website (max_listings is None)
            __deletion(db, scraped_ids)
        else:
            print("\nBot: Skipping Database Cleanup (Partial scrape mode active).")

        db.close()


def __deletion(db, scraped_ids: set) -> None:
    """
    Deletes the listings from the database and corresponding images if they weren't scraped a second time,
    since that implies they have been deleted.
    :param db: database session
    :param scraped_ids: scraped ids
    :return: None
    """
    print("\nBot: Performing Database Sync & Cleanup...")
    # Get all listings currently in our DB
    all_db_listings = db.query(Listing).all()
    deleted_count = 0

    # The directory where our downloader saves the images
    image_dir = "data/raw/listings"

    for db_listing in all_db_listings:
        # If a DB listing was not found in our current scrape, it was removed from Tutti
        if db_listing.tutti_id not in scraped_ids:
            print(f"  - Deleting obsolete listing: {db_listing.title[:30]}...")

            image_path = os.path.join(image_dir, f"{db_listing.tutti_id}.jpg")
            if os.path.exists(image_path):
                try:
                    os.remove(image_path)
                    print(f"    -> Deleted associated image: {db_listing.tutti_id}.jpg")
                except OSError as e:
                    print(f"    -> Error deleting image file {image_path}: {e}")

            db.delete(db_listing)
            deleted_count += 1

    if deleted_count > 0:
        db.commit()
        print(f"Bot: Successfully removed {deleted_count} deleted listings from the database and disk.")
    else:
        print("Bot: Database is up to date. No listings or images were deleted.")
