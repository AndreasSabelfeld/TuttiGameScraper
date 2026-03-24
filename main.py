import os
import time
import asyncio
import ssl

from dotenv import load_dotenv

from src.pricing.pipeline import run_smart_pipeline
import src.scraper.tutti as tutti
import src.scraper.ricardo as ricardo
from src.scraper.image_downloader import run_downloader
from src.vision.detector import run_vision_pipeline
from src.vision.gemini_classifier import analyze_game_sequential, analyze_game_parallel, analyze_game_free_tier
from src.pricing.pricecharting import run_parallel_pricer
from src.pricing.calculator import calculate_arbitrage
from src.notifications.reporter import generate_and_send_report


def main():
    ssl._create_default_https_context = ssl._create_unverified_context

    print("===================================================")
    print("   VIDO GAME ARBITRAGE BOT - PIPELINE INITIATED")
    print("===================================================")

    start_time = time.time()
    load_dotenv()

    try:
        print("\n>>> PHASE 1: SCRAPING & DOWNLOADING <<<")
        asyncio.run(tutti.run_hybrid_scraper(max_listings=10))
        asyncio.run(run_downloader())

        print("\n>>> PHASE 2 & 3: VISION AI & MARKET PRICING <<<")
        run_vision_pipeline()

        tier = os.environ.get("GEMINI_TIER", "FREE").upper()

        if tier == "PAID":
            # Traditional Sequential Pipeline for Paid Tier
            print("\nBot: Running Paid Tier Pipeline (Sequential High-Speed)...")
            asyncio.run(analyze_game_parallel())
            asyncio.run(run_parallel_pricer())
        else:
            # New Producer-Consumer Pipeline for Free Tier
            print("\nBot: Running Free Tier Pipeline (Concurrent Smart Mode)...")

            # Step 1: Backlog Check (Clear out anything that crashed yesterday)
            print("\nBot: Checking for unpriced backlog...")
            asyncio.run(run_parallel_pricer())

            # Step 2: Run the concurrent Generator/Consumer pipeline for new images
            asyncio.run(run_smart_pipeline())

        print("\n>>> PHASE 4: ARBITRAGE & REPORTING <<<")
        calculate_arbitrage()  # DB Status Updates
        generate_and_send_report()

    except KeyboardInterrupt:
        print("\nBot: Pipeline manually stopped by user.")
    except Exception as e:
        print(f"\nBot: FATAL PIPELINE ERROR: {e}")
    finally:
        # Calculate the total execution time
        elapsed = time.time() - start_time
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)

        print("\n===================================================")
        print(f"  PIPELINE COMPLETE in {minutes}m {seconds}s")
        print("===================================================")


if __name__ == "__main__":
    main()