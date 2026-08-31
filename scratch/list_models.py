import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.llm_client import get_gemini_client

client = get_gemini_client()
print("Listing models:")
for m in client.models.list():
    if "flash" in m.name.lower() or "gemini" in m.name.lower():
        print(m.name)
