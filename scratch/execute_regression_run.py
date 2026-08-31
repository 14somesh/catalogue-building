import os
import sys
import time
from datetime import datetime

# Ensure project root in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.run_brand import run_brand

print("=" * 80)
print("STARTING UNATTENDED REGRESSION TEST RUN ON SCRATCH DATASET")
print(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 80)

t0 = time.time()
report_path = run_brand("Stuffcool", config_path="config_test.yaml")
t1 = time.time()
elapsed = t1 - t0

print("\n" + "=" * 80)
print(f"REGRESSION TEST RUN COMPLETED in {elapsed:.2f} seconds ({elapsed/60:.2f} mins)")
print(f"Run Report Generated at: {report_path}")
print("=" * 80)
