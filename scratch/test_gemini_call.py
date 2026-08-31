import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.llm_client import get_gemini_client

client = get_gemini_client()
print("Testing gemini-3.6-flash...")
res = client.models.generate_content(
    model="gemini-3.6-flash",
    contents="Say 'OK' in one word."
)
print("Result:", res.text.strip())
