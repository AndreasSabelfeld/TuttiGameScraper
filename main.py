import time
import asyncio

from src.scraper.tutti import run_hybrid_scraper
from src.scraper.image_downloader import run_downloader
from src.vision.detector import run_vision_pipeline
from src.vision.gemini_classifier import analyze_card_sequential, analyze_card_parallel
from src.pricing.pricecharting import run_parallel_pricer
from src.pricing.calculator import calculate_arbitrage
from src.notifications.reporter import generate_and_send_report


def main():
    print("===================================================")
    print("  POKÉMON TCG ARBITRAGE BOT - PIPELINE INITIATED")
    print("===================================================")

    start_time = time.time()

    try:
        print("\n>>> PHASE 1: SCRAPING & DOWNLOADING <<<")
        asyncio.run(run_hybrid_scraper(max_listings=150))
        asyncio.run(run_downloader())

        print("\n>>> PHASE 2: VISION & AI CLASSIFICATION <<<")
        run_vision_pipeline()  # OpenCV & Local Roboflow OBB

        # Using the Uncapped Paid Tier Sequential Analyzer!
        asyncio.run(analyze_card_parallel())

        print("\n>>> PHASE 3: PRICECHARTING MARKET ANALYSIS <<<")
        asyncio.run(run_parallel_pricer())  # Stealth Playwright Engine
        calculate_arbitrage()  # DB Status Updates

        print("\n>>> PHASE 4: REPORTING & DATABASE CLEANUP <<<")
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