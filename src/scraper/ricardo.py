import asyncio
import json
import urllib.parse
import re
import random
from playwright.async_api import async_playwright
from src.db.database import SessionLocal, engine
from src.db.models_ricardo import Base, RicardoListing, RicardoGame

RICARDO_BASE_URL = "https://www.ricardo.ch/de/c/games-82019/"
MAX_LISTINGS_TO_PROCESS = 250


def parse_chf_price(price_str: str) -> float:
    """Helper to parse raw text prices for the DOM engine."""
    if not price_str or price_str.strip() == "":
        return 0.0
    cleaned = re.sub(r'[^\d.]', '', price_str)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


async def run_hybrid_scraper(max_listings: int = MAX_LISTINGS_TO_PROCESS):
    """
    Scrapes Ricardo using a Dual-Engine approach:
    1. JSON-LD (Lightning fast, locked to Page 1)
    2. DOM Visual Scrape (Fallback for Pages 2+ using Human Click Pagination)
    """
    print("===================================================")
    print(f"   RICARDO DUAL-ENGINE SCRAPER - INITIATED")
    print(f"   Target: {max_listings if max_listings else 'ALL'} listings")
    print("===================================================")

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    async with async_playwright() as p:
        # Hijack the real local Chrome to bypass Cloudflare
        browser = await p.chromium.launch(
            headless=True,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()

        scraped_count = 0
        db_duplicate_count = 0
        total_db_entries = db.query(RicardoListing).count()
        page_num = 1
        has_more_pages = True

        try:
            # Load the very first page OUTSIDE the loop
            page_url = f"{RICARDO_BASE_URL}?offer_type=fixed_price"
            print(f"\nBot: Loading Initial Page 1...")
            print(f"  -> {page_url}")
            await page.goto(page_url)
            await page.wait_for_load_state("domcontentloaded")

            # ==========================================
            # 🚨 COOKIE BANNER CRUSHER 🚨
            # ==========================================
            try:
                print("Bot: Checking for OneTrust cookie banner...")
                accept_button = page.locator("button#onetrust-accept-btn-handler")
                await accept_button.wait_for(state="visible", timeout=4000)
                await accept_button.click()
                print("Bot: Cookie banner destroyed! 🍪🔨")
                await asyncio.sleep(1.5)  # Wait for fade out
            except Exception:
                print("Bot: No cookie banner detected. Proceeding...")

            while has_more_pages:
                print(f"\nBot: Scraping Page {page_num}...")
                await asyncio.sleep(2.0)  # Let React render fully

                extracted_items = []
                engine_used = "UNKNOWN"

                # ==========================================
                # ENGINE 1: Try JSON-LD (LOCKED TO PAGE 1)
                # ==========================================
                if page_num == 1:
                    try:
                        json_ld_script = await page.locator("script#srps-json-ld").first.inner_text(timeout=2000)
                        if json_ld_script:
                            data = json.loads(json_ld_script)
                            for graph_item in data.get("@graph", []):
                                if graph_item.get("@type") == "ItemList":
                                    item_list = graph_item.get("itemListElement", [])
                                    for element in item_list:
                                        item = element.get("item", {})

                                        url = item.get("url", "")
                                        if url and url.startswith("/"):
                                            url = urllib.parse.urljoin("https://www.ricardo.ch", url)

                                        extracted_items.append({
                                            "title": item.get("name", "Unknown Title"),
                                            "url": url,
                                            "image_url": item.get("image", ""),
                                            "price": float(item.get("offers", {}).get("price", 0.0))
                                        })
                                    engine_used = "JSON-LD"
                    except Exception:
                        pass

                # ==========================================
                # ENGINE 2: DOM Visual Scrape (Pages 2+)
                # ==========================================
                if not extracted_items:
                    try:
                        print(f"Bot: Engaging visual DOM scraper for Page {page_num}...")
                        await page.wait_for_selector('[data-testid="regular-results"] a[href^="/de/a/"]', timeout=10000)
                        articles = await page.locator('[data-testid="regular-results"] a[href^="/de/a/"]').all()

                        for article in articles:
                            href = await article.get_attribute("href")
                            url = urllib.parse.urljoin("https://www.ricardo.ch", href) if href else ""

                            img_loc = article.locator("img").first
                            image_url = ""
                            if await img_loc.is_visible():
                                image_url = await img_loc.get_attribute("src") or ""

                            title_loc = article.locator("span.mui-knftza").first
                            title = await title_loc.inner_text() if await title_loc.is_visible() else "Unknown Title"

                            price_loc = article.locator("span.mui-gvg9wj, span.mui-1vra4wj").first
                            price_text = await price_loc.inner_text() if await price_loc.is_visible() else "0"

                            extracted_items.append({
                                "title": title.strip(),
                                "url": url,
                                "image_url": image_url,
                                "price": parse_chf_price(price_text)
                            })
                        engine_used = "DOM Selector"
                    except Exception as e:
                        print("Bot: ⚠️ Timeout waiting for games to render. Reached end of results.")
                        has_more_pages = False
                        break

                # ==========================================
                # EVALUATE AND SAVE RESULTS
                # ==========================================
                if not extracted_items:
                    print(f"Bot: Found 0 items on page {page_num}. Reached the end of Ricardo.")
                    break

                print(f"Bot: Extracted {len(extracted_items)} items using {engine_used} engine.")

                for i, item in enumerate(extracted_items):
                    if max_listings is not None and scraped_count >= max_listings:
                        print(f"\nBot: Reached requested limit of {max_listings} listings. Stopping.")
                        has_more_pages = False
                        break

                    url = item["url"]
                    title = item["title"]
                    price = item["price"]
                    image_url = item["image_url"]

                    original_id = url.strip('/').split('-')[-1] if url else f"manual-{page_num}-{i}"

                    existing_listing = db.query(RicardoListing).filter(
                        RicardoListing.original_id == original_id).first()

                    if existing_listing:
                        db_duplicate_count += 1
                        print(f"\rBot: Page {page_num} -> Game {i + 1}/{len(extracted_items)} [DB DUPLICATE]\033[K",
                              end="", flush=True)
                        scraped_count += 1
                        continue

                    print(f"\rBot: Page {page_num} -> Game {i + 1}/{len(extracted_items)} [NEW: {title[:20]}...]\033[K",
                          end="", flush=True)

                    try:
                        new_listing = RicardoListing(
                            original_id=original_id,
                            listing_url=url,
                            scraper_run="ricardo_dual_v4"
                        )
                        db.add(new_listing)
                        db.commit()

                        new_game = RicardoGame(
                            listing_id=new_listing.id,
                            original_title=title,
                            original_price_chf=price,
                            original_image_url=image_url,
                            scrape_source="ricardo"
                        )
                        db.add(new_game)
                        db.commit()

                        scraped_count += 1

                    except Exception as db_error:
                        db.rollback()
                        print(f"\n  -> Critical DB error on ID {original_id}: {db_error}")
                        continue

                if not has_more_pages:
                    break

                # ==========================================
                # PAGINATION: HUMAN CLICK METHOD (NO NETWORKIDLE)
                # ==========================================
                print(f"\nBot: Attempting to click 'Next Page' button...")
                next_button = page.locator('button[aria-label="Go to next page"]')

                if await next_button.is_disabled():
                    print("Bot: 'Next' button is disabled. Reached the last page.")
                    has_more_pages = False
                else:
                    await next_button.scroll_into_view_if_needed()
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    await next_button.click()

                    page_num += 1

                    # Watch the URL update to confirm the click worked (e.g. ?page=2)
                    try:
                        await page.wait_for_url(f"**page={page_num}**", timeout=8000)
                    except Exception:
                        pass  # Sometimes the URL updates weirdly, just proceed

                    # Hard sleep to allow the React Javascript to swap the pictures out
                    print("Bot: Waiting for React to inject new games...")
                    await asyncio.sleep(random.uniform(3.0, 4.5))

            # --- FINAL SUMMARY ---
            print("\n\n✅ Ricardo Scraping Session Finished.")
            print("===================================================")
            print(f"   Summary: Scanned {scraped_count} total items across {page_num} pages.")
            print(f"   - Added to Database: {scraped_count - db_duplicate_count} new entries")
            print(f"   - Skipped as Duplicates: {db_duplicate_count}")
            print(f"   - Total DB entries: {total_db_entries + (scraped_count - db_duplicate_count)}")
            print("===================================================")

        except Exception as fatal_error:
            print(f"\nFATAL Bot Error: {fatal_error}")
            db.rollback()
        finally:
            await browser.close()
            db.close()


if __name__ == "__main__":
    asyncio.run(run_hybrid_scraper(max_listings=150))