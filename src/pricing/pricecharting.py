import asyncio
import urllib.parse
import urllib.request
import json
import re
import random
import requests
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
from src.db.database import SessionLocal
from src.db.models import Listing, Game

CONCURRENCY_LIMIT = 1


def get_live_exchange_rate() -> float:
    """Fetches the live USD to CHF exchange rate from a free public API."""
    try:
        url = "https://open.er-api.com/v6/latest/USD"
        response = requests.get(url, timeout=10)
        data = response.json()
        rate = data["rates"]["CHF"]
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


def format_search_query(name: str, platform: str) -> str:
    """Transforms Gemini's output into a PriceCharting query for Video Games."""
    if not platform or platform.lower() == "unknown":
        return name
    # Simply combine the Game Name and the Console (e.g., "Super Mario 64 Nintendo 64")
    return f"{name} {platform}"


def get_price_selector(condition: str) -> str:
    """Maps the game's condition to PriceCharting's specific HTML IDs."""
    cond = condition.lower() if condition else "loose"

    # Fixed to match the HTML ID: 'complete_price'
    if cond == "cib" or cond == "complete":
        return "#complete_price .js-price"
    elif cond == "new" or cond == "sealed":
        return "#new_price .js-price"
    elif cond == "box only":
        return "#box_only_price .js-price"
    elif cond == "manual only":
        return "#manual_only_price .js-price"
    else:
        # Default to loose cartridge/disc
        return "#used_price .js-price"


async def fetch_game_price(context, game_id: int, search_query: str, condition: str, semaphore: asyncio.Semaphore,
                           exchange_rate: float):
    """Worker function (Muted to allow for clean progress bar)."""

    await asyncio.sleep(random.uniform(0.1, 2.5))

    async with semaphore:
        page = await context.new_page()

        try:
            encoded_query = urllib.parse.quote(search_query)
            search_url = f"https://www.pricecharting.com/en/search-products?q={encoded_query}&type=prices"

            await page.goto(search_url)
            await page.wait_for_load_state("domcontentloaded")

            title = await page.title()
            if "Just a moment" in title or "Cloudflare" in title or "Attention Required" in title:
                return game_id, 0.0, None, None, "CLOUDFLARE"

            await asyncio.sleep(random.uniform(1.2, 3.5))

            if await page.locator("#games_table").first.is_visible():
                first_row = page.locator("#games_table tbody tr").first
                if await first_row.is_visible():
                    href = await first_row.locator("td.title a").first.get_attribute("href")
                    if href:
                        if href.startswith("/"):
                            href = "https://www.pricecharting.com" + href

                        await asyncio.sleep(random.uniform(0.3, 1.1))
                        await page.goto(href)
                        await page.wait_for_load_state("domcontentloaded")
                        await asyncio.sleep(random.uniform(1.5, 3.2))

            price_val_usd = 0.0
            pc_url = None
            img_url = None

            price_selector = get_price_selector(condition)

            try:
                await page.locator(price_selector).first.wait_for(state="visible", timeout=3000)

                price_text = await page.locator(price_selector).first.inner_text()
                price_val_usd = parse_usd_price(price_text)
                pc_url = page.url

                img_locator = page.locator(".photo img, .cover img, #cover img").first
                if await img_locator.first.is_visible():
                    img_url = await img_locator.first.get_attribute("src")
                    if img_url and img_url.startswith("//"):
                        img_url = "https:" + img_url
            except Exception as e:
                # If it times out, it means the specific condition price (e.g., CIB) doesn't exist for this game
                pass

            price_val_chf = round(price_val_usd * exchange_rate, 2)
            status = "FOUND" if price_val_usd > 0 else "NOT_FOUND"

            return game_id, price_val_chf, pc_url, img_url, status

        except Exception as e:
            return game_id, 0.0, None, None, f"ERROR: {str(e)[:20]}"

        finally:
            await page.close()
            await asyncio.sleep(random.uniform(0.5, 1.5))


async def run_parallel_pricer():
    print(f"Bot: Starting Parallel PriceCharting Engine ({CONCURRENCY_LIMIT} threads)...")
    db = SessionLocal()

    try:
        games_to_price = db.query(Game).filter(
            Game.detected_name != None,
            Game.detected_name != "Unknown",
            Game.detected_name != "Error",
            Game.detected_name != "Safety Blocked",
            Game.estimated_price == None
        ).all()

        if not games_to_price:
            print("Bot: No valid identified games need pricing.")
            return

        total_games = len(games_to_price)
        print(f"Bot: Found {total_games} games to price.\n")

        usd_to_chf_rate = get_live_exchange_rate()
        print("Bot: Launching browser...")

        semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
        affected_listing_ids = set()

        async with async_playwright() as p:

            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )

            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="Europe/Zurich"
            )

            await Stealth().apply_stealth_async(context)

            tasks = []

            for game in games_to_price:
                affected_listing_ids.add(game.listing_id)
                query = format_search_query(game.detected_name, game.platform)

                # Passing the condition to the worker
                tasks.append(fetch_game_price(context, game.id, query, game.condition, semaphore, usd_to_chf_rate))

            print(f"Bot: Dispatching {total_games} searches to PriceCharting...")

            priced_count = 0
            bar_length = 30

            for coro in asyncio.as_completed(tasks):
                game_id, price_val_chf, pc_url, img_url, status = await coro

                db_game = db.query(Game).filter(Game.id == game_id).first()
                if db_game:
                    db_game.estimated_price = price_val_chf
                    db_game.pricecharting_url = pc_url
                    db_game.pricecharting_image_url = img_url
                    db.commit()
                    priced_count += 1

                percent = (priced_count / total_games) * 100
                filled_length = int(bar_length * priced_count // total_games)
                bar = '█' * filled_length + '-' * (bar_length - filled_length)

                if status == "CLOUDFLARE":
                    print(f"\n [Game {game_id}] Hit Cloudflare wall! Skipping.")

                status_text = f"[{price_val_chf} CHF]" if status == "FOUND" else f"[{status}]"
                print(
                    f"\rBot: Pricing |{bar}| {percent:.1f}% ({priced_count}/{total_games}) -> Game {game_id} {status_text}\033[K",
                    end="", flush=True)

            print("\nBot: Game pricing complete! Closing browser...")
            await browser.close()

        print("Bot: Evaluating Listing statuses...")
        listings_marked = 0

        for lid in affected_listing_ids:
            listing = db.query(Listing).filter(Listing.id == lid).first()
            if not listing:
                continue

            unpriced_valid_games = db.query(Game).filter(
                Game.listing_id == lid,
                Game.detected_name != None,
                Game.detected_name != "Unknown",
                Game.detected_name != "Error",
                Game.detected_name != "Safety Blocked",
                Game.estimated_price == None
            ).count()

            if unpriced_valid_games == 0:
                listings_marked += 1

        db.commit()
        print(f"Bot: Marked {listings_marked} listings as 'PRICED'. Ready for arbitrage calculations!")

    except Exception as e:
        print(f"\nFatal Error in pricing engine: {e}")
        db.rollback()
    finally:
        db.close()


async def background_pricing_task(context, game_data, semaphore, exchange_rate):
    """Background task that runs while Gemini is sleeping."""
    query = format_search_query(game_data["name"], game_data["platform"])

    # We call your existing fetch_game_price function
    game_id, price_val_chf, pc_url, img_url, status = await fetch_game_price(
        context, game_data["game_id"], query, game_data["condition"], semaphore, exchange_rate
    )

    # Open a fast, independent DB session to save the result
    db = SessionLocal()
    try:
        db_game = db.query(Game).filter(Game.id == game_id).first()
        if db_game:
            db_game.estimated_price = price_val_chf
            db_game.pricecharting_url = pc_url
            db_game.pricecharting_image_url = img_url
            db.commit()

            status_text = f"[{price_val_chf} CHF]" if status == "FOUND" else f"[{status}]"
            print(f" [Pricer] Finished Game {game_id}: {status_text}")
    finally:
        db.close()
