import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.llm_client import draft_bullets_and_subtitle

print("=== VERIFYING BUG 3: LLM Backoff & Drafting ===")

specs = {
    "capacity": "10000 mAh",
    "output": "15W Fast Charging",
    "ports": "Type-C, Magnetic Wireless",
    "weight": "190g",
    "warranty": "6 Months Warranty"
}

desc = (
    "Stuffcool Aura is a sleek 10000mAh magnetic wireless powerbank. "
    "Features 15W fast wireless charging and 20W PD Type-C output. "
    "Compact pocket-sized aluminium body with LED battery indicator."
)

res = draft_bullets_and_subtitle(
    brand="Stuffcool",
    model_name="Aura",
    product_description_block=desc,
    specs=specs
)

print("\nDrafting result:")
print(res)

assert res is not None, "Failed: draft_bullets_and_subtitle returned None"
assert len(res["bullet_1"]) <= 60, f"Bullet 1 exceeds 60 chars: {len(res['bullet_1'])}"
assert len(res["bullet_2"]) <= 60, f"Bullet 2 exceeds 60 chars: {len(res['bullet_2'])}"
assert len(res["bullet_3"]) <= 60, f"Bullet 3 exceeds 60 chars: {len(res['bullet_3'])}"
assert len(res["bullet_4"]) <= 60, f"Bullet 4 exceeds 60 chars: {len(res['bullet_4'])}"

print("\n>>> BUG 3 VERIFICATION PASSED SUCCESSFULLY! <<<")
