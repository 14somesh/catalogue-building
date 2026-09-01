import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.parsers.brochure import parse_brochure_for_model

def make_multi_product_pdf(filename: str):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    lines = [
        "Aura 10000mAh Powerbank",
        "Capacity: 10000 mAh EV Grade Battery",
        "Output: 22.5W Fast Charging Output",
        "Ports: Dual Type-C and USB-A Ports",
        "Weight: 185g Lightweight Body",
        "Warranty: 6 Months Replacement Warranty",
        "",
        "Titan 25000mAh Powerbank",
        "Capacity: 25000 mAh Heavy Duty Power",
        "Output: 100W Ultra Power Delivery",
        "Ports: Triple Type-C Ports",
        "Weight: 490g Rugged Build",
        "Warranty: 2 Years Extended Warranty"
    ]
    
    content = "BT /F1 12 Tf 50 720 Td\n"
    for l in lines:
        content += f"({l}) Tj 0 -22 Td\n"
    content += "ET"
    stream_len = len(content)
    
    pdf = f"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj
4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
5 0 obj << /Length {stream_len} >>
stream
{content}
endstream
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000236 00000 n 
0000000305 00000 n 
trailer << /Size 6 /Root 1 0 R >>
startxref
{360 + stream_len}
%%EOF"""
    with open(filename, "wb") as f:
        f.write(pdf.encode("latin-1"))
    print(f"Created synthetic multi-product brochure PDF at: {filename}")

def run_isolation_test():
    test_pdf_path = "brochures/testbrand/multi_product_test.pdf"
    make_multi_product_pdf(test_pdf_path)
    
    config = {"llm": {"provider": "gemini", "model": "gemini-3.5-flash-lite"}}
    
    print("\n" + "=" * 80)
    print("RUNNING BROCHURE MULTI-PRODUCT ISOLATION TEST")
    print("=" * 80)
    
    # Extract Product A
    res_a = parse_brochure_for_model(
        brand="TestBrand",
        model_name="Aura 10000mAh",
        qualifier_tokens=["Titan", "Aura", "Max", "Plus"],
        config=config,
        brochure_override=test_pdf_path
    )
    
    # Extract Product B
    res_b = parse_brochure_for_model(
        brand="TestBrand",
        model_name="Titan 25000mAh",
        qualifier_tokens=["Titan", "Aura", "Max", "Plus"],
        config=config,
        brochure_override=test_pdf_path
    )
    
    print("\n--- SIDE-BY-SIDE EXTRACTION RESULTS ---")
    print(f"{'Field':<15} | {'Product A (Aura 10000mAh)':<30} | {'Product B (Titan 25000mAh)':<30}")
    print("-" * 80)
    
    fields = ["capacity", "output", "ports", "weight", "warranty"]
    all_isolated = True
    
    for f in fields:
        val_a = res_a.specs.get(f, "EMPTY") if res_a else "FAILED"
        val_b = res_b.specs.get(f, "EMPTY") if res_b else "FAILED"
        print(f"{f:<15} | {val_a:<30} | {val_b:<30}")
        
        # Verify no cross-contamination
        if val_a == val_b and val_a not in ["EMPTY", "FAILED"]:
            all_isolated = False
            
    print("-" * 80)
    
    # Strict Invariant Verifications
    assert res_a is not None, "Product A extraction failed!"
    assert res_b is not None, "Product B extraction failed!"
    assert "10000" in res_a.specs.get("capacity", ""), "Product A got wrong capacity!"
    assert "25000" in res_b.specs.get("capacity", ""), "Product B got wrong capacity!"
    assert "22.5" in res_a.specs.get("output", ""), "Product A got wrong output!"
    assert "100" in res_b.specs.get("output", ""), "Product B got wrong output!"
    assert "185" in res_a.specs.get("weight", ""), "Product A got wrong weight!"
    assert "490" in res_b.specs.get("weight", ""), "Product B got wrong weight!"
    assert all_isolated, "Cross contamination detected between Product A and Product B!"
    
    print("\n✅ SUCCESS: Multi-product page isolation verified! Zero cross-contamination.")
    print("=" * 80)

if __name__ == "__main__":
    run_isolation_test()
