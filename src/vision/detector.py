import os
import cv2
import numpy as np
from dotenv import load_dotenv
from inference import get_model
from src.db.database import SessionLocal
from src.db.models import Listing, Card

RAW_DIR = "data/raw/listings"
CROP_DIR = "data/processed/cropped_cards"


def order_points(pts):
    """Orders 4 points: top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def warp_card(image, points):
    """Takes an angled set of 4 points and warps it into a flat rectangle."""
    rect = order_points(np.array(points, dtype="float32"))
    (tl, tr, br, bl) = rect

    widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    maxWidth = max(int(widthA), int(widthB))

    heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    maxHeight = max(int(heightA), int(heightB))

    dst = np.array([
        [0, 0],
        [maxWidth - 1, 0],
        [maxWidth - 1, maxHeight - 1],
        [0, maxHeight - 1]], dtype="float32")

    M = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image, M, (maxWidth, maxHeight))


def run_vision_pipeline():
    print("Bot: Initializing High-Fidelity OBB Vision Pipeline...")
    load_dotenv()

    api_key = os.environ.get("ROBOFLOW_API_KEY")
    if not api_key:
        print("Error: Missing ROBOFLOW_API_KEY in .env file!")
        return

    os.environ["ROBOFLOW_API_KEY"] = api_key
    model = get_model(model_id="sleeved-obb/7")

    os.makedirs(CROP_DIR, exist_ok=True)
    db = SessionLocal()

    try:
        listings = db.query(Listing).filter(Listing.status == "IMAGES_DOWNLOADED").all()

        if not listings:
            print("Bot: No new images to process.")
            return

        print(f"Bot: Found {len(listings)} images to analyze.")

        for listing in listings:
            img_path = os.path.join(RAW_DIR, f"{listing.tutti_id}.jpg")

            if not os.path.exists(img_path):
                print(f"  -> File missing for {listing.tutti_id}, skipping...")
                listing.status = "IMAGE_MISSING"
                continue

            print(f"\nAnalyzing: {listing.title[:30]}...")
            img = cv2.imread(img_path)

            if img is None:
                print(f"  -> Could not read image at {img_path}. Skipping.")
                continue

            # Run the Roboflow Model with optimized parameters
            results = model.infer(
                img,
                confidence=0.40,        # Ignore anything under XX% confidence immediately
                iou_threshold=0.60,     # Allow cards to overlap by up to 60% before deleting one
                max_detections=200      # Allow up to 500 cards per photo!
            )

            if isinstance(results, list):
                predictions = results[0].predictions
            else:
                predictions = results.predictions

            print(f"  -> AI found {len(predictions)} potential objects.")

            card_count = 0
            listing_crop_dir = os.path.join(CROP_DIR, str(listing.tutti_id))

            if len(predictions) > 0:
                os.makedirs(listing_crop_dir, exist_ok=True)

            for i, pred in enumerate(predictions):
                # Filter out low confidence junk (adjust if needed!)
                confidence = getattr(pred, 'confidence', 0)
                if confidence < 0.40:
                    continue

                # Extract points and format for OpenCV
                points_data = getattr(pred, 'points', [])
                pts = [[p.x, p.y] for p in points_data]

                if len(pts) >= 4:
                    contour = np.array(pts, dtype=np.int32)
                    rect = cv2.minAreaRect(contour)
                    box = cv2.boxPoints(rect)

                    # Warp into a flat card
                    flat_card = warp_card(img, box)

                    if flat_card.size == 0:
                        continue

                    crop_filename = f"card_{i}.jpg"
                    crop_filepath = os.path.join(listing_crop_dir, crop_filename)
                    cv2.imwrite(crop_filepath, flat_card)

                    new_card = Card(
                        listing_id=listing.id,
                        cropped_image_path=crop_filepath,
                    )
                    db.add(new_card)
                    card_count += 1

            listing.status = "PROCESSED_VISION"
            print(f"  -> Successfully cropped, flattened, and saved {card_count} objects to DB.")

        db.commit()
        print("\nBot: Vision pipeline finished successfully.")

    except Exception as e:
        print(f"Error in vision pipeline: {e}")
        db.rollback()
    finally:
        db.close()
