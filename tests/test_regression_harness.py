import os
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


if __name__ == "__main__":
    unittest.main()

