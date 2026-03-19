import asyncio

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from src.pricing.pricecharting import get_live_exchange_rate, CONCURRENCY_LIMIT, background_pricing_task
from src.vision.gemini_classifier import analyze_game_free_tier_generator


async def run_smart_pipeline():
    """Orchestrates Gemini and Playwright to run concurrently."""
    print("Bot: Initializing Concurrent Vision & Pricing Pipeline...")
    usd_to_chf_rate = get_live_exchange_rate()
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    pricing_tasks = []

    async with async_playwright() as p:
        # 1. Launch Browser once for the whole pipeline
        browser = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        await Stealth().apply_stealth_async(context)

        # 2. Start the Gemini Generator
        # Every time Gemini yields a game, we instantly spin up a Playwright task
        async for identified_game in analyze_game_free_tier_generator():
            # create_task pushes the pricing job into the background so Gemini doesn't wait!
            task = asyncio.create_task(
                background_pricing_task(context, identified_game, semaphore, usd_to_chf_rate)
            )
            pricing_tasks.append(task)

        # 3. Cleanup: Gemini is finished, but Playwright might still be pricing the last few games
        if pricing_tasks:
            print("\nBot: Vision complete. Waiting for final pricing tasks to finish...")
            await asyncio.gather(*pricing_tasks)

        await browser.close()
        print("Bot: Pipeline fully complete!")