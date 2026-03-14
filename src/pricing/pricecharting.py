import asyncio
import urllib.parse
import urllib.request
import json
import re
import random
import requests
from playwright.async_api import async_playwright
from src.db.database import SessionLocal
from src.db.models import Card


CONCURRENCY_LIMIT = 1


def get_live_exchange_rate() -> float:
    """Fetches the live USD to CHF exchange rate from a free public API."""
    print("Bot: Fetching live USD -> CHF exchange rate...")
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        response = requests.get(url, timeout=10)
        data = response.json()
        rate = data["rates"]["CHF"]
        print(f"Bot: Current exchange rate is 1 USD = {rate} CHF")
        return rate
    except Exception as e:
        # If the internet drops or the API is down, use a sensible fallback (approx. March 2026 rate)
        fallback_rate = 0.78
        print(f"Bot: Warning! Could not fetch live exchange rate ({e}). Using fallback rate of {fallback_rate}.")
        return fallback_rate


def parse_usd_price(price_str: str) -> float:
    """Converts a string like '$11.99' or '11,99$' to a float."""
    if not price_str or price_str.strip() == "":
        return 0.0
    cleaned = re.sub(r'[^\d.,]', '', price_str).replace(',', '.')
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def format_search_query(name: str, set_info: str) -> str:
    """Transforms Gemini's output into a PriceCharting query."""
    if not set_info or set_info == "Unknown":
        return name

    numerator = set_info.split('/')[0].strip()
    if numerator.isdigit():
        numerator = str(int(numerator))

    return f"{name} #{numerator}"


async def fetch_card_price(browser, card_id: int, search_query: str, semaphore: asyncio.Semaphore,
                           exchange_rate: float):
    """Worker function that searches a card with randomized human jitter to evade firewalls."""

    # Before we even acquire the semaphore, sleep for a random fraction of a second.
    # This prevents the initial batch of tasks from hitting the site at the exact same millisecond.
    await asyncio.sleep(random.uniform(0.1, 2.5))

    async with semaphore:
        page = await browser.new_page()

        try:
            encoded_query = urllib.parse.quote(search_query)
            search_url = f"https://www.pricecharting.com/de/search-products?q={encoded_query}&type=prices"

            await page.goto(search_url)
            await page.wait_for_load_state("domcontentloaded")

            title = await page.title()
            if "Just a moment" in title or "Cloudflare" in title or "Attention Required" in title:
                print(f"  -> [Card {card_id}] Cloudflare wall hit on search! Skipping to protect IP...")
                return card_id, 0.0, None, None

            await asyncio.sleep(random.uniform(1.2, 3.5))

            # We landed on a search results table
            if await page.locator("#games_table").first.is_visible():
                first_row = page.locator("#games_table tbody tr").first
                if await first_row.is_visible():
                    # Grab the link to the actual product page and navigate there
                    href = await first_row.locator("td.title a").first.get_attribute("href")
                    if href:
                        # Handle relative URLs
                        if href.startswith("/"):
                            href = "https://www.pricecharting.com" + href

                        await asyncio.sleep(random.uniform(0.3, 1.1))

                        await page.goto(href)
                        await page.wait_for_load_state("domcontentloaded")

                        await asyncio.sleep(random.uniform(1.5, 3.2))

            # Now we should be on the actual product page
            price_val_usd = 0.0
            pc_url = None
            img_url = None

            if await page.locator("#used_price").first.is_visible():
                # Get Price
                price_text = await page.locator("#used_price .js-price").first.inner_text()
                price_val_usd = parse_usd_price(price_text)

                # Get the Official URL
                pc_url = page.url

                # Get the Image URL
                img_locator = page.locator(".photo img, .cover img, #cover img").first
                if await img_locator.is_visible():
                    img_url = await img_locator.get_attribute("src")
                    if img_url and img_url.startswith("//"):
                        img_url = "https:" + img_url

            # Convert to CHF
            price_val_chf = round(price_val_usd * exchange_rate, 2)

            if price_val_usd > 0:
                print(f"  -> [Card {card_id}] Found: {price_val_chf} CHF | URL Saved.")
            else:
                print(f"  -> [Card {card_id}] No results found. Defaulting to 0.0 CHF")

            return card_id, price_val_chf, pc_url, img_url

        except Exception as e:
            print(f"  -> [Card {card_id}] Error fetching price: {str(e)[:50]}")
            return card_id, 0.0, None, None

        finally:
            await page.close()
            # Wait a moment before returning the semaphore so the next tab doesn't open instantly
            await asyncio.sleep(random.uniform(0.5, 1.5))


async def run_parallel_pricer():
    print(f"Bot: Starting Parallel PriceCharting Engine ({CONCURRENCY_LIMIT} threads)...")
    db = SessionLocal()

    try:
        cards_to_price = db.query(Card).filter(
            Card.detected_name != None,
            Card.detected_name != "Unknown",
            Card.detected_name != "Error",
            Card.detected_name != "Safety Blocked",
            Card.estimated_price == None
        ).all()

        if not cards_to_price:
            print("Bot: No valid identified cards need pricing.")
            return

        print(f"Bot: Found {len(cards_to_price)} cards to price.\n")

        # Fetch the exchange rate ONCE before starting the parallel workers
        usd_to_chf_rate = get_live_exchange_rate()
        print("Bot: Launching browser...")

        semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            tasks = []

            for card in cards_to_price:
                query = format_search_query(card.detected_name, card.set_info)
                tasks.append(fetch_card_price(browser, card.id, query, semaphore, usd_to_chf_rate))

            print(f"Bot: Dispatching {len(tasks)} searches to PriceCharting...")

            priced_count = 0
            for coro in asyncio.as_completed(tasks):
                card_id, price_val_chf, pc_url, img_url = await coro

                db_card = db.query(Card).filter(Card.id == card_id).first()
                if db_card:
                    db_card.estimated_price = price_val_chf
                    db_card.pricecharting_url = pc_url
                    db_card.pricecharting_image_url = img_url
                    db.commit()
                    priced_count += 1
            print(f"Bot: Successfully evaluated and saved {priced_count} cards (values in CHF).")

            await browser.close()

    finally:
        db.close()
