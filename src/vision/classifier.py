import os
import cv2
import easyocr
import re
from src.db.database import SessionLocal
from src.db.models import Card


def preprocess_image_for_ocr(image):
    """Converts an image to grayscale and increases contrast for better OCR results."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Apply CLAHE (Contrast Limited Adaptive Histogram Equalization) to handle bad lighting
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    return enhanced


def run_classifier() -> None:
    """
    Deprecated. Local solution to detect the text on the cards to figure out which card it is.
    This method has low precision and is therefore not recommended.
    :return: None
    """
    print("Bot: Starting improved OCR classification...")
    # Initialize the OCR reader (gpu=True is faster if you have a compatible graphics card)
    reader = easyocr.Reader(['en', 'de'], gpu=False)

    db = SessionLocal()

    try:
        # Get all cards that haven't been identified yet
        unidentified_cards = db.query(Card).filter(Card.detected_name == None).all()

        if not unidentified_cards:
            print("Bot: No new cropped cards to identify.")
            return

        print(f"Bot: Analyzing {len(unidentified_cards)} cards using Region-of-Interest (ROI) cropping...\n")

        processed_count = 0

        for card in unidentified_cards:
            if not os.path.exists(card.cropped_image_path):
                # If the file is missing, remove the entry from the database
                db.delete(card)
                continue

            # Load the image using OpenCV
            img = cv2.imread(card.cropped_image_path)
            if img is None:
                continue

            h, w, _ = img.shape

            # --- REGION OF INTEREST (ROI) ---
            # Slice the top 15% (where the Name is) and bottom 15% (where the Set Number is)
            top_roi = img[0:int(h * 0.15), 0:w]
            bottom_roi = img[int(h * 0.85):h, 0:w]

            # Apply our contrast filter to make the black text stand out
            top_processed = preprocess_image_for_ocr(top_roi)
            bottom_processed = preprocess_image_for_ocr(bottom_roi)

            # Apply OCR *only* to these specific small areas, ignoring the artwork and HP
            top_results = reader.readtext(top_processed, detail=0)
            bottom_results = reader.readtext(bottom_processed, detail=0)

            # Name Logic: Grab the longest word from the top region
            detected_name = "Unknown"
            if top_results:
                # Filter out short artifacts like "e" or "HP"
                valid_words = [word for word in top_results if len(word) > 3]
                if valid_words:
                    detected_name = valid_words[0].title()  # Capitalize it nicely: "vulpix" -> "Vulpix"
                else:
                    detected_name = top_results[0].title()

            # Set Number Logic: Look for the Number/Number pattern in the bottom region
            bottom_text = " ".join(bottom_results).lower()
            # Regex allows for "/" or "|" since OCR sometimes misreads slashes
            set_number_match = re.search(r'(\d{1,3}\s*[/|]\s*\d{1,3})', bottom_text)

            set_info = "Not Found"
            if set_number_match:
                # Clean up empty spaces and fix pipes: "4 | 102" -> "4/102"
                set_info = set_number_match.group(1).replace(" ", "").replace("|", "/")

            print(f"Card ID {card.id}: Name: '{detected_name}' | Set: '{set_info}'")

            # Save the findings to the database
            card.detected_name = detected_name
            card.set_info = set_info
            processed_count += 1

        db.commit()
        print(f"\nBot: OCR classification complete. Updated {processed_count} cards.")

    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

