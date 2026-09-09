import os
import sys
import io
import importlib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from PIL import Image

images_mod = importlib.import_module("src.2_images")
normalize_and_pad_image_square = images_mod.normalize_and_pad_image_square
from src.utils.llm_client import audit_collected_image_quality


def test_normalize_and_pad_image_square():
    # Create non-square test image (1000x600)
    raw_img = Image.new("RGB", (1000, 600), (200, 100, 50))
    squared = normalize_and_pad_image_square(raw_img, bg_color=(255, 255, 255), min_size=1200)
    
    assert squared.size == (1200, 1200)
    # Check that corners are pure white
    assert squared.getpixel((0, 0)) == (255, 255, 255)
    assert squared.getpixel((1199, 1199)) == (255, 255, 255)


def test_visual_ai_review_gate_approves_clean_packshots():
    clean_paths = [
        ("Urbn", "Nano 10000mAh 20W", "images/urbn/nano-10000mah-20w.png"),
        ("Pebble", "Pebble PB Rapid Air", "images/pebble/pebble-pb-rapid-air.png"),
        ("Urbn", "Slate MagSafe Qi2 10000mAh", "images/urbn/slate-magsafe-qi2-10000mah.png")
    ]
    for brand, model, path in clean_paths:
        if os.path.exists(path):
            with open(path, "rb") as f:
                img_bytes = f.read()
            is_valid, score, rejection = audit_collected_image_quality(img_bytes, brand, model)
            assert is_valid is True, f"Expected {path} to be approved, but got rejected: {rejection}"
            assert score >= 7, f"Expected score >= 7 for {path}, got {score}"


def test_visual_ai_review_gate_rejects_banners_and_hands():
    bad_tests = [
        # Hand held photo
        ("Pebble", "Pebble PB Rapid Air", "scratch/pebble_inspect/air_01.jpg"),
        # Marketing Infographic Banner
        ("Urbn", "Flux MagSafe Qi2", "scratch/flux_inspect/flux_03.jpg")
    ]
    for brand, model, path in bad_tests:
        if os.path.exists(path):
            with open(path, "rb") as f:
                img_bytes = f.read()
            is_valid, score, rejection = audit_collected_image_quality(img_bytes, brand, model, mime_type="image/jpeg")
            assert is_valid is False, f"Expected {path} to be REJECTED by Visual Review Gate, but was approved (score={score})"
            assert rejection is not None


if __name__ == "__main__":
    print("Testing normalize_and_pad_image_square...")
    test_normalize_and_pad_image_square()
    print("PASS: normalize_and_pad_image_square")
    print("Testing visual_ai_review_gate_approves_clean_packshots...")
    test_visual_ai_review_gate_approves_clean_packshots()
    print("PASS: test_visual_ai_review_gate_approves_clean_packshots")
    print("Testing visual_ai_review_gate_rejects_banners_and_hands...")
    test_visual_ai_review_gate_rejects_banners_and_hands()
    print("PASS: test_visual_ai_review_gate_rejects_banners_and_hands")
