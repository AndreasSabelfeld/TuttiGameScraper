import os
import cv2
import numpy as np
from dotenv import load_dotenv
from inference import get_model

MODEL_ID = "sleeved-obb/7"
TEST_IMAGE_PATH = "data/raw/listings/45251731.jpg"  # Point this to a real image!


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

    # Calculate width of the new flat card
    widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    maxWidth = max(int(widthA), int(widthB))

    # Calculate height of the new flat card
    heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    maxHeight = max(int(heightA), int(heightB))

    # Construct the destination points for the perfectly flat card
    dst = np.array([
        [0, 0],
        [maxWidth - 1, 0],
        [maxWidth - 1, maxHeight - 1],
        [0, maxHeight - 1]], dtype="float32")

    # Calculate the perspective transform matrix and warp it!
    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image, M, (maxWidth, maxHeight))

    return warped


def test_local_inference():
    print("Bot: Downloading/Loading the Roboflow model locally...")
    print("     (This might take a minute on the very first run to download the weights!)")

    load_dotenv()

    os.environ["ROBOFLOW_API_KEY"] = os.environ.get("ROBOFLOW_API_KEY")

    model = get_model(model_id=MODEL_ID)

    print(f"\nBot: Model loaded successfully! Analyzing {TEST_IMAGE_PATH}...")
    image = cv2.imread(TEST_IMAGE_PATH)

    results = model.infer(image)

    # Handle the response format (inference package returns an object locally)
    # Sometimes it returns a list if you passed multiple images, so we grab the first one
    if isinstance(results, list):
        predictions = results[0].predictions
    else:
        predictions = results.predictions

    print(f"Bot: Found {len(predictions)} cards! Processing and flattening...")

    # 3. Process each detected card
    for i, pred in enumerate(predictions):
        confidence = getattr(pred, 'confidence', 0)

        # Extract all the polygon points
        points_data = getattr(pred, 'points', [])
        pts = []
        for p in points_data:
            pts.append([p.x, p.y])

        # As long as it has at least 4 points to make a shape, we can process it!
        if len(pts) >= 4:
            # 1. Convert the points into a format OpenCV understands
            contour = np.array(pts, dtype=np.int32)

            # 2. Ask OpenCV to find the tightest 4-corner angled box around this polygon
            rect = cv2.minAreaRect(contour)
            box = cv2.boxPoints(rect)

            # Now `box` contains exactly 4 corner coordinates!
            flat_card = warp_card(image, box)

            # Save it to look at it!
            output_name = f"test_crop_{i}_conf_{confidence:.2f}.jpg"
            cv2.imwrite(output_name, flat_card)
            print(f"  -> Saved flattened card: {output_name} (from {len(pts)} polygon points)")
        else:
            print(f"  -> Skipping detection {i}: Not enough points to form a polygon ({len(pts)}).")


if __name__ == "__main__":
    test_local_inference()