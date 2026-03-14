import os
import json
import asyncio
from google import genai
from google.genai import types
from dotenv import load_dotenv
from src.db.database import SessionLocal
from src.db.models import Card

load_dotenv()
API_KEY = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY)
MODEL_ID = "gemini-3.1-flash-lite-preview"

BATCH_SIZE = 100  # For parallel mode

PROMPT = """
You are an expert Pokemon TCG appraiser. Look at this cropped image of a Pokemon card.
Your goal is to extract the exact Card Name and the Set Number.

CRITICAL INSTRUCTIONS:
1. Look closely at the BOTTOM LEFT or BOTTOM RIGHT corner for the Set Number (e.g., "004/165", "TG13/TG30", "112/105", or "SWSH250"). 
2. The card might be in English, German, or Japanese. Output the name exactly as printed on the card.
3. Ignore HP numbers, attack damage numbers, or illustrator names.
4. If the image is just a piece of artwork, a table texture, or clearly NOT a full Pokemon card, return "Unknown" for both fields.
5. If the card is too blurry or covered by glare to read the number, return "Unknown" for both fields.

Respond STRICTLY in JSON format: {"card_name": "...", "set_number": "..."}
"""


async def analyze_card_sequential() -> None:
    """
    Sequential analyzing (UNCAPPED SPEED - Paid Tier)
    :return: None
    """
    print(f"Bot: Starting Gemini Vision (Uncapped Sequential Mode)...")
    db = SessionLocal()

    try:
        cards_to_identify = db.query(Card).filter(
            (Card.detected_name == None) | (Card.detected_name == "Error")
        ).all()

        if not cards_to_identify:
            print("Bot: No cards to identify.")
            return

        print(f"Bot: Found {len(cards_to_identify)} cards. Processing at maximum speed...\n")

        processed_count = 0

        for i, card in enumerate(cards_to_identify):
            if not os.path.exists(card.cropped_image_path):
                print(f"  -> [Card {card.id}] Image missing at {card.cropped_image_path}. Skipping.")
                card.detected_name = "Error"
                continue

            try:
                with open(card.cropped_image_path, "rb") as f:
                    image_data = f.read()

                image_part = types.Part.from_bytes(data=image_data, mime_type="image/jpeg")

                response = await client.aio.models.generate_content(
                    model=MODEL_ID,
                    contents=[PROMPT, image_part],
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )

                if response.text:
                    result_data = json.loads(response.text)
                    # If Gemini wrapped our dictionary in a list, extract the first item
                    if isinstance(result_data, list):
                        if len(result_data) > 0 and isinstance(result_data[0], dict):
                            result_data = result_data[0]
                        else:
                            # If it gave us a list of weird junk, default to empty dict
                            result_data = {}

                    name = result_data.get('card_name', 'Unknown')
                    num = result_data.get('set_number', 'Unknown')

                    card.detected_name = name
                    card.set_info = num
                    print(f"  -> [{i + 1}/{len(cards_to_identify)}] Identified: {name} ({num})")
                    processed_count += 1
                else:
                    print(f"  -> [{i + 1}/{len(cards_to_identify)}] AI returned empty text (possibly safety filter).")
                    card.detected_name = "Safety Blocked"

                db.commit()

            except Exception as e:
                if "429" in str(e):
                    print(f"  !! Rate limit hit (4000 RPM reached). Backing off for 5s...")
                    await asyncio.sleep(5)
                else:
                    print(f"  -> Error on Card {card.id}: {str(e)[:100]}")
                    card.detected_name = "Error"
                    db.commit()

        print(f"\nBot: Successfully classified {processed_count} cards at maximum speed!")

    finally:
        db.close()


async def _process_single_card_api(card_id: int, image_path: str):
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

            # If Gemini wrapped our dictionary in a list, extract the first item
            if isinstance(result_data, list):
                if len(result_data) > 0 and isinstance(result_data[0], dict):
                    result_data = result_data[0]
                else:
                    # If it gave us a list of weird junk, default to empty dict
                    result_data = {}

            name = result_data.get('card_name', 'Unknown')
            num = result_data.get('set_number', 'Unknown')
            return card_id, name, num, None
        else:
            return card_id, "Safety Blocked", "Safety Blocked", None

    except Exception as e:
        return card_id, "Error", "Error", str(e)


async def analyze_card_parallel() -> None:
    """
    pipeline for parallel processing.
    :return: None
    """
    print(f"Bot: Starting Gemini Vision (Parallel Batch Mode)...")
    db = SessionLocal()

    try:
        cards_to_identify = db.query(Card).filter(
            (Card.detected_name == None) | (Card.detected_name == "Error")
        ).all()

        if not cards_to_identify:
            print("Bot: No cards to identify.")
            return

        total_cards = len(cards_to_identify)
        print(f"Bot: Found {total_cards} cards. Processing in batches of {BATCH_SIZE}...\n")

        # Slice the list of cards into chunks of size BATCH_SIZE
        for i in range(0, total_cards, BATCH_SIZE):
            batch = cards_to_identify[i:i + BATCH_SIZE]
            current_batch_num = (i // BATCH_SIZE) + 1
            total_batches = (total_cards + BATCH_SIZE - 1) // BATCH_SIZE

            print(f"--- Processing Batch {current_batch_num}/{total_batches} ({len(batch)} cards) ---")

            # Fire off all API requests in this batch at the exact same time
            tasks = [_process_single_card_api(card.id, card.cropped_image_path) for card in batch]
            results = await asyncio.gather(*tasks)

            # Process results and update DB
            for card_id, name, num, error in results:
                if error:
                    print(f"  -> Card #{card_id} Error: {error[:80]}...")
                    if "429" in error:
                        print(f"  !! Rate limit hit (4000 RPM reached). Backing off for 5s...")
                        await asyncio.sleep(5)
                else:
                    print(f"  -> Card #{card_id} Identified: {name} ({num})")

                # Update the specific card in the DB
                db_card = db.query(Card).filter(Card.id == card_id).first()
                if db_card:
                    db_card.detected_name = "Error" if error else name
                    if not error:
                        db_card.set_info = num

            # Commit the whole batch to the database at once
            db.commit()

        print("\nBot: All parallel work complete.")

    finally:
        db.close()
