import os
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


async def _process_single_card_api(game_id: int, image_path: str):
    """Helper function for the parallel pipeline to hit the API."""
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

            tasks = [_process_single_card_api(game.id, game.cropped_image_path) for game in batch]
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
    Sequential analyzing with strict delays for the Free API Tier (max 15 RPM).
    """
    print(f"Bot: Starting Gemini Vision for Games (Free Tier Mode - 15 RPM Limit)...")
    db = SessionLocal()

    try:
        games_to_identify = db.query(Game).filter(
            (Game.detected_name == None) | (Game.detected_name == "Error")
        ).all()

        if not games_to_identify:
            print("Bot: No games to identify.")
            return

        print(f"Bot: Found {len(games_to_identify)} games. Processing at ~4.2 seconds per game to avoid bans...\n")

        processed_count = 0

        for i, game in enumerate(games_to_identify):
            if not os.path.exists(game.cropped_image_path):
                print(f"  -> [Game {game.id}] Image missing at {game.cropped_image_path}. Skipping.")
                game.detected_name = "Error"
                db.commit()
                continue

            try:
                with open(game.cropped_image_path, "rb") as f:
                    image_data = f.read()

                image_part = types.Part.from_bytes(data=image_data, mime_type="image/jpeg")

                try:
                    # We give Gemini exactly 30 seconds to answer, otherwise we kill the request.
                    response = await asyncio.wait_for(
                        client.aio.models.generate_content(
                            model=MODEL_ID,
                            contents=[PROMPT, image_part],
                            config=types.GenerateContentConfig(response_mime_type="application/json")
                        ),
                        timeout=10.0
                    )
                except asyncio.TimeoutError:
                    print(f"  -> Timeout Error on Game {game.id}: Gemini took longer than 30s.")
                    game.detected_name = "Error"  # Mark as error so it moves on
                    db.commit()
                    continue  # Skip to the next game

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
                print(f"  -> Error on Game {game.id}: {str(e)[:100]}")
                if "429" in str(e):
                    print(f"  !! Rate limit hit. Backing off for 20s...")
                    await asyncio.sleep(20)

                game.detected_name = "Error"
                db.commit()

            # 🚨 CRITICAL FREE TIER THROTTLE 🚨
            # 60 seconds / 15 requests = 4 seconds. We use 4.5 to safely dodge micro-timing bans.
            if i < len(games_to_identify) - 1:
                await asyncio.sleep(4.5)

        print(f"\nBot: Successfully classified {processed_count} games safely on the Free Tier!")

    finally:
        db.close()