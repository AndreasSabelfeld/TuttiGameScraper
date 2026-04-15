import os
import re
import requests
import json
import asyncio

from google import genai
from google.genai import types
from dotenv import load_dotenv
from src.db.database import SessionLocal
from src.db.models import Game

load_dotenv()
API_KEY = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY)
MODEL_ID = "gemini-3.1-flash-lite-preview"
STATE_FILE = ".api_key_state"

BATCH_SIZE = 100  # For parallel mode

PROMPT = """
You are an expert Video Game appraiser. Look at this cropped image of a video game.
Your goal is to extract the exact Game Title (including special editions), the Platform (Console) with its Region, and the Condition.

CRITICAL INSTRUCTIONS:
1. Game Title: Output the exact name of the game. If you see specific variant banners (e.g., "Greatest Hits", "Platinum", "Collector's Edition", "Special Edition", "Player's Choice", "Nintendo Selects"), append it to the title in brackets. Example: "Grand Theft Auto V [Greatest Hits]" or "Halo 3 [Collector's Edition]".
2. Region & Platform: Identify the console the game is for, AND look closely at the age rating logo on the cover to determine the region (it's most probably PAL, since we are looking at Swiss sellers):
   - ESRB rating (E, T, M, E10+) = North America (NTSC). Output JUST the console name (e.g., "Wii", "Playstation 3").
   - PEGI rating (numbers 3, 7, 12, 16, 18 in squares) or USK rating (large diamond with numbers) = Europe. Add "PAL" before the console (e.g., "PAL Wii", "PAL Playstation 3").
   - CERO rating (letters A, B, C, D, Z) or heavy Japanese text on the cover = Japan. Add "JP" before the console (e.g., "JP Wii", "JP Nintendo 64").
3. Condition: 
   - If the image shows only a bare cartridge or a bare CD/DVD disc without its official case, output "loose".
   - If the image shows the game inside its official plastic or cardboard retail box/case, output "cib".
   - If the box is clearly wrapped in original factory cellophane/shrinkwrap (with Y-folds or seal stickers), output "new".
4. If the image is just a console system, a random object, or clearly NOT a video game, return "Unknown" for title and platform, and "loose" for condition.
5. If the image is too blurry to read the title or platform, return "Unknown" for both.

Respond STRICTLY in JSON format: {"game_title": "...", "platform": "...", "condition": "..."}
"""


def get_saved_key_idx() -> int:
    """Reads the last used API key index from a file."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return int(f.read().strip())
        except ValueError:
            return 0
    return 0

def save_key_idx(idx: int) -> None:
    """Saves the current API key index to a file."""
    with open(STATE_FILE, "w") as f:
        f.write(str(idx))


def is_banned_tutti_seller(url: str) -> bool:
    """
    Does a lightning-fast invisible check on the Tutti listing page
    to extract the seller's name from the JSON-LD data.
    """
    if "tutti.ch" not in url:
        return False

    banned_env = os.environ.get("BANNED_TUTTI_SELLERS", "")
    banned_sellers = [seller.strip().lower() for seller in banned_env.split(",") if seller.strip()]

    if not banned_sellers:
        return False

    try:
        # Standard HTTP request (No Playwright needed!)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
        response = requests.get(url, headers=headers, timeout=5)

        if response.status_code != 200:
            return False

        # Extract the JSON-LD block using regex
        match = re.search(r'<script type="application/ld\+json">(.*?)</script>', response.text, re.DOTALL)
        if match:
            data = json.loads(match.group(1))

            # Navigate the JSON-LD to find the seller name
            seller_name = data.get("offers", {}).get("seller", {}).get("name", "").lower()

            if seller_name in banned_sellers:
                print(f"  BOUNCER: Intercepted listing from banned seller '{seller_name}'.")
                return True

    except Exception as e:
        pass  # If the check fails, assume they are safe so we don't miss good deals

    return False


async def analyze_game_sequential() -> None:
    """
    Sequential analyzing (UNCAPPED SPEED - Paid Tier)
    """
    print(f"Bot: Starting Gemini Vision for Games (Uncapped Sequential Mode)...")
    db = SessionLocal()

    try:
        games_to_identify = db.query(Game).filter(
            (Game.detected_name == None) | (Game.detected_name == "Error")
        ).all()

        if not games_to_identify:
            print("Bot: No games to identify.")
            return

        print(f"Bot: Found {len(games_to_identify)} games. Processing at maximum speed...\n")

        processed_count = 0

        for i, game in enumerate(games_to_identify):
            if not os.path.exists(game.cropped_image_path):
                print(f"  -> [Game {game.id}] Image missing at {game.cropped_image_path}. Skipping.")
                game.detected_name = "Error"
                continue

            try:
                with open(game.cropped_image_path, "rb") as f:
                    image_data = f.read()

                image_part = types.Part.from_bytes(data=image_data, mime_type="image/jpeg")

                response = await client.aio.models.generate_content(
                    model=MODEL_ID,
                    contents=[PROMPT, image_part],
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )

                if response.text:
                    result_data = json.loads(response.text)
                    if isinstance(result_data, list):
                        if len(result_data) > 0 and isinstance(result_data[0], dict):
                            result_data = result_data[0]
                        else:
                            result_data = {}

                    name = result_data.get('game_title', 'Unknown')
                    plat = result_data.get('platform', 'Unknown')
                    cond = result_data.get('condition', 'loose')

                    game.detected_name = name
                    game.platform = plat
                    game.condition = cond

                    print(f"  -> [{i + 1}/{len(games_to_identify)}] Identified: {name} | {plat} | {cond.upper()}")
                    processed_count += 1
                else:
                    print(f"  -> [{i + 1}/{len(games_to_identify)}] AI returned empty text (possibly safety filter).")
                    game.detected_name = "Safety Blocked"

                db.commit()

            except Exception as e:
                if "429" in str(e):
                    print(f"  !! Rate limit hit (4000 RPM reached). Backing off for 5s...")
                    await asyncio.sleep(5)
                else:
                    print(f"  -> Error on Game {game.id}: {str(e)[:100]}")
                    game.detected_name = "Error"
                    db.commit()

        print(f"\nBot: Successfully classified {processed_count} games at maximum speed!")

    finally:
        db.close()


async def _process_single_card_api(game_id: int, image_path: str, url: str, banned_cache: dict):
    """Helper function for the parallel pipeline to hit the API."""

    if url:
        # Check if we already know this seller is banned/safe
        if url not in banned_cache:
            # We use to_thread so the HTTP request doesn't freeze the async event loop!
            banned_cache[url] = await asyncio.to_thread(is_banned_tutti_seller, url)

        # If the seller is banned, return immediately and skip the Gemini API!
        if banned_cache[url]:
            # Return exactly what the DB expects so it saves gracefully
            return game_id, "Banned Seller", "Banned Seller", "loose", None

    try:
        with open(image_path, "rb") as f:
            image_data = f.read()

        image_part = types.Part.from_bytes(data=image_data, mime_type="image/jpeg")

        response = await client.aio.models.generate_content(
            model=MODEL_ID,
            contents=[PROMPT, image_part],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )

        if response.text:
            result_data = json.loads(response.text)

            if isinstance(result_data, list):
                if len(result_data) > 0 and isinstance(result_data[0], dict):
                    result_data = result_data[0]
                else:
                    result_data = {}

            name = result_data.get('game_title', 'Unknown')
            plat = result_data.get('platform', 'Unknown')
            cond = result_data.get('condition', 'loose')

            return game_id, name, plat, cond, None
        else:
            return game_id, "Safety Blocked", "Safety Blocked", "loose", None

    except Exception as e:
        return game_id, "Error", "Error", "loose", str(e)


async def analyze_game_parallel() -> None:
    """
    Pipeline for parallel processing.
    """
    print(f"Bot: Starting Gemini Vision for Games (Parallel Batch Mode)...")
    db = SessionLocal()

    try:
        games_to_identify = db.query(Game).filter(
            (Game.detected_name == None) | (Game.detected_name == "Error")
        ).all()

        if not games_to_identify:
            print("Bot: No games to identify.")
            return

        total_games = len(games_to_identify)
        print(f"Bot: Found {total_games} games. Processing in batches of {BATCH_SIZE}...\n")

        for i in range(0, total_games, BATCH_SIZE):
            batch = games_to_identify[i:i + BATCH_SIZE]
            current_batch_num = (i // BATCH_SIZE) + 1
            total_batches = (total_games + BATCH_SIZE - 1) // BATCH_SIZE

            print(f"--- Processing Batch {current_batch_num}/{total_batches} ({len(batch)} games) ---")

            shared_banned_cache = {}

            tasks = [
                _process_single_card_api(
                    game.id,
                    game.cropped_image_path,
                    game.listing.url,  # <--- Pass the parent URL
                    shared_banned_cache  # <--- Pass the shared memory cache
                )
                for game in batch
            ]
            results = await asyncio.gather(*tasks)

            for game_id, name, plat, cond, error in results:
                if error:
                    print(f"  -> Game #{game_id} Error: {error[:80]}...")
                    if "429" in error:
                        print(f"  !! Rate limit hit. Backing off for 5s...")
                        await asyncio.sleep(5)
                else:
                    print(f"  -> Game #{game_id} Identified: {name} | {plat} | {cond.upper()}")

                db_game = db.query(Game).filter(Game.id == game_id).first()
                if db_game:
                    db_game.detected_name = "Error" if error else name
                    if not error:
                        db_game.platform = plat
                        db_game.condition = cond

            db.commit()

        print("\nBot: All parallel work complete.")

    finally:
        db.close()


async def analyze_game_free_tier() -> None:
    """
    Sequential analyzing with strict delays and automatic API Key rotation.
    """
    print(f"Bot: Starting Gemini Vision for Games (Free Tier Mode - Key Rotation Enabled)...")
    db = SessionLocal()

    # 1. Setup API Key Rotation
    available_keys = [
        os.environ.get("GEMINI_API_KEY"),
        os.environ.get("GEMINI_API_KEY_2"),
        os.environ.get("GEMINI_API_KEY_3")
    ]
    # Filter out any empty/None keys if you only have 2
    available_keys = [k for k in available_keys if k]

    current_key_idx = get_saved_key_idx() % len(available_keys)
    active_client = genai.Client(api_key=available_keys[current_key_idx])

    consecutive_429_count = 0

    try:
        games_to_identify = db.query(Game).filter(
            (Game.detected_name == None) | (Game.detected_name == "Error")
        ).all()

        if not games_to_identify:
            print("Bot: No games to identify.")
            return

        print(f"Bot: Found {len(games_to_identify)} games. Loaded {len(available_keys)} API keys.\n")

        processed_count = 0

        for i, game in enumerate(games_to_identify):
            if not os.path.exists(game.cropped_image_path):
                print(f"  -> [Game {game.id}] Image missing. Skipping.")
                game.detected_name = "Error"
                db.commit()
                continue

            try:
                with open(game.cropped_image_path, "rb") as f:
                    image_data = f.read()

                image_part = types.Part.from_bytes(data=image_data, mime_type="image/jpeg")

                try:
                    # 2. Use the 'active_client' instead of the global 'client'
                    response = await asyncio.wait_for(
                        active_client.aio.models.generate_content(
                            model=MODEL_ID,
                            contents=[PROMPT, image_part],
                            config=types.GenerateContentConfig(response_mime_type="application/json")
                        ),
                        timeout=30.0
                    )
                except asyncio.TimeoutError:
                    print(f"  -> Timeout Error on Game {game.id}: Gemini took longer than 30s.")
                    game.detected_name = "Error"
                    db.commit()
                    consecutive_429_count = 0  # Reset counter on non-429 error
                    continue

                if response.text:
                    result_data = json.loads(response.text)

                    if isinstance(result_data, list):
                        if len(result_data) > 0 and isinstance(result_data[0], dict):
                            result_data = result_data[0]
                        else:
                            result_data = {}

                    name = result_data.get('game_title', 'Unknown')
                    plat = result_data.get('platform', 'Unknown')
                    cond = result_data.get('condition', 'loose')

                    game.detected_name = name
                    game.platform = plat
                    game.condition = cond

                    print(f"  -> [{i + 1}/{len(games_to_identify)}] Identified: {name} | {plat} | {cond.upper()}")
                    processed_count += 1

                    # 3. Reset the 429 counter upon a successful API call
                    consecutive_429_count = 0
                else:
                    print(f"  -> [{i + 1}/{len(games_to_identify)}] AI returned empty text.")
                    game.detected_name = "Safety Blocked"
                    consecutive_429_count = 0

                db.commit()

            except Exception as e:
                error_msg = str(e)

                # 4. Check for Rate Limit Error
                if "429" in error_msg:
                    consecutive_429_count += 1
                    print(f"  !! Rate limit hit (429). Strike {consecutive_429_count}/3.")

                    if consecutive_429_count >= 3:
                        if len(available_keys) > 1:
                            # Move to the next key, loop back to start if at the end of the list
                            current_key_idx = (current_key_idx + 1) % len(available_keys)
                            save_key_idx(current_key_idx)
                            print(f" 3 Strikes! Switching to API Key #{current_key_idx + 1}...")

                            # Initialize a fresh client with the new key
                            active_client = genai.Client(api_key=available_keys[current_key_idx])

                            # Reset the strike counter so the new key gets a fair chance
                            consecutive_429_count = 0
                        else:
                            raise RuntimeError("FATAL: API limits exhausted and no backup keys available. Stopping pipeline.")
                    else:
                        print("  !! Backing off for 20s...")
                        await asyncio.sleep(20)
                else:
                    print(f"  -> Unexpected Error on Game {game.id}: {error_msg[:100]}")
                    consecutive_429_count = 0

                game.detected_name = "Error"
                db.commit()

            # 60 seconds / 15 requests = 4 seconds. We use 4.5 to safely dodge micro-timing bans.
            if i < len(games_to_identify) - 1:
                await asyncio.sleep(4.5)

        print(f"\nBot: Successfully classified {processed_count} games!")

    finally:
        db.close()


async def analyze_game_free_tier_generator():
    """
    Generates identified games one by one for the pricing engine to consume immediately.
    """
    print(f"Bot: Starting Gemini Vision (Producer Mode)...")
    db = SessionLocal()

    available_keys = [k for k in [os.environ.get("GEMINI_API_KEY"),
                                  os.environ.get("GEMINI_API_KEY_2"),
                                  os.environ.get("GEMINI_API_KEY_3")] if k]
    current_key_idx = get_saved_key_idx() % len(available_keys)
    active_client = genai.Client(api_key=available_keys[current_key_idx]) if available_keys else None
    consecutive_429_count = 0

    try:
        games_to_identify = db.query(Game).filter(
            (Game.detected_name == None) | (Game.detected_name == "Error")
        ).all()

        if not games_to_identify:
            print("Bot: No games need identification.")
            return

        banned_url_cache = {}

        for i, game in enumerate(games_to_identify):
            if not os.path.exists(game.cropped_image_path):
                game.detected_name = "Error"
                db.commit()
                continue

            parent_url = game.listing.url

            if parent_url not in banned_url_cache:
                banned_url_cache[parent_url] = await asyncio.to_thread(is_banned_tutti_seller, parent_url)

            is_banned = banned_url_cache[parent_url]

            if is_banned:
                print(f"  -> [Game {game.id}] Banned seller detected. Skipping AI Vision.")
                game.detected_name = "Banned Seller"
                db.commit()
                continue  # Skip the Gemini Vision call completely!

            try:
                with open(game.cropped_image_path, "rb") as f:
                    image_data = f.read()

                image_part = types.Part.from_bytes(data=image_data, mime_type="image/jpeg")

                response = await asyncio.wait_for(
                    active_client.aio.models.generate_content(
                        model=MODEL_ID,
                        contents=[PROMPT, image_part],
                        config=types.GenerateContentConfig(response_mime_type="application/json")
                    ), timeout=30.0
                )

                if response.text:
                    result_data = json.loads(response.text)
                    if isinstance(result_data, list) and len(result_data) > 0:
                        result_data = result_data[0]

                    name = result_data.get('game_title', 'Unknown')
                    plat = result_data.get('platform', 'Unknown')
                    cond = result_data.get('condition', 'loose')

                    game.detected_name = name
                    game.platform = plat
                    game.condition = cond
                    db.commit()

                    print(f"  -> [Vision] Identified: {name} | {plat}")

                    if name != "Unknown":
                        yield {
                            "game_id": game.id,
                            "name": name,
                            "platform": plat,
                            "condition": cond
                        }

                consecutive_429_count = 0

            except Exception as e:
                error_msg = str(e)

                # Check for Rate Limit AND Server Overload (503/500)
                if "429" in error_msg or "503" in error_msg:
                    code = 429 if "429" in error_msg else 503
                    if code == 429:
                        print(f"  !! Rate limit hit ({code}). Strike {consecutive_429_count}/3.")
                        consecutive_429_count += 1
                    else:
                        print(f"  !! Server overload ({code}).")
                        raise RuntimeError("FATAL: Server Overload")

                    if consecutive_429_count >= 3:
                        if len(available_keys) > 1:
                            # Move to the next key, loop back to start if at the end of the list
                            current_key_idx = (current_key_idx + 1) % len(available_keys)
                            save_key_idx(current_key_idx)
                            print(f" 3 Strikes! Switching to API Key #{current_key_idx + 1}...")

                            # Initialize a fresh client with the new key
                            active_client = genai.Client(api_key=available_keys[current_key_idx])

                            # Reset the strike counter so the new key gets a fair chance
                            consecutive_429_count = 0
                        else:
                            raise RuntimeError("FATAL: API limits exhausted and no backup keys available. Stopping pipeline.")
                    else:
                        print("  !! Backing off for 20s...")
                        await asyncio.sleep(20)
                else:
                    print(f"  -> Unexpected Error on Game {game.id}: {error_msg[:100]}")
                    consecutive_429_count = 0

                game.detected_name = "Error"
                db.commit()

            if i < len(games_to_identify) - 1:
                await asyncio.sleep(4.5)  # Wait for rate limit while Playwright works!

    finally:
        db.close()