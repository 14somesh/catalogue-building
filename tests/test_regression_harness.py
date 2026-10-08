import os
import re
import sys
import unittest
import tempfile
import pandas as pd
import openpyxl
import importlib

# Ensure workspace root is in sys.path
WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from src.utils.excel_handler import (
    validate_write_guard,
    DataLayerInvariantViolation,
    MissingCatalogueSheetError,
    load_catalogue_data,
    save_catalogue_data,
    get_effective_value,
    get_effective_product_dict,
    RAW_FIELD_TO_SOURCE_MAP
)
from src.utils.scraper import (
    score_candidate_match,
    reject_qualifier_mismatch,
    normalize_model_tokens,
    save_brand_domain_default
)
from src.utils.validators import (
    validate_row_deterministic,
    get_image_file_hash
)
from src.onboard_brand import (
    derive_category_prefix,
    append_products_to_catalogue,
    BrandInferenceSchema,
    RawProductItem
)

build_module = importlib.import_module("src.4_build")
format_name_html = build_module.format_name_html
resolve_effective_dp_float = build_module.resolve_effective_dp_float
validate_product_data = build_module.validate_product_data
build_catalogue_pdf = build_module.build_catalogue_pdf





class TestFabricationGuard(unittest.TestCase):
    """
    FABRICATION GUARD (Highest Priority Invariant):
    A row with any Raw_ field populated but no matching Source_ field (or general Source_URL)
    must be immediately rejected by validate_write_guard.
    Override_ fields are human-supplied and must remain strictly exempt.
    """

    def test_raw_field_without_source_raises_invariant_violation(self):
        """Prove validate_write_guard rejects Raw_ field with empty source."""
        for raw_col, source_col in RAW_FIELD_TO_SOURCE_MAP.items():
            row = {
                "Product_ID": "TEST-FAB-001",
                "Brand": "Pebble",
                "Model_Name": "Fabricated Model",
                raw_col: "Fabricated Value",
                source_col: None,
                "Source_URL": None
            }
            with self.assertRaises(DataLayerInvariantViolation, msg=f"Should reject unverified {raw_col}"):
                validate_write_guard(row)

    def test_raw_field_with_whitespace_source_raises_invariant_violation(self):
        """Prove whitespace-only source is considered empty and rejected."""
        row = {
            "Product_ID": "TEST-FAB-002",
            "Brand": "Pebble",
            "Model_Name": "Fabricated Model",
            "Raw_Title": "Some Title",
            "Source_Title": "   ",
            "Source_URL": ""
        }
        with self.assertRaises(DataLayerInvariantViolation):
            validate_write_guard(row)

    def test_raw_field_with_valid_source_passes(self):
        """Prove validate_write_guard accepts Raw_ field when verified source is present."""
        row = {
            "Product_ID": "TEST-FAB-003",
            "Brand": "Pebble",
            "Model_Name": "Valid Model",
            "Raw_Title": "Verified Title",
            "Source_Title": "https://brand.com/products/model",
            "Source_URL": "https://brand.com/products/model"
        }
        # Should not raise
        validate_write_guard(row)

    def test_override_fields_are_strictly_exempt(self):
        """Prove human Override_ fields pass write guard even with zero sources."""
        row = {
            "Product_ID": "TEST-FAB-004",
            "Brand": "Pebble",
            "Model_Name": "Override Model",
            "Override_Title": "Human Curated Title",
            "Override_DP": 1499,
            "Override_MRP": 2999,
            "Override_Image_Path": "images/custom.png",
            "Override_Spec_Capacity": "10000mAh",
            "Source_URL": None,
            "Source_Title": None
        }
        # Should not raise
        validate_write_guard(row)

    def test_deterministic_validator_hard_flags_unverified_raw_field(self):
        """Prove deterministic validator flags unverified raw fields as fatal bug."""
        row = {
            "Product_ID": "TEST-FAB-005",
            "Brand": "Pebble",
            "Model_Name": "Test Model",
            "Raw_Title": "Unverified Title",
            "Source_Title": None,
            "Source_URL": None,
            "Category": "Powerbank",
            "Bullet_1": "Feature 1",
            "Bullet_2": "Feature 2",
            "Spec_Capacity": "10000mAh",
            "Spec_Output": "22.5W"
        }
        is_passed, hard_flags, _ = validate_row_deterministic(row)
        self.assertFalse(is_passed)
        self.assertTrue(any("without verified source URL" in f for f in hard_flags))


class TestMatcherRejections(unittest.TestCase):
    """
    MATCHER REJECTION INVARIANTS:
    Guarantees that similar names, sibling models, and brand substrings are not falsely matched.
    """

    def test_luxcell_mini_must_not_match_luxcell_wireless_mini(self):
        """Luxcell Mini must not match Luxcell Wireless Mini due to extra qualifier/token."""
        qualifiers = ["Max", "Ultra", "Plus", "Pro", "Mini", "Lite", "Go", "Wireless"]
        score, is_valid, reason = score_candidate_match(
            target_model_name="Luxcell Mini",
            candidate_title="Ambrane Luxcell Wireless Mini 10000mAh Power Bank",
            candidate_url="https://ambrane.com/products/luxcell-wireless-mini-10000mah",
            brand="Ambrane",
            qualifier_tokens=qualifiers,
            category="Powerbank"
        )
        self.assertFalse(is_valid, f"Luxcell Mini matched Wireless Mini unexpectedly: {reason}")
        self.assertLess(score, 0)

    def test_glow_gadget_ray_must_not_match_ray_ban(self):
        """Glow Gadget 'Ray' must not match 'Ray-Ban'."""
        score, is_valid, reason = score_candidate_match(
            target_model_name="Ray",
            candidate_title="Glow Gadget Ray-Ban Edition 10000mAh Power Bank",
            candidate_url="https://glowgadget.com/products/ray-ban-edition",
            brand="Glow Gadget",
            category="Powerbank"
        )
        self.assertFalse(is_valid, f"Ray matched Ray-Ban unexpectedly: {reason}")
        self.assertLess(score, 0)

    def test_10000mah_model_must_not_match_20000mah_sibling(self):
        """A 10000mAh model must not match a 20000mAh sibling model."""
        score, is_valid, reason = score_candidate_match(
            target_model_name="Volt 10",
            candidate_title="Pebble Volt 20 20000mAh Fast Charging Powerbank",
            candidate_url="https://pebblecart.com/products/volt-20",
            brand="Pebble",
            category="Powerbank"
        )
        self.assertFalse(is_valid, f"10000mAh model matched 20000mAh sibling: {reason}")
        self.assertLess(score, 0)

    def test_click_10_must_not_match_click_20(self):
        """Click 10 must not match Click 20."""
        score, is_valid, reason = score_candidate_match(
            target_model_name="Click 10",
            candidate_title="Portronics Click 20 20000mAh Power Bank",
            candidate_url="https://portronics.com/products/click-20",
            brand="Portronics",
            category="Powerbank"
        )
        self.assertFalse(is_valid, f"Click 10 matched Click 20: {reason}")
        self.assertLess(score, 0)


class TestMatcherAcceptances(unittest.TestCase):
    """
    MATCHER ACCEPTANCE INVARIANTS:
    Guarantees valid variants, category descriptors, and token normalizations match cleanly.
    """

    def test_striker_matches_striker_buds_via_category_descriptors(self):
        """'STRIKER' matches 'Striker buds' via category descriptors in TWS category."""
        score, is_valid, reason = score_candidate_match(
            target_model_name="STRIKER",
            candidate_title="Pebble Striker Buds Earbuds",
            candidate_url="https://pebblecart.com/products/striker-buds",
            brand="Pebble",
            category="TWS"
        )
        self.assertTrue(is_valid, f"STRIKER failed to match Striker buds: {reason}")
        self.assertGreater(score, 50)

    def test_openloop_matches_open_loop(self):
        """'openloop' matches 'open loop' via compound token-boundary equivalence."""
        score, is_valid, reason = score_candidate_match(
            target_model_name="openloop",
            candidate_title="Pebble Open Loop - Wireless Earphones",
            candidate_url="https://pebblecart.com/products/open-loop",
            brand="Pebble",
            category="TWS"
        )
        self.assertTrue(is_valid, f"openloop failed to match open loop: {reason}")
        self.assertGreater(score, 50)

    def test_click_10_matches_click_10000mah(self):
        """'Click 10' matches 'Click 10000mAh' via 10 <-> 10000 normalization."""
        score, is_valid, reason = score_candidate_match(
            target_model_name="Click 10",
            candidate_title="Portronics Click 10000mAh Magnetic Wireless Power Bank",
            candidate_url="https://portronics.com/products/click-10000mah",
            brand="Portronics",
            category="Powerbank"
        )
        self.assertTrue(is_valid, f"Click 10 failed to match Click 10000mAh: {reason}")
        self.assertGreater(score, 50)


class TestDataResolution(unittest.TestCase):
    """
    DATA RESOLUTION & OVERRIDE INVARIANTS:
    Fallback chains, title width container limit, and override preservation.
    """

    def test_override_dp_and_mrp_fallback_chains(self):
        """Override_DP > MRP_Input; Override_MRP > MRP_Display > Raw_MRP_Scraped."""
        row = {
            "Product_ID": "TEST-RES-001",
            "Brand": "Portronics",
            "Model_Name": "Test Power",
            "MRP_Input": 1999,
            "Raw_MRP_Scraped": 3999,
            "MRP_Display": 3499,
            "Override_DP": 1799,
            "Override_MRP": 2999
        }
        # With overrides
        self.assertEqual(get_effective_value(row, "DP"), 1799)
        self.assertEqual(get_effective_value(row, "MRP"), 2999)

        # Without DP override -> MRP_Input
        row_no_dp = dict(row, Override_DP=None)
        self.assertEqual(get_effective_value(row_no_dp, "DP"), 1999)

        # Without MRP override -> MRP_Display
        row_no_mrp = dict(row, Override_MRP=None)
        self.assertEqual(get_effective_value(row_no_mrp, "MRP"), 3499)

        # Without MRP override and without MRP_Display -> Raw_MRP_Scraped
        row_raw_only = dict(row, Override_MRP=None, MRP_Display=None)
        self.assertEqual(get_effective_value(row_raw_only, "MRP"), 3999)

    def test_override_title_priority_chain(self):
        """Override_Title > Display_Name > Model_Name."""
        row = {
            "Product_ID": "TEST-RES-002",
            "Brand": "Portronics",
            "Model_Name": "ModelNameVal",
            "Display_Name": "DisplayNameVal",
            "Override_Title": "OverrideTitleVal"
        }
        prod1 = get_effective_product_dict(row)
        self.assertEqual(prod1["display_name"], "OverrideTitleVal")

        row_no_ovr = dict(row, Override_Title=None)
        prod2 = get_effective_product_dict(row_no_ovr)
        self.assertEqual(prod2["display_name"], "DisplayNameVal")

        row_base_only = dict(row, Override_Title=None, Display_Name=None)
        prod3 = get_effective_product_dict(row_base_only)
        self.assertEqual(prod3["display_name"], "ModelNameVal")

    def test_title_width_overflow_rejection(self):
        """Container width is 291px. In card rendering, model names with double spaces or invalid spacing are rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dummy_img = os.path.join(tmpdir, "test.png")
            from PIL import Image
            img = Image.new("RGB", (290, 290), color=(255, 255, 255))
            img.save(dummy_img)

            prod_double_space = {
                "product_id": "TEST-RES-003",
                "brand": "Portronics",
                "model_name": "Invalid  Double  Space",
                "subtitle": "10000mAh Fast Charging",
                "bullets": ["Fast charge", "Compact design"],
                "image_full_path": dummy_img,
                "mrp": 1999
            }
            with self.assertRaises(ValueError, msg="Double spaces in model name must fail validation"):
                validate_product_data(prod_double_space, {"Product_ID": "TEST-RES-003"})

    def test_override_survives_rerun(self):
        """Prove an Override_ field survives re-running a product row."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            test_excel = os.path.join(tmpdir, "catalogue_data.xlsx")
            test_config = os.path.join(tmpdir, "config.yaml")

            # Create test dataframe
            df = pd.DataFrame([{
                "Product_ID": "PB-TST-001",
                "Category": "Powerbank",
                "Brand": "Pebble",
                "Model_Name": "Volt 10",
                "Display_Name": "Volt 10",
                "MRP_Input": 999,
                "Status": "Approved",
                "Attempts": 1,
                "Override_Title": "Preserved Human Override Title",
                "Override_DP": 899,
                "Raw_Title": "Volt 10",
                "Source_URL": "https://pebblecart.com/products/volt-10",
                "Source_Title": "https://pebblecart.com/products/volt-10"
            }])
            save_catalogue_data(df, test_excel)

            from src.run_brand import re_run_product
            import yaml
            with open("config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            cfg["paths"]["excel_file"] = test_excel
            with open(test_config, "w", encoding="utf-8") as f:
                yaml.safe_dump(cfg, f)

            # Mock process_row_loop to simulate offline successful re-run
            def mock_loop(row, all_rows, config, brand_defaults, **kwargs):
                r = dict(row)
                r["Status"] = "Ready_For_Review"
                r["Attempts"] = 1
                return r

            with patch("src.run_brand.process_row_loop", side_effect=mock_loop):
                updated = re_run_product("PB-TST-001", config_path=test_config)

            self.assertEqual(updated.get("Override_Title"), "Preserved Human Override Title")
            self.assertEqual(updated.get("Override_DP"), 899)
            self.assertEqual(updated.get("Status"), "Ready_For_Review")

            # Also check persisted excel on disk
            persisted_df = load_catalogue_data(test_excel)
            self.assertEqual(persisted_df.iloc[0]["Override_Title"], "Preserved Human Override Title")
            self.assertEqual(persisted_df.iloc[0]["Override_DP"], 899)


class TestPipelineInvariants(unittest.TestCase):
    """
    PIPELINE & BUILD INVARIANTS:
    Approved-only gate, max+1 ID derivation, category scoping, bullets 2-4, and DP sorting.
    """

    def test_only_approved_rows_build_ready_for_review_must_not(self):
        """Prove build_catalogue_pdf builds only Approved rows; Ready_For_Review raises ValueError when alone."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_excel = os.path.join(tmpdir, "catalogue_data.xlsx")
            test_config = os.path.join(tmpdir, "config.yaml")

            # Only Ready_For_Review rows
            df = pd.DataFrame([{
                "Product_ID": "PB-TST-002",
                "Category": "TWS",
                "Brand": "Pebble",
                "Model_Name": "Striker Buds",
                "Status": "Ready_For_Review",
                "MRP_Input": 1299
            }])
            save_catalogue_data(df, test_excel)

            import yaml
            with open("config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            cfg["paths"]["excel_file"] = test_excel
            with open(test_config, "w", encoding="utf-8") as f:
                yaml.safe_dump(cfg, f)

            with self.assertRaises(ValueError, msg="Build must reject when no Approved rows exist"):
                build_catalogue_pdf(config_path=test_config, brand="Pebble", category="TWS")

    def test_product_id_generated_as_max_plus_one_never_count_plus_one(self):
        """Deleted rows must not cause collision: if 001, 002, 005 exist (len=3), next is 006, not 004."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_excel = os.path.join(tmpdir, "catalogue_data.xlsx")
            # Existing rows: seq 1, 2, 5 (len = 3)
            df = pd.DataFrame([
                {"Product_ID": "PB-PEB-001", "Category": "Powerbank", "Brand": "Pebble", "Model_Name": "M1", "Status": "Approved"},
                {"Product_ID": "PB-PEB-002", "Category": "Powerbank", "Brand": "Pebble", "Model_Name": "M2", "Status": "Approved"},
                {"Product_ID": "PB-PEB-005", "Category": "Powerbank", "Brand": "Pebble", "Model_Name": "M5", "Status": "Approved"},
            ])
            save_catalogue_data(df, test_excel)

            inference = BrandInferenceSchema(
                brand_name="Pebble",
                brand_code="PEB",
                category="Powerbank",
                dp_column_explanation="Standard DP header",
                products=[
                    RawProductItem(
                        raw_text="NewModel 10000mAh",
                        model_name="NewModel",
                        display_name="NewModel",
                        dp=999.0,
                        mrp=1999.0
                    )
                ]
            )

            _, results = append_products_to_catalogue(inference, category="Powerbank", excel_path=test_excel)
            assigned_pid = results[0]["Product_ID"]
            # Must be max+1 -> PB-PEB-006 (NEVER count+1 -> PB-PEB-004)
            self.assertEqual(assigned_pid, "PB-PEB-006", f"Expected max+1 'PB-PEB-006' but got '{assigned_pid}'")

    def test_pebble_tws_operation_must_not_select_pebble_powerbank_rows(self):
        """Scoping check: Pebble TWS filtering must exclude Pebble Powerbank rows."""
        df = pd.DataFrame([
            {"Product_ID": "PB-PEB-001", "Category": "Powerbank", "Brand": "Pebble", "Model_Name": "Volt 10", "Status": "Approved"},
            {"Product_ID": "TWS-PEB-001", "Category": "TWS", "Brand": "Pebble", "Model_Name": "Striker Buds", "Status": "Ready_For_Review"},
        ])
        tws_mask = (df["Brand"].str.lower() == "pebble") & (df["Category"].str.lower() == "tws")
        tws_df = df[tws_mask]
        self.assertEqual(len(tws_df), 1)
        self.assertEqual(tws_df.iloc[0]["Product_ID"], "TWS-PEB-001")
        self.assertNotIn("PB-PEB-001", tws_df["Product_ID"].values)

    def test_bullet_count_validation(self):
        """2 to 4 bullets accepted; fewer than 2 flagged as thin copy; never padded."""
        # 1 bullet -> Flagged as thin copy
        row_1_bullet = {
            "Product_ID": "TEST-BUL-001",
            "Brand": "Pebble",
            "Category": "Powerbank",
            "Model_Name": "TestModel",
            "Raw_Title": "TestModel 10000mAh",
            "Source_URL": "https://brand.com/prod",
            "Source_Title": "https://brand.com/prod",
            "Spec_Capacity": "10000mAh",
            "Spec_Output": "22.5W",
            "Bullet_1": "Fast 22.5W charging output"
        }
        is_passed, hard_flags, _ = validate_row_deterministic(row_1_bullet)
        self.assertFalse(is_passed)
        self.assertTrue(any("Thin copy: Product has fewer than 2 bullets" in f for f in hard_flags))

        # 3 bullets -> Passes
        row_3_bullets = dict(
            row_1_bullet,
            Bullet_2="Dual USB output ports",
            Bullet_3="LED battery percentage indicator"
        )
        is_passed2, hard_flags2, _ = validate_row_deterministic(row_3_bullets)
        # Check no thin copy flags
        self.assertFalse(any("Thin copy" in f for f in hard_flags2))

    def test_products_sort_by_dp_ascending_ties_by_product_id_no_dp_last(self):
        """Products within a brand sort by DP ascending, ties by Product_ID, no-DP last."""
        rows = [
            {"Product_ID": "PB-003", "MRP_Input": None, "MRP_Display": 2999},  # No DP -> inf
            {"Product_ID": "PB-001", "MRP_Input": 999, "MRP_Display": 1999},
            {"Product_ID": "PB-002", "MRP_Input": 499, "MRP_Display": 999},
            {"Product_ID": "PB-000", "MRP_Input": 499, "MRP_Display": 1299},   # Tie with PB-002 on DP=499
        ]
        df = pd.DataFrame(rows)
        df["_sort_dp"] = df.apply(resolve_effective_dp_float, axis=1)
        sorted_df = df.sort_values(by=["_sort_dp", "Product_ID"], ascending=[True, True])
        sorted_pids = sorted_df["Product_ID"].tolist()
        self.assertEqual(sorted_pids, ["PB-000", "PB-002", "PB-001", "PB-003"])


class TestSilentFailureGuards(unittest.TestCase):
    """
    SILENT-FAILURE & STRUCTURAL GUARDS:
    Warranty/guarantee prohibition, missing CatalogueData sheet detection, image presence check.
    """

    def test_subtitle_and_bullets_must_not_contain_warranty_or_guarantee(self):
        """No product subtitle or bullets may contain 'warranty' or 'guarantee'."""
        row_with_warranty_bullet = {
            "Product_ID": "TEST-WAR-001",
            "Brand": "Pebble",
            "Category": "Powerbank",
            "Model_Name": "TestModel",
            "Raw_Title": "TestModel",
            "Source_URL": "https://brand.com/p",
            "Source_Title": "https://brand.com/p",
            "Spec_Capacity": "10000mAh",
            "Spec_Output": "20W",
            "Bullet_1": "1 Year Manufacturer Guarantee Included",
            "Bullet_2": "Fast charging"
        }
        is_passed, hard_flags, _ = validate_row_deterministic(row_with_warranty_bullet)
        self.assertFalse(is_passed)
        self.assertTrue(any("Disallowed warranty claim in Bullet_1" in f for f in hard_flags))

        row_with_warranty_subtitle = {
            "Product_ID": "TEST-WAR-002",
            "Brand": "Pebble",
            "Category": "Powerbank",
            "Model_Name": "TestModel",
            "Raw_Title": "TestModel",
            "Source_URL": "https://brand.com/p",
            "Source_Title": "https://brand.com/p",
            "Subtitle": "10000mAh Powerbank with 6 Months Guarantee",
            "Bullet_1": "Type C input",
            "Bullet_2": "Fast charging"
        }
        is_passed2, hard_flags2, _ = validate_row_deterministic(row_with_warranty_subtitle)
        self.assertFalse(is_passed2)
        self.assertTrue(any("Disallowed warranty claim in Subtitle" in f for f in hard_flags2))
        self.assertTrue(any("Disallowed warranty claim in Subtitle" in f for f in hard_flags2))

    def test_missing_catalogue_sheet_raises_named_error(self):
        """load_catalogue_data raises MissingCatalogueSheetError if CatalogueData sheet is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_excel = os.path.join(tmpdir, "corrupt_catalogue.xlsx")
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Sheet1"  # Clobbered sheet name
            wb.save(bad_excel)
            wb.close()

            with self.assertRaises(MissingCatalogueSheetError):
                load_catalogue_data(bad_excel)

    def test_no_approved_row_may_reach_build_without_image_on_disk(self):
        """build_catalogue_pdf raises FileNotFoundError if any approved row lacks an image on disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_excel = os.path.join(tmpdir, "catalogue_data.xlsx")
            test_config = os.path.join(tmpdir, "config.yaml")

            df = pd.DataFrame([{
                "Product_ID": "PB-TST-009",
                "Category": "Powerbank",
                "Brand": "Pebble",
                "Model_Name": "NoImageModel",
                "Status": "Approved",
                "MRP_Input": 999,
                "MRP_Display": 1999,
                "Subtitle": "10000mAh Fast Charging",
                "Bullet_1": "Feature 1",
                "Bullet_2": "Feature 2",
                "Local_Image_Path": "images/non_existent_image_12345.png"
            }])
            save_catalogue_data(df, test_excel)

            import yaml
            with open("config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            cfg["paths"]["excel_file"] = test_excel
            with open(test_config, "w", encoding="utf-8") as f:
                yaml.safe_dump(cfg, f)

            with self.assertRaises(FileNotFoundError, msg="Missing image file must block build"):
                build_catalogue_pdf(config_path=test_config, brand="Pebble", category="Powerbank")


class TestAdditionalPastFixInvariants(unittest.TestCase):
    """
    ADDITIONAL INVARIANTS FROM PAST FIXES:
    Brand defaults root key prohibition, single-word title styling, duplicate image MD5 hash detection.
    """

    def test_saving_collection_url_without_category_is_prohibited(self):
        """save_brand_domain_default raises ValueError if collection_url is passed without category."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_yaml = os.path.join(tmpdir, "brand_defaults.yaml")
            import yaml
            with open(test_yaml, "w", encoding="utf-8") as f:
                yaml.safe_dump({"brands": {}}, f)

            with self.assertRaises(ValueError, msg="Writing collection_url at brand root is prohibited"):
                save_brand_domain_default(
                    brand="TestBrand",
                    domain="testbrand.com",
                    collection_url="https://testbrand.com/collections/all",
                    category=None,
                    config_path=test_yaml
                )

    def test_format_name_html_styling_rules(self):
        """Single-word names stay solid ink; multi-word names accent trailing word in <em>."""
        self.assertEqual(format_name_html("AURA"), "aura")
        self.assertEqual(format_name_html("GIGA"), "giga")
        self.assertEqual(format_name_html("Click 10"), "click <em>10</em>")
        self.assertEqual(format_name_html("Major Ultra"), "major <em>ultra</em>")
        self.assertEqual(format_name_html("Striker Buds Pro"), "striker buds <em>pro</em>")

    def test_duplicate_image_file_hash_triggers_hard_flag(self):
        """validate_row_deterministic detects identical image file hash across different SKUs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            img1 = os.path.join(tmpdir, "img1.png")
            img2 = os.path.join(tmpdir, "img2.png")
            # Create a 800x800 square test image
            from PIL import Image
            test_img = Image.new("RGB", (800, 800), color=(255, 255, 255))
            test_img.save(img1)
            test_img.save(img2)

            row1 = {
                "Product_ID": "P-001",
                "Brand": "BrandX",
                "Category": "Powerbank",
                "Model_Name": "Model One",
                "Raw_Title": "Model One",
                "Source_URL": "https://brandx.com/1",
                "Source_Title": "https://brandx.com/1",
                "Spec_Capacity": "10000mAh",
                "Spec_Output": "20W",
                "Bullet_1": "B1",
                "Bullet_2": "B2",
                "Override_Image_Path": img1
            }
            row2 = {
                "Product_ID": "P-002",
                "Brand": "BrandX",
                "Category": "Powerbank",
                "Model_Name": "Model Two",
                "Raw_Title": "Model Two",
                "Source_URL": "https://brandx.com/2",
                "Source_Title": "https://brandx.com/2",
                "Spec_Capacity": "20000mAh",
                "Spec_Output": "20W",
                "Bullet_1": "B1",
                "Bullet_2": "B2",
                "Override_Image_Path": img2
            }

            is_passed, hard_flags, _ = validate_row_deterministic(row2, all_rows=[row1, row2])
            self.assertFalse(is_passed)
            self.assertTrue(any("Duplicate image asset" in f for f in hard_flags))


class TestParserContract(unittest.TestCase):
    """Every registered parser must accept (url, html, status_code, tier, category)."""

    TWS_HTML = (
        '<html><head><title>Nothing Ear (a)</title></head><body>'
        '<h1 class="product-title">Nothing Ear (a)</h1>'
        '<div class="product-description">Up to 42.5 hours total playtime with charging case. '
        '11mm dynamic driver. Bluetooth 5.3. Active Noise Cancellation up to 45dB. 1 year warranty.</div>'
        '</body></html>'
    )
    PB_HTML = (
        '<html><head><title>Volt 20K</title></head><body><h1 class="product-title">Volt 20K</h1>'
        '<div class="product-description">20000 mAh capacity, 22.5W output, 2 USB-A + 1 Type-C ports, '
        '1 year warranty.</div></body></html>'
    )

    def test_every_registered_parser_accepts_category_kwarg(self):
        from src.parsers import PARSER_REGISTRY
        for name, parser in PARSER_REGISTRY.items():
            with self.subTest(parser=name):
                try:
                    parser.parse(url="https://example.com/products/x", html=self.TWS_HTML,
                                 status_code=200, tier=1, category="TWS")
                except TypeError as e:
                    self.fail(f"Parser '{name}' rejects the standard parse() signature: {e}")

    def test_generic_parser_extracts_tws_specs(self):
        from src.parsers.generic import GenericParser
        res = GenericParser().parse(url="https://example.com/ear-a", html=self.TWS_HTML,
                                    status_code=200, tier=1, category="TWS")
        self.assertIn("playtime", res.specs)
        self.assertIn("bluetooth", res.specs)

    def test_generic_parser_powerbank_specs_unchanged(self):
        from src.parsers.generic import GenericParser
        res = GenericParser().parse(url="https://example.com/volt", html=self.PB_HTML,
                                    status_code=200, tier=1, category="Powerbank")
        self.assertEqual(res.specs.get("capacity"), "20000 mAh")
        self.assertEqual(res.specs.get("output"), "22.5W")

    # A real Nothing.tech-style page: no WooCommerce/Shopify-theme class names anywhere (Tailwind
    # utility classes / hashed CSS-module names, the norm for custom-built Next.js/React storefronts).
    # The spec text is genuinely on the page in plain <div>/<span> tags, just not inside any container
    # the old targeted selectors (class containing "spec", "attribute", "description", ...) recognize.
    MODERN_SITE_HTML = (
        '<html><head><title>CMF Buds 2</title></head><body>'
        '<header class="h_a1b2"><nav>Shop All</nav></header>'
        '<h1 class="tw-text-2xl tw-font-bold">CMF Buds 2</h1>'
        '<div class="tw-grid tw-gap-4">'
        '<span class="tw-text-sm">11 mm PMI driver</span>'
        '<span class="tw-text-sm">48 dB Hybrid ANC</span>'
        '<span class="tw-text-sm">6 HD microphones</span>'
        '<span class="tw-text-sm">Up to 40 hours total playtime</span>'
        '<span class="tw-text-sm">Bluetooth 5.3</span>'
        '</div>'
        '<footer class="f_x9y8">Copyright Nothing</footer>'
        '</body></html>'
    )

    def test_generic_parser_finds_specs_with_no_semantic_class_names(self):
        """Nothing/CMF regression: a page with real spec text but no recognizable container class
        names must still yield specs, instead of silently coming back empty."""
        from src.parsers.generic import GenericParser
        res = GenericParser().parse(url="https://nothing.tech/products/cmf-buds-2", html=self.MODERN_SITE_HTML,
                                    status_code=200, tier=1, category="TWS")
        self.assertTrue(res.specs, f"Expected specs to be extracted from plain page text, got: {res.specs}")
        self.assertIn("playtime", res.specs)
        self.assertIn("bluetooth", res.specs)
        # Footer/nav noise must not leak into the extracted spec text.
        self.assertNotIn("Copyright", str(res.specs))

    def test_generic_parser_fallback_does_not_override_targeted_match(self):
        """When the targeted selectors already found real content, the full-page fallback should not
        be needed (targeted_text_len >= 200), so behavior on a normal themed page is unchanged."""
        from src.parsers.generic import GenericParser
        res = GenericParser().parse(url="https://example.com/ear-a", html=self.TWS_HTML,
                                    status_code=200, tier=1, category="TWS")
        self.assertEqual(res.description_text.strip(),
                         "Up to 42.5 hours total playtime with charging case. "
                         "11mm dynamic driver. Bluetooth 5.3. Active Noise Cancellation up to 45dB. 1 year warranty.")

    # Same page as MODERN_SITE_HTML, but with an unrelated "You may also like" carousel appended --
    # a different product with its own (different) Bluetooth version and driver size, in a widget that
    # itself has no recognizable class name either ("product-card" matches the shared noise-class list).
    MODERN_SITE_WITH_RECOMMENDATIONS_HTML = (
        '<html><head><title>CMF Buds 2</title></head><body>'
        '<header class="h_a1b2"><nav>Shop All</nav></header>'
        '<h1 class="tw-text-2xl tw-font-bold">CMF Buds 2</h1>'
        '<div class="tw-grid tw-gap-4">'
        '<span class="tw-text-sm">11 mm PMI driver</span>'
        '<span class="tw-text-sm">48 dB Hybrid ANC</span>'
        '<span class="tw-text-sm">6 HD microphones</span>'
        '<span class="tw-text-sm">Up to 40 hours total playtime</span>'
        '<span class="tw-text-sm">Bluetooth 5.3</span>'
        '</div>'
        '<section class="you-may-also-like">'
        '<h2>You may also like</h2>'
        '<div class="product-card"><span>CMF Buds Pro 2</span><span>10mm Driver</span><span>Bluetooth 5.4</span></div>'
        '<div class="product-card"><span>CMF Neckband Pro</span><span>Bluetooth 5.2</span></div>'
        '</section>'
        '<footer class="f_x9y8">Copyright Nothing</footer>'
        '</body></html>'
    )

    def test_generic_parser_fallback_ignores_recommended_products_noise(self):
        """A 'You may also like' carousel full of OTHER products (own class-free spec-like text) must
        not contaminate this product's specs -- e.g. its own Bluetooth 5.3 must win, not a
        recommended product's Bluetooth 5.4 or 5.2."""
        from src.parsers.generic import GenericParser
        res = GenericParser().parse(url="https://nothing.tech/products/cmf-buds-2",
                                    html=self.MODERN_SITE_WITH_RECOMMENDATIONS_HTML,
                                    status_code=200, tier=1, category="TWS")
        self.assertEqual(res.specs.get("bluetooth"), "Bluetooth v5.3")
        self.assertNotIn("You may also like", res.description_text or "")
        self.assertNotIn("CMF Buds Pro 2", str(res.specs))

    def test_shopify_parser_finds_specs_on_headless_custom_theme(self):
        """Same gap as GenericParser, for a Shopify store running a fully custom/headless theme
        (e.g. Hydrogen) whose markup uses none of the classic Shopify-theme class names."""
        from src.parsers.shopify import ShopifyParser
        html = (
            '<html><head><title>Volt 20K</title></head><body>'
            '<h1 class="tw-text-xl">Volt 20K</h1>'
            '<div class="tw-flex tw-flex-col">'
            '<span>20000 mAh capacity</span>'
            '<span>22.5W output</span>'
            '<span>2 USB-A + 1 Type-C ports</span>'
            '<span>1 year warranty</span>'
            '</div></body></html>'
        )
        res = ShopifyParser().parse(url="https://brandx.myshopify.com/products/volt-20k", html=html,
                                    status_code=200, tier=1, category="Powerbank")
        self.assertTrue(res.success)
        self.assertEqual(res.specs.get("capacity"), "20000mAh")
        self.assertEqual(res.specs.get("output"), "22.5W Fast Charging")

    def test_generic_parser_fallback_survives_unlisted_class_sibling_section(self):
        """A recommendation widget whose class name is NOT in the noise blocklist (e.g. 'other-buyers-chose')
        -- a blocklist can never be exhaustive -- must still be kept out, because it is a DOM sibling of the
        real product's content block, not an ancestor of it. The real Bluetooth 5.3 must win over a
        'frequently bought with' product's Bluetooth 5.4."""
        from src.parsers.generic import GenericParser
        html = (
            '<html><head><title>CMF Buds 2</title></head><body>'
            '<section class="other-buyers-chose"><h2>Frequently bought with</h2>'
            '<div><span>CMF Buds Pro 2</span><span>10mm Driver</span><span>Bluetooth 5.4</span></div>'
            '</section>'
            '<div class="tw-product-block">'
            '<h1 class="tw-text-2xl">CMF Buds 2</h1>'
            '<div class="tw-grid tw-gap-4">'
            '<span class="tw-text-sm">11 mm PMI driver</span>'
            '<span class="tw-text-sm">48 dB Hybrid ANC</span>'
            '<span class="tw-text-sm">Bluetooth 5.3</span>'
            '</div></div>'
            '</body></html>'
        )
        res = GenericParser().parse(url="https://nothing.tech/products/cmf-buds-2", html=html,
                                    status_code=200, tier=1, category="TWS")
        self.assertEqual(res.specs.get("bluetooth"), "Bluetooth v5.3")

    def test_generic_parser_fallback_survives_classless_sibling_with_no_main(self):
        """A page with no <main> landmark and a classless 'Trending now' sibling block placed BEFORE
        the real product's own (short) content must not let that sibling's numbers win just because
        climbing to <body> would otherwise pull both blocks in together."""
        from src.parsers.generic import GenericParser
        html = (
            '<html><head><title>Volt 20K</title></head><body>'
            '<div><h3>Trending now</h3>'
            '<div><span>Volt 10K</span><span>10000 mAh capacity</span></div></div>'
            '<div class="tw-product-block">'
            '<h1 class="tw-text-xl">Volt 20K</h1>'
            '<div><span>20000 mAh capacity</span><span>22.5W output</span></div>'
            '</div></body></html>'
        )
        res = GenericParser().parse(url="https://brandx.com/products/volt-20k", html=html,
                                    status_code=200, tier=1, category="Powerbank")
        self.assertEqual(res.specs.get("capacity"), "20000 mAh")


class TestCodeIntegrity(unittest.TestCase):
    """
    Static guards for the crash classes that previously only surfaced during a live run:
    missing imports / undefined names (swallowed by broad excepts), imports of names that don't exist,
    duplicate API routes, and parser signature drift.
    """
    SRC_DIR = os.path.join(WORKSPACE_ROOT, "src")

    @classmethod
    def _src_files(cls):
        out = []
        for root, _, files in os.walk(cls.SRC_DIR):
            if "__pycache__" in root:
                continue
            out += [os.path.join(root, f) for f in files if f.endswith(".py")]
        return sorted(out)

    def test_every_src_module_imports_cleanly(self):
        for path in self._src_files():
            rel = os.path.relpath(path, WORKSPACE_ROOT)[:-3].replace(os.sep, ".")
            if rel.endswith("__init__"):
                rel = rel[: -len(".__init__")]
            with self.subTest(module=rel):
                importlib.import_module(rel)

    def test_no_undefined_names_in_src(self):
        try:
            from pyflakes.api import check
            from pyflakes.reporter import Reporter
            from pyflakes import messages as pm
        except ImportError:
            self.fail("pyflakes is required for this guard: pip install -r requirements.txt")
        import io
        problems = []

        class _Collect(Reporter):
            def __init__(self):
                super().__init__(io.StringIO(), io.StringIO())

            def flake(self, message):
                if isinstance(message, (pm.UndefinedName, pm.UndefinedLocal, pm.UndefinedExport)):
                    problems.append(str(message))

        rep = _Collect()
        for path in self._src_files():
            with open(path, encoding="utf-8") as f:
                check(f.read(), path, rep)
        self.assertEqual(problems, [], "Undefined names (missing import or typo):\n" + "\n".join(problems))

    def test_every_internal_from_import_resolves(self):
        import ast
        missing = []
        for path in self._src_files() + [os.path.join(WORKSPACE_ROOT, "tests", "harness.py")]:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src"):
                    mod = importlib.import_module(node.module)
                    for alias in node.names:
                        if alias.name != "*" and not hasattr(mod, alias.name):
                            try:
                                importlib.import_module(f"{node.module}.{alias.name}")
                            except ImportError:
                                missing.append(f"{os.path.relpath(path, WORKSPACE_ROOT)}:{node.lineno} from {node.module} import {alias.name}")
        self.assertEqual(missing, [], "Imports of names that do not exist:\n" + "\n".join(missing))

    def test_no_duplicate_api_routes(self):
        import ast
        api_path = os.path.join(self.SRC_DIR, "api.py")
        with open(api_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), api_path)
        seen, dups = {}, []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                            and dec.func.attr in ("get", "post", "put", "patch", "delete")
                            and dec.args and isinstance(dec.args[0], ast.Constant)):
                        key = (dec.func.attr.upper(), dec.args[0].value)
                        if key in seen:
                            dups.append(f"{key[0]} {key[1]} defined at lines {seen[key]} and {node.lineno}")
                        seen[key] = node.lineno
        self.assertEqual(dups, [], "Duplicate API routes (the later one is silently ignored):\n" + "\n".join(dups))

    def test_parser_subclasses_keep_base_parse_signature(self):
        import inspect
        from src.parsers.base import BaseParser
        from src.parsers import PARSER_REGISTRY
        base_params = list(inspect.signature(BaseParser.parse).parameters)
        for name, parser in PARSER_REGISTRY.items():
            params = list(inspect.signature(type(parser).parse).parameters)
            with self.subTest(parser=name):
                self.assertTrue(set(base_params).issubset(params),
                                f"{type(parser).__name__}.parse is missing {sorted(set(base_params) - set(params))}")


class TestDisplayNameFallback(unittest.TestCase):
    """An empty Display_Name cell must fall back to the model name, never the literal string 'nan'."""

    def test_clean_display_name_of_empty_cell_is_empty_not_nan(self):
        rb = importlib.import_module("src.run_brand")
        self.assertEqual(rb.clean_display_name(float("nan"), brand="Zylo"), "")
        self.assertEqual(rb.clean_display_name(None, brand="Zylo"), "")
        self.assertEqual(rb.clean_display_name("Zylo Air 2", brand="Zylo"), "Air 2")


class TestSubBrandAndRetailListingMatching(unittest.TestCase):
    """
    Real titles from in.nothing.tech/collections/earbuds and retail listings.
    A sub-brand word in the user's own model name (CMF for Nothing) is not a foreign brand,
    and colour / generic listing words never make a retail listing a different model.
    """
    Q = ["Pro", "Max", "Plus"]
    SITE = [("Ear (3a)", "ear-3a"), ("Ear (3)", "ear-3"), ("Ear", "ear"), ("Ear (a)", "ear-a"), ("Ear (open)", "ear-open"),
            ("CMF Buds Pro 2", "cmf-buds-pro-2"), ("CMF Buds 2 Plus", "cmf-buds-2-plus"),
            ("CMF Buds 2a", "cmf-buds-2a"), ("CMF Buds 2", "cmf-buds-2")]

    def _best(self, target):
        best = None
        for title, handle in self.SITE:
            score, ok, _ = score_candidate_match(target, title, f"https://in.nothing.tech/products/{handle}",
                                                 brand="Nothing", qualifier_tokens=self.Q, category="TWS")
            if ok and (best is None or score > best[0]):
                best = (score, title)
        return best[1] if best else None

    def test_cmf_sub_brand_products_resolve_to_exact_page(self):
        self.assertEqual(self._best("CMF Buds 2"), "CMF Buds 2")
        self.assertEqual(self._best("CMF Buds 2 Plus"), "CMF Buds 2 Plus")
        self.assertEqual(self._best("CMF Buds Pro 2"), "CMF Buds Pro 2")
        self.assertEqual(self._best("Ear (A)"), "Ear (a)")

    def test_product_absent_from_site_matches_nothing(self):
        self.assertIsNone(self._best("CMF Buds Neo"))

    def test_retail_listing_colour_and_filler_words_accepted(self):
        for target, title in [("Ear (A)", "Nothing TWS Ear (a) Earbuds, Black"),
                              ("Ear (A)", "Nothing Ear (a) Bluetooth Truly Wireless in Ear Earbuds with Mic"),
                              ("CMF Buds 2", "CMF BY NOTHING Buds 2 Wireless Earbuds, Orange")]:
            with self.subTest(title=title):
                self.assertTrue(score_candidate_match(target, title, "https://www.reliancedigital.in/x/p/1", brand="Nothing",
                                                      qualifier_tokens=self.Q, category="TWS")[1])

    def test_retail_listing_of_sibling_model_still_rejected(self):
        for target, title in [("Ear (A)", "Nothing Ear (3a) Earbuds, White"),
                              ("CMF Buds 2", "CMF by Nothing Buds 2 Plus, Blue"),
                              ("CMF Buds 2", "CMF by Nothing Buds 2a, Dark Grey")]:
            with self.subTest(title=title):
                self.assertFalse(score_candidate_match(target, title, "https://www.reliancedigital.in/x/p/1", brand="Nothing",
                                                       qualifier_tokens=self.Q, category="TWS")[1])

    def test_foreign_brand_still_rejected(self):
        ok, reason = reject_qualifier_mismatch("Tune Beam 2", "GOBOULT Tune Beam 2 Earbuds", ["Pro"], brand="JBL")
        self.assertFalse(ok)


class TestIngestDisplayNames(unittest.TestCase):
    """The AI's short display name may drop generic words, never the words that identify the product."""

    def _run(self, brand, rows):
        from src.onboard_brand import BrandInferenceSchema, RawProductItem, enforce_distinct_display_names
        inf = BrandInferenceSchema(brand_name=brand, brand_code="X", domain=None, platform=None, dp_column_explanation="x",
                                   column_mapping=[], qualifier_tokens=[],
                                   products=[RawProductItem(raw_text="", model_name=m, display_name=d) for m, d in rows])
        return [p.display_name for p in enforce_distinct_display_names(inf).products]

    def test_identifying_words_restored_and_duplicates_removed(self):
        self.assertEqual(self._run("Nothing", [("CMF Buds 2", "CMF Buds 2"), ("CMF Buds Neo", "CMF Buds"),
                                               ("CMF Buds Pro 2", "CMF Buds"), ("Ear (A)", "Ear")]),
                         ["CMF Buds 2", "CMF Buds Neo", "CMF Buds Pro 2", "Ear (A)"])

    def test_valid_short_names_kept(self):
        self.assertEqual(self._run("Pebble", [("STRIKER Buds", "STRIKER"), ("Wave Buds", "Wave"),
                                              ("Roam 20000mAh", "Roam"), ("Pebble Open Loop", "Open Loop")]),
                         ["STRIKER", "Wave", "Roam", "Open Loop"])


class TestProviderChain(unittest.TestCase):
    """AI calls go Groq first, Gemini only as fallback; quota-exhausted providers are skipped; bad output falls through."""

    def setUp(self):
        from src.utils import llm_client
        llm_client.reset_provider_states()
        self.llm = llm_client

    def _fake_groq(self, content=None, exc=None):
        from unittest.mock import MagicMock
        client = MagicMock()
        if exc:
            client.chat.completions.create.side_effect = exc
        else:
            msg = MagicMock(); msg.message.content = content
            client.chat.completions.create.return_value = MagicMock(choices=[msg])
        return client

    def _fake_gemini(self, text):
        from unittest.mock import MagicMock
        client = MagicMock(); client.models.generate_content.return_value = MagicMock(text=text)
        return client

    def test_groq_answers_first(self):
        from unittest.mock import patch
        from src.utils.product_verifier import ProductMatchVerdict
        cfg = {"providers": [{"name": "groq", "model": "g"}, {"name": "gemini", "model": "m"}]}
        gem = self._fake_gemini('{"decision": "not_match", "confidence": 1, "reason": "gemini"}')
        with patch.object(self.llm, "get_groq_client", return_value=self._fake_groq('{"decision": "match", "confidence": 90, "reason": "groq"}')), \
             patch.object(self.llm, "get_gemini_client", return_value=gem):
            data, prov = self.llm.call_llm_json("s", "u", ProductMatchVerdict, llm_config=cfg)
        self.assertEqual(data["reason"], "groq")
        self.assertTrue(prov.startswith("groq"))
        gem.models.generate_content.assert_not_called()

    def test_falls_back_to_gemini_and_marks_quota_exhausted(self):
        from unittest.mock import patch
        from src.utils.product_verifier import ProductMatchVerdict
        cfg = {"providers": [{"name": "groq", "model": "g"}, {"name": "gemini", "model": "m"}]}
        with patch.object(self.llm, "get_groq_client", return_value=self._fake_groq(exc=Exception("429 rate_limit_exceeded: tokens per day (TPD) limit reached, quota exceeded"))), \
             patch.object(self.llm, "get_gemini_client", return_value=self._fake_gemini('{"decision": "match", "confidence": 80, "reason": "gemini"}')):
            data, prov = self.llm.call_llm_json("s", "u", ProductMatchVerdict, llm_config=cfg)
        self.assertEqual(data["reason"], "gemini")
        self.assertTrue(prov.startswith("gemini"))

    def test_invalid_output_falls_through(self):
        from unittest.mock import patch
        from src.utils.product_verifier import ProductMatchVerdict
        cfg = {"providers": [{"name": "groq", "model": "g"}, {"name": "gemini", "model": "m"}]}
        with patch.object(self.llm, "get_groq_client", return_value=self._fake_groq('{"not": "the schema"}')), \
             patch.object(self.llm, "get_gemini_client", return_value=self._fake_gemini('{"decision": "unsure", "confidence": 40, "reason": "gemini"}')):
            data, _ = self.llm.call_llm_json("s", "u", ProductMatchVerdict, llm_config=cfg)
        self.assertEqual(data["decision"], "unsure")

    def test_vision_uses_vision_chain_and_fails_closed(self):
        from unittest.mock import patch
        cfg = {"providers": [{"name": "groq", "model": "g"}], "vision_providers": [{"name": "groq", "model": "qwen/qwen3.8-27b"}]}
        with patch.object(self.llm, "get_groq_client", return_value=self._fake_groq(exc=Exception("boom"))):
            ok, score, reason = self.llm.audit_collected_image_quality(b"x", "JBL", "Tour Pro 3", llm_config=cfg)
        self.assertFalse(ok)
        self.assertIn("Visual AI unavailable", reason)


class TestProductVerifier(unittest.TestCase):
    """The verifier's hard checks hold even when the AI is wrong or unavailable."""

    def _verify(self, ai_answer, **kw):
        from unittest.mock import patch
        from src.utils import product_verifier as pv
        args = dict(brand="Urbn", model_name="Nano 20000mAh", category="Powerbank", page_url="https://x/p/nano",
                    page_title="20000 mAh Nano Power Bank")
        args.update(kw)
        with patch("src.utils.llm_client.call_llm_json", return_value=ai_answer):
            return pv.verify_product_page(**args)

    def test_capacity_conflict_rejected_without_ai(self):
        decision, reason, provider = self._verify(({"decision": "match", "confidence": 99, "reason": "x"}, "groq:g"),
                                                  page_title="10000 mAh Nano Power Bank", page_url="https://x/p/nano-10k")
        self.assertEqual(decision, "not_match")
        self.assertIsNone(provider)

    def test_ai_match_with_absurd_price_downgraded(self):
        decision, _, _ = self._verify(({"decision": "match", "confidence": 90, "reason": "same"}, "groq:g"),
                                      page_price=24999, sheet_dp=1200, sheet_mrp=2999)
        self.assertEqual(decision, "unsure")

    def test_ai_unavailable_clear_rule_match_accepted_tie_not(self):
        self.assertEqual(self._verify((None, None), rule_valid=True, rule_tied=False)[0], "match")
        self.assertEqual(self._verify((None, None), rule_valid=True, rule_tied=True)[0], "unsure")
        self.assertEqual(self._verify((None, None), rule_valid=False, rule_tied=False)[0], "unsure")

    def test_ai_decision_passed_through(self):
        self.assertEqual(self._verify(({"decision": "not_match", "confidence": 90, "reason": "Plus variant"}, "groq:g"))[0], "not_match")


class TestVerifyRetryWithMemory(unittest.TestCase):
    """Collect step: a page the verifier rejects is skipped, the next candidate is tried, and the rejection is remembered."""

    def test_rejected_page_skipped_next_verified_and_remembered(self):
        from unittest.mock import patch
        from src.parsers.base import ParserResult
        collect = importlib.import_module("src.1_collect")
        wrong, right = "https://www.volto.in/products/volto-max-combo", "https://www.volto.in/products/volto-max-20k"
        fetched = []

        def fake_search(*a, **k):
            k["out_candidates"].extend([
                {"url": wrong, "title": "Volto Max 20K", "score": 250.0, "valid": True, "reason": "", "source": "t"},
                {"url": right, "title": "Volto Max 20K", "score": 250.0, "valid": True, "reason": "", "source": "t"}])
            return wrong

        def fake_fetch(url, **k):
            fetched.append(url)
            return ParserResult(success=True, status_code=200, url=url, tier=1,
                                title="Volto Max 20K Combo (2-pack)" if url == wrong else "Volto Max 20K",
                                specs={"capacity": "20000 mAh", "output": "22.5W"}, description_text="20000 mAh 22.5W")

        def fake_verify(brand, model, cat, url, title, *a, **k):
            return ("not_match", "combo pack", "groq:g") if "Combo" in (title or "") else ("match", "same product", "groq:g")

        row = {"Product_ID": "PB-VOL-001", "Brand": "Volto", "Model_Name": "Volto Max 20K", "Category": "Powerbank",
               "MRP_Input": 1999}
        cfg = {"domain": "volto.in", "platform": "shopify", "qualifier_tokens": ["Max"], "retail_order": []}
        with patch.object(collect, "search_shopify_brand_store", side_effect=fake_search), \
             patch.object(collect, "fetch_and_parse_url", side_effect=fake_fetch), \
             patch("src.utils.product_verifier.verify_product_page", side_effect=fake_verify), \
             patch.object(collect, "load_brand_defaults", return_value=cfg), \
             patch.object(collect, "parse_brochure_for_model", return_value=None), \
             patch.object(collect, "draft_bullets_and_subtitle", return_value=({"title": "Max 20K", "subtitle": "s.", "bullet_1": "b1.", "bullet_2": "b2."}, "groq")):
            updates, ok, _ = collect.collect_data_for_row(dict(row), {"llm": {}})
            self.assertTrue(ok)
            self.assertEqual(updates["Source_URL"], right)
            self.assertIn(wrong, updates["Rejected_URLs"])
            # Retry: the remembered page is never fetched again
            fetched.clear()
            row2 = dict(row, Rejected_URLs=updates["Rejected_URLs"])
            updates2, ok2, _ = collect.collect_data_for_row(row2, {"llm": {}})
        self.assertTrue(ok2)
        self.assertNotIn(wrong, fetched)
        self.assertEqual(updates2["Source_URL"], right)

    def test_unsure_everywhere_gives_needs_your_pick(self):
        from unittest.mock import patch
        from src.parsers.base import ParserResult
        collect = importlib.import_module("src.1_collect")
        rb = importlib.import_module("src.run_brand")
        urls = ["https://p.in/products/konnect-x-a", "https://p.in/products/konnect-x-b"]

        def fake_search(*a, **k):
            k["out_candidates"].extend([{"url": u, "title": f"Konnect X variant {i}", "score": 200.0, "valid": True,
                                         "reason": "", "source": "t"} for i, u in enumerate(urls)])
            return urls[0]

        cfg = {"domain": "p.in", "platform": "shopify", "qualifier_tokens": [], "retail_order": []}
        with patch.object(collect, "search_shopify_brand_store", side_effect=fake_search), \
             patch.object(collect, "fetch_and_parse_url", side_effect=lambda url, **k: ParserResult(success=True, status_code=200, url=url, title="Konnect X", specs={"a": "b"})), \
             patch("src.utils.product_verifier.verify_product_page", return_value=("unsure", "sheet name fits several cable variants", "groq:g")), \
             patch.object(collect, "load_brand_defaults", return_value=cfg), \
             patch.object(collect, "parse_brochure_for_model", return_value=None):
            updates, ok, _ = collect.collect_data_for_row({"Product_ID": "X", "Brand": "Portronics", "Model_Name": "Konnect X", "Category": "Cable"}, {"llm": {}})
        self.assertFalse(ok)
        self.assertIn("Needs your pick", updates["Flags"])
        self.assertIn(urls[0], rb.derive_failure_reason(dict(updates)))
        self.assertIsNone(updates["Rejected_URLs"])  # unsure pages are offered, not blacklisted

    def test_memory_column_roundtrip(self):
        collect = importlib.import_module("src.1_collect")
        mem = {"https://a/x": "not_match: Plus variant", "https://a/y": "delisted: HTTP 404"}
        self.assertEqual(collect.parse_rejected_urls(collect.format_rejected_urls(mem)), mem)
        self.assertEqual(collect.parse_rejected_urls(float("nan")), {})

    def test_verifier_rejection_reason_beats_legacy_ambiguous_message(self):
        """CMF Buds 2 regression: a near-miss candidate (e.g. 'CMF Buds 2 Plus' for target 'CMF Buds 2') is both
        (a) recorded as a near-miss candidate for the verifier, and (b) flagged by the older pre-verifier
        title-overlap diagnostic (out_diagnostics['ambiguous_candidates']) that scraper.py still populates as a
        side effect of scoring. When the verifier actually fetches and rejects that page with a real reason, its
        decision must win — not the older heuristic's generic 'may be ambiguous or outdated' text, which never
        looked at the page content and used to take priority by code order."""
        from unittest.mock import patch, MagicMock
        from src.parsers.base import ParserResult
        collect = importlib.import_module("src.1_collect")
        url = "https://in.nothing.tech/products/cmf-buds-2-plus"

        def fake_search(*a, **k):
            k["out_candidates"].append({"url": url, "title": "CMF Buds 2 Plus", "score": 0.0, "valid": False,
                                        "reason": "qualifier mismatch", "source": "t"})
            k["out_diagnostics"].setdefault("ambiguous_candidates", []).append("CMF Buds 2 Plus")
            return None

        cfg = {"domain": "in.nothing.tech", "platform": "shopify", "qualifier_tokens": ["Plus", "Pro"], "retail_order": []}
        fake_generic_parser_cls = MagicMock(return_value=MagicMock(find_product_url=lambda *a, **k: None))
        with patch.object(collect, "search_shopify_brand_store", side_effect=fake_search), \
             patch("src.parsers.generic.GenericBrandParser", fake_generic_parser_cls), \
             patch.object(collect, "fetch_and_parse_url",
                          return_value=ParserResult(success=True, status_code=200, url=url, tier=1,
                                                    title="CMF Buds 2 Plus", specs={"a": "b"})), \
             patch("src.utils.product_verifier.verify_product_page",
                   return_value=("not_match", "candidate is the Plus variant, not the base model", "groq:g")), \
             patch.object(collect, "load_brand_defaults", return_value=cfg), \
             patch.object(collect, "parse_brochure_for_model", return_value=None):
            updates, ok, _ = collect.collect_data_for_row(
                {"Product_ID": "TWS-CMF-002", "Brand": "CMF by Nothing", "Model_Name": "CMF Buds 2", "Category": "TWS"},
                {"llm": {}})
        self.assertFalse(ok)
        self.assertIn("verified as a different product", updates["Flags"])
        self.assertIn("Plus variant", updates["Flags"])
        self.assertNotIn("may be ambiguous or outdated", updates["Flags"])


class TestFinderRecallOnRealCatalogues(unittest.TestCase):
    """
    Generalisation guard on ~300 real catalogue/audio/power/watch products from 5 recorded brand stores:
    for each product, written the way a dealer price sheet would list it (brand dropped, specs moved to the end),
    the correct product must be among the top candidates handed to the verifier.
    """
    BRANDS = {"stuffcool.com": "Stuffcool", "pebblecart.com": "Pebble", "portronics.com": "Portronics",
              "urbnworld.com": "Urbn", "glowgadgets.in": "Glow Gadget"}

    @staticmethod
    def _category(p):
        t = f"{p.get('product_type', '')} {p.get('title', '')}".lower()
        if re.search(r"earbud|\bbuds\b|\btws\b|earphone|headphone|neckband", t): return "TWS"
        if re.search(r"power\s*bank|powerbank|\d+\s*mah", t): return "Powerbank"
        if "speaker" in t or "soundbar" in t: return "Speaker"
        if re.search(r"smart\s*watch|smartwatch|\bwatch\b|smart band", t): return "Smartwatch"
        return None

    @staticmethod
    def _sheet_name(title, brand):
        n = re.sub(rf"^\s*{re.escape(brand)}\s+", "", title, flags=re.I)
        n = re.sub(r"(\d),(\d)", r"\1\2", n)
        n = re.split(r"\s[-|–]\s|,|\s+with\s+|\(", n, maxsplit=1)[0]
        specs = re.findall(r"\d+(?:\.\d+)?\s*(?:mah|w)\b", n, flags=re.I)
        n = re.sub(r"\d+(?:\.\d+)?\s*(?:mah|w)\b", " ", n, flags=re.I)
        n = re.sub(r"\b(?:power\s*bank|powerbank|earbuds|tws|bluetooth speaker|smart\s*watch)\b", " ", n, flags=re.I)
        return " ".join(n.split() + [sp.replace(" ", "") for sp in specs]).strip()

    def test_correct_product_within_verifier_shortlist(self):
        import json, hashlib, logging
        from src.utils.scraper import record_candidate, load_brand_defaults
        from src.utils.product_verifier import order_candidates, MAX_CANDIDATES_PER_SOURCE
        fx = json.load(open(os.path.join(WORKSPACE_ROOT, "tests", "fixtures", "http_fixtures.json"), encoding="utf-8"))["cache_files"]
        logging.disable(logging.CRITICAL)
        try:
            total = found = 0
            misses = []
            for dom, brand in self.BRANDS.items():
                catalogue = fx[hashlib.md5(f"shopify_cat_{dom}".encode()).hexdigest() + ".json"]
                for p in catalogue:
                    cat = self._category(p)
                    if not cat:
                        continue
                    target = self._sheet_name(p["title"], brand)
                    if len(target) < 2:
                        continue
                    q = load_brand_defaults(brand, category=cat).get("qualifier_tokens", [])
                    cands = []
                    for c in catalogue:
                        url = f"https://www.{dom}/products/{c['handle']}"
                        sc, ok, why = score_candidate_match(target, c["title"], url, brand=brand, qualifier_tokens=q,
                                                            category=cat, source_is_brand_site=True)
                        record_candidate(cands, url, c["title"], sc, ok, why, "catalogue", target, brand)
                    shortlist = order_candidates(cands)[:MAX_CANDIDATES_PER_SOURCE]
                    total += 1
                    if any(c["url"].endswith("/" + p["handle"]) or c["title"].strip().lower() == p["title"].strip().lower() for c in shortlist):
                        found += 1
                    else:
                        misses.append(f"{brand}: '{target}' -> wanted '{p['title']}'")
        finally:
            logging.disable(logging.NOTSET)
        recall = found / total
        self.assertGreaterEqual(total, 250)
        self.assertGreaterEqual(recall, 0.99, f"Shortlist recall {recall:.3f} on {total} products; misses: {misses[:10]}")


class TestReviewFindings(unittest.TestCase):
    """Guards for defects found by the independent review of the verifier rebuild."""

    def test_price_guard_accepts_normal_dealer_margins(self):
        from src.utils.product_verifier import _price_out_of_range as out
        self.assertFalse(out(3999, 1100, None))     # MRP on page vs DP only on sheet
        self.assertFalse(out(12999, 7500, 12999))   # >10k product
        self.assertFalse(out(129.99, 7500, None))   # unreliable tiny parse ignored
        self.assertTrue(out(24999, 1200, 2999))     # clearly another product / bundle

    def test_visible_mrp_above_10k_not_divided(self):
        from src.parsers.shopify import ShopifyParser
        h = ('<html><head><title>X</title></head><body><h1 class="product__title">Big Speaker</h1>'
             '<div class="product__description rte">40W output speaker</div>'
             '<span class="price-item--regular">MRP: ₹12,999</span></body></html>')
        self.assertEqual(ShopifyParser().parse(url="https://x/products/a", html=h, category="Speaker").mrp, 12999.0)

    def test_candidate_with_nonpositive_score_not_valid(self):
        from src.utils.scraper import record_candidate
        c = []
        record_candidate(c, "https://x/products/a", "Luxcell Mini", -5.0, True, "", "t", "Luxcell Mini", "Portronics")
        self.assertFalse(c[0]["valid"])

    def test_anc_variant_is_a_different_model(self):
        ok = score_candidate_match("Airdopes 141", "boAt Airdopes 141 ANC", "https://www.croma.com/p/1",
                                   brand="boAt", qualifier_tokens=["Pro"], category="TWS")[1]
        self.assertFalse(ok)

    def test_ai_timeout_does_not_block(self):
        import time
        from unittest.mock import patch, MagicMock
        from src.utils import llm_client
        from src.utils.product_verifier import ProductMatchVerdict
        llm_client.reset_provider_states()
        slow = MagicMock()
        slow.chat.completions.create.side_effect = lambda **k: time.sleep(3)
        t0 = time.time()
        with patch.object(llm_client, "get_groq_client", return_value=slow):
            data, _ = llm_client.call_llm_json("s", "u", ProductMatchVerdict,
                                               llm_config={"providers": [{"name": "groq", "model": "g"}]}, timeout_s=0.5)
        self.assertIsNone(data)
        self.assertLess(time.time() - t0, 2.0)

    def test_groq_numbers_and_reasoning_text_accepted(self):
        from unittest.mock import patch, MagicMock
        from src.utils import llm_client
        llm_client.reset_provider_states()
        msg = MagicMock(); msg.message.content = '<think>checking</think>{"capacity": "10000 mAh", "weight": 380}'
        client = MagicMock(); client.chat.completions.create.return_value = MagicMock(choices=[msg])
        with patch.object(llm_client, "get_groq_client", return_value=client):
            data, prov = llm_client.call_llm_json("s", "u", llm_client.VisionExtractedSpecsSchema,
                                                  llm_config={"vision_providers": [{"name": "groq", "model": "q"}]},
                                                  images=[(b"x", "image/png")])
        self.assertEqual(data["weight"], "380")

    def test_page_without_readable_specs_is_verified_not_blacklisted(self):
        from unittest.mock import patch
        from src.parsers.base import ParserResult
        collect = importlib.import_module("src.1_collect")
        url = "https://www.volto.in/products/volto-max-20k"
        vision_calls = []

        def fake_search(*a, **k):
            k["out_candidates"].append({"url": url, "title": "Volto Max 20K", "score": 250.0, "valid": True, "reason": "", "source": "t"})
            return url

        def fake_vision(page_url, *a, **k):
            vision_calls.append(page_url)
            return ParserResult(success=True, status_code=200, url=page_url, tier=1, specs={"capacity": "20000 mAh", "output": "22.5W"})

        cfg = {"domain": "volto.in", "platform": "shopify", "qualifier_tokens": [], "retail_order": []}
        with patch.object(collect, "search_shopify_brand_store", side_effect=fake_search), \
             patch.object(collect, "fetch_and_parse_url", return_value=ParserResult(success=False, status_code=200, url=url, is_delisted=True, error="Page lacks technical specs")), \
             patch("src.utils.product_verifier.verify_product_page", return_value=("match", "same product", "groq:g")), \
             patch.object(collect, "execute_vision_fallback_for_page", side_effect=fake_vision), \
             patch.object(collect, "load_brand_defaults", return_value=cfg), \
             patch.object(collect, "parse_brochure_for_model", return_value=None), \
             patch.object(collect, "draft_bullets_and_subtitle", return_value=({"title": "Max", "subtitle": "s.", "bullet_1": "b1.", "bullet_2": "b2."}, "groq")):
            updates, ok, _ = collect.collect_data_for_row({"Product_ID": "P", "Brand": "Volto", "Model_Name": "Volto Max 20K", "Category": "Powerbank"}, {"llm": {}})
        self.assertTrue(ok)
        self.assertEqual(vision_calls, [url])
        self.assertIsNone(updates.get("Rejected_URLs"))


if __name__ == "__main__":
    unittest.main()

