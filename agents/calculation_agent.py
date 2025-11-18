# agents/calculation_agent.py
"""CalculationAgent: Performs CA-style tax computation (Old vs New regimes)"""
from __future__ import annotations

import os
import json
from datetime import datetime
from typing import Any, Dict

OUTPUT_DIR = "data/outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def _remove_zero_values(obj, keep_keys=None):
    """Recursively remove zero values, empty dicts, and None values from nested structures.
    keep_keys: list of keys to always keep even if zero (e.g., ['refund', 'tax_due'])
    """
    if keep_keys is None:
        keep_keys = ['refund', 'tax_due', 'schema_version', 'pan', 'name', 'assessment_year', 'generated_on', 'output_path']
    
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            # Always keep structural/important keys
            if k in keep_keys:
                result[k] = v
                continue
            
            cleaned = _remove_zero_values(v, keep_keys)
            # Skip if value is 0, None, empty dict/list, or empty string
            if cleaned is None or cleaned == 0 or cleaned == 0.0 or cleaned == "":
                continue
            if isinstance(cleaned, (dict, list)) and len(cleaned) == 0:
                continue
            result[k] = cleaned
        return result
    elif isinstance(obj, list):
        result = []
        for item in obj:
            cleaned = _remove_zero_values(item, keep_keys)
            if cleaned is not None and cleaned != 0 and cleaned != 0.0 and cleaned != "":
                if not (isinstance(cleaned, (dict, list)) and len(cleaned) == 0):
                    result.append(cleaned)
        return result
    else:
        return obj


def _to_number(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x)
        s = s.replace("(", "-").replace(")", "")
        s = s.replace("₹", "").replace("Rs", "").replace(",", "").strip()
        if s == "":
            return float(default)
        return float(s)
    except Exception:
        return float(default)


def compute_hra_exemption(basic: float, hra_received: float, rent_paid: float, is_metro: bool = False) -> float:
    """HRA exemption = min(actual HRA, rent_paid - 10% basic, 50%/40% basic)"""
    if basic <= 0 or hra_received <= 0:
        return 0.0
    return round(min(hra_received, max(0.0, rent_paid - 0.10 * basic), (0.5 if is_metro else 0.4) * basic), 2)


def compute_lta_exemption(lta_received: float, actual_lta_claimed: float) -> float:
    """LTA exemption = min(lta_received, actual_lta_claimed)"""
    if lta_received <= 0:
        return 0.0
    return round(min(lta_received, actual_lta_claimed or lta_received), 2)


def compute_children_education_exemption(amount: float) -> float:
    """Children education exemption capped at ₹1,200 per child per year"""
    return round(min(amount, 1200.0), 2) if amount > 0 else 0.0


def compute_tax_old_regime(ti: float) -> tuple[float, float, float]:
    """Compute tax under old regime (AY 2024-25). Returns: (tax_before_cess, cess_4pct, total_tax)"""
    ti = max(0.0, float(ti))
    tax_before_cess = 0.0
    
    # Old regime slabs (AY 2024-25)
    if ti <= 250000:
        tax_before_cess = 0.0
    elif ti <= 500000:
        tax_before_cess = (ti - 250000) * 0.05
    elif ti <= 1000000:
        tax_before_cess = 12500 + (ti - 500000) * 0.20
    else:
        tax_before_cess = 12500 + 100000 + (ti - 1000000) * 0.30

    tax_before_cess = round(tax_before_cess, 2)
    cess_4pct = round(tax_before_cess * 0.04, 2)
    total_tax = round(tax_before_cess + cess_4pct, 2)
    
    return (tax_before_cess, cess_4pct, total_tax)


def compute_tax_new_regime(ti: float) -> tuple[float, float, float]:
    """Compute tax under new regime (AY 2024-25). Returns: (tax_before_cess, cess_4pct, total_tax)"""
    ti = max(0.0, float(ti))
    tax_before_cess = 0.0
    if ti > 300000:
        tax_before_cess += min(400000, ti - 300000) * 0.05
    if ti > 700000:
        tax_before_cess += min(300000, ti - 700000) * 0.10
    if ti > 1000000:
        tax_before_cess += min(200000, ti - 1000000) * 0.15
    if ti > 1200000:
        tax_before_cess += min(300000, ti - 1200000) * 0.20
    if ti > 1500000:
        tax_before_cess += (ti - 1500000) * 0.30
    
    tax_before_cess = round(tax_before_cess, 2)
    cess_4pct = round(tax_before_cess * 0.04, 2)
    total_tax = round(tax_before_cess + cess_4pct, 2)
    
    return (tax_before_cess, cess_4pct, total_tax)


class CalculationAgent:
    def __init__(self, output_dir: str = OUTPUT_DIR):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def _heuristic_extract(self, calc_result: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize input to core numeric fields"""
        normalized = {
            "name": None,
            "pan": None,
            "assessment_year": None,
            "basic": 0.0,
            "hra_received": 0.0,
            "rent_paid": 0.0,
            "gross_salary": 0.0,
            "standard_deduction": 50000.0,  # default for salaried -> can be overridden by inputs
            "professional_tax": 0.0,
            "chapter_vi_a": {},  # dict of deductions like 80C,80D,80CCD etc
            "total_deductions": 0.0,
            "interest_income": 0.0,
            "rental_income": 0.0,
            "other_income": 0.0,
            "tds": 0.0,
            "period": {"from": None, "to": None}
        }

        # If consolidated shape
        cons = calc_result.get("consolidated") if isinstance(calc_result, dict) and "consolidated" in calc_result else None
        if cons:
            s = cons.get("income_components", {}) or {}
            d = cons.get("deductions", {}) or {}
            normalized["name"] = cons.get("name") or normalized["name"]
            normalized["pan"] = cons.get("pan") or normalized["pan"]
            normalized["assessment_year"] = cons.get("assessment_year") or normalized["assessment_year"]
            normalized["period"] = cons.get("period") or normalized["period"]

            # common keys: Gross Salary, Basic, HRA, Net Salary, Taxable Income, Standard Deduction
            for k, v in s.items():
                kl = k.strip().lower()
                if "gross" in kl and "total" not in kl:  # Exclude "Total Income"
                    # CRITICAL: Use max() instead of sum() for gross salary
                    normalized["gross_salary"] = max(normalized["gross_salary"], _to_number(v))
                elif kl in ("basic", "basic salary", "basic pay"):
                    normalized["basic"] = max(normalized["basic"], _to_number(v))
                elif "hra" in kl and "hra exempt" not in kl:
                    normalized["hra_received"] = max(normalized["hra_received"], _to_number(v))
                elif "taxable" in kl or "total income" in kl:
                    # CRITICAL: Don't add "Total Income" to other_income
                    # handled downstream
                    pass
                elif "interest" in kl:
                    # CRITICAL: Use max() instead of sum() for interest
                    normalized["interest_income"] = max(normalized["interest_income"], _to_number(v))
                elif "rent" in kl and "rent paid" in kl:
                    normalized["rent_paid"] += _to_number(v)
                elif "rental" in kl and "income" in kl:
                    # Rental income - use max()
                    normalized["rental_income"] = max(normalized["rental_income"], _to_number(v))
                else:
                    # CRITICAL: Only add to other_income if it's not a known income component
                    # Skip: "Total Income", "Gross Salary", "Interest Income", etc.
                    if kl not in ("total income", "gross salary", "interest income", "rental income", "other income"):
                        val = _to_number(v)
                        if val > 0 and val <= 10000000:  # Validate
                            normalized["other_income"] = max(normalized["other_income"], val)

            # CRITICAL: Extract deductions from consolidated.deductions
            for k, v in d.items():
                kl = k.strip().lower()
                num = _to_number(v)
                if "80c" in kl or "80 c" in kl:
                    normalized["chapter_vi_a"]["80C"] = num
                elif "80d" in kl:
                    normalized["chapter_vi_a"]["80D"] = num
                elif "80ccd" in kl:
                    normalized["chapter_vi_a"]["80CCD"] = num
                elif "professional tax" in kl or "prof tax" in kl:
                    # CRITICAL: Extract professional tax from consolidated deductions
                    normalized["professional_tax"] = num
                elif "section 24" in kl or "house property" in kl or "house property interest" in kl:
                    # CRITICAL: Extract Section 24 from consolidated deductions
                    normalized["section_24_deduction"] = num
                elif "standard deduction" in kl:
                    normalized["standard_deduction"] = num
                # Don't add other deductions to total_deductions here - it will be computed in _compute_tax_flow

            normalized["tds"] = _to_number(cons.get("tds") or cons.get("tds_amount") or 0.0)

        else:
            # If calc_result is a single structured_data dict from DocumentAgent
            if isinstance(calc_result, dict) and "structured_data" in calc_result:
                s = calc_result.get("structured_data") or {}
                ic = s.get("income_components", {}) or {}
                d = s.get("deductions", {}) or {}
                normalized["name"] = s.get("name") or normalized["name"]
                normalized["pan"] = s.get("pan") or normalized["pan"]
                normalized["assessment_year"] = s.get("assessment_year") or normalized["assessment_year"]
                normalized["period"] = s.get("period") or normalized["period"]

                for k, v in ic.items():
                    kl = k.strip().lower()
                    if "gross" in kl:
                        normalized["gross_salary"] += _to_number(v)
                    elif "basic" in kl:
                        normalized["basic"] = _to_number(v)
                    elif "hra" in kl:
                        normalized["hra_received"] = _to_number(v)
                    elif "net" in kl:
                        normalized["net_salary"] = _to_number(v)
                    elif "interest" in kl:
                        normalized["interest_income"] += _to_number(v)
                    else:
                        normalized["other_income"] += _to_number(v)

                for k, v in d.items():
                    kl = k.strip().lower()
                    num = _to_number(v)
                    if "80c" in kl or "80 c" in kl:
                        normalized["chapter_vi_a"]["80C"] = num
                    elif "80d" in kl:
                        normalized["chapter_vi_a"]["80D"] = num
                    elif "80ccd" in kl:
                        normalized["chapter_vi_a"]["80CCD"] = num
                    else:
                        normalized["total_deductions"] += num

                normalized["tds"] = _to_number(s.get("tds_amount") or s.get("tds") or 0.0)
            else:
                # try to accept raw minimal dict
                for k in normalized.keys():
                    if isinstance(calc_result.get(k), (int, float, str)):
                        normalized[k] = _to_number(calc_result.get(k, normalized[k]))

        # CRITICAL: Don't finalize total_deductions here - it will be computed in _compute_tax_flow
        # The _heuristic_extract should only collect raw values, not compute totals
        # total_deductions will be computed as: standard_deduction + professional_tax + chapter_vi_a
        # Keep normalized["total_deductions"] as is (it may contain other deductions from payslip)
        # if gross salary empty but basic present, set gross ~ basic
        if normalized["gross_salary"] == 0 and normalized["basic"]:
            normalized["gross_salary"] = normalized["basic"]

        return normalized

    def _compute_tax_flow(self, core: Dict[str, Any], is_metro: bool = False) -> Dict[str, Any]:
        """Complete tax flow calculation: Gross Total Income → Deductions → Exemptions → Tax"""
        basic = _to_number(core.get("basic_salary") or core.get("basic", 0.0))
        hra_received = _to_number(core.get("hra_received", 0.0))
        rent_paid = _to_number(core.get("rent_paid", 0.0))
        lta_received = _to_number(core.get("lta", 0.0))
        gross_salary = _to_number(core.get("gross_salary", 0.0))
        standard_ded = _to_number(core.get("standard_deduction", 50000.0))
        # CRITICAL: Extract professional_tax from core (should be set from consolidated.deductions)
        prof_tax = _to_number(core.get("professional_tax", 0.0))
        # If still 0, try to get from deductions dict
        if prof_tax == 0 and isinstance(core.get("deductions"), dict):
            prof_tax = _to_number(core.get("deductions", {}).get("Professional Tax", 0))
        
        interest = _to_number(core.get("interest_income", 0.0))
        rental = _to_number(core.get("rental_income", 0.0))
        capital_gains = _to_number(core.get("capital_gains", 0.0))
        other = _to_number(core.get("other_income", 0.0))
        
        chapter_vi_a = core.get("chapter_vi_a", {}) or {}
        deduction_80c = _to_number(chapter_vi_a.get("80C") or core.get("deduction_80c", 0.0))
        deduction_80d = _to_number(chapter_vi_a.get("80D") or core.get("deduction_80d", 0.0))
        deduction_80ccd = _to_number(chapter_vi_a.get("80CCD") or core.get("deduction_80ccd", 0.0))
        deduction_80tta = _to_number(core.get("deduction_80tta", 0.0))
        deduction_80g = _to_number(core.get("deduction_80g", 0.0))
        tds = _to_number(core.get("tds", 0.0))

        hra_exempt = compute_hra_exemption(basic, hra_received, rent_paid, is_metro=is_metro)
        lta_exempt = compute_lta_exemption(lta_received, core.get("lta_exempt", lta_received))
        children_edu_exempt = compute_children_education_exemption(_to_number(core.get("children_education_allowance", 0.0)))

        gross_total = round(gross_salary + interest + rental + capital_gains + other, 2)

        ch_80c_capped = min(deduction_80c, 150000.0)
        ch_80d_capped = min(deduction_80d, 50000.0)
        ch_80ccd_capped = min(deduction_80ccd, 50000.0)
        ch_80tta_capped = min(deduction_80tta, 10000.0)
        total_chapter_vi_a = round(ch_80c_capped + ch_80d_capped + ch_80ccd_capped + ch_80tta_capped + deduction_80g, 2)

        section_24 = _to_number(core.get("section_24_deduction", 0.0))
        if section_24 == 0 and isinstance(core.get("deductions"), dict):
            section_24 = _to_number(
                core.get("deductions", {}).get("Section 24 (House Property Interest)", 0) or
                core.get("deductions", {}).get("Section 24", 0) or
                core.get("deductions", {}).get("House Property Interest", 0) or
                core.get("house_property_interest", 0)
            )
        section_24_capped = min(section_24, 200000.0)
        
        total_deductions_old = round(standard_ded + prof_tax + total_chapter_vi_a + section_24_capped, 2)
        total_deductions_new = round(standard_ded, 2)
        core["total_deductions"] = total_deductions_old

        total_exemptions = hra_exempt + lta_exempt + children_edu_exempt
        taxable_old = max(0.0, gross_total - total_exemptions - total_deductions_old)
        taxable_new = max(0.0, gross_total - total_deductions_new)

        # compute tax for both regimes
        tax_old_before_cess, cess_old, tax_old = compute_tax_old_regime(taxable_old)
        tax_new_before_cess, cess_new, tax_new = compute_tax_new_regime(taxable_new)

        rebate_old = rebate_new = 0.0
        if taxable_old <= 500000:
            rebate_old = min(tax_old_before_cess, 12500.0)
            tax_old_before_cess = max(0.0, tax_old_before_cess - rebate_old)
            cess_old = round(tax_old_before_cess * 0.04, 2)
            tax_old = round(tax_old_before_cess + cess_old, 2)
        if taxable_new <= 500000:
            rebate_new = min(tax_new_before_cess, 12500.0)
            tax_new_before_cess = max(0.0, tax_new_before_cess - rebate_new)
            cess_new = round(tax_new_before_cess * 0.04, 2)
            tax_new = round(tax_new_before_cess + cess_new, 2)

        chosen_regime = "new" if tax_new <= tax_old else "old"
        chosen_tax = min(tax_new, tax_old)
        tax_due_raw = round(max(0.0, chosen_tax - tds), 2)
        refund_raw = round(max(0.0, tds - chosen_tax), 2)

        breakdown = {
            "basic": round(basic, 2),
            "hra_received": round(hra_received, 2),
            "lta_received": round(lta_received, 2),
            "gross_salary": round(gross_salary, 2),
            "hra_exempt": round(hra_exempt, 2),
            "lta_exempt": round(lta_exempt, 2),
            "children_education_exempt": round(children_edu_exempt, 2),
            "total_exemptions": round(total_exemptions, 2),
            "rent_paid": round(rent_paid, 2),
            "gross_total_income": round(gross_total, 2),
            "income_breakdown": {
                "salary": round(gross_salary, 2),
                "interest": round(interest, 2),
                "rental": round(rental, 2),
                "capital_gains": round(capital_gains, 2),
                "other": round(other, 2)
            },
            "standard_deduction": round(standard_ded, 2),
            "professional_tax": round(prof_tax, 2),
            "chapter_vi_a": {
                "80C": round(deduction_80c, 2),
                "80C_capped": round(ch_80c_capped, 2),
                "80D": round(deduction_80d, 2),
                "80D_capped": round(ch_80d_capped, 2),
                "80CCD": round(deduction_80ccd, 2),
                "80CCD_capped": round(ch_80ccd_capped, 2),
                "80TTA": round(deduction_80tta, 2),
                "80TTA_capped": round(ch_80tta_capped, 2),
                "80G": round(deduction_80g, 2),
                "total_chapter_vi_a": round(total_chapter_vi_a, 2)
            },
            "total_deductions": round(total_deductions_old, 2),
            "total_deductions_new": round(total_deductions_new, 2),
            "section_24_deduction": round(section_24_capped, 2),
            "tds_total": round(tds, 2)
        }

        result = {
            "taxable_old": round(taxable_old, 2),
            "taxable_new": round(taxable_new, 2),
            "tax_old_before_cess": round(tax_old_before_cess, 2),
            "cess_old_4pct": round(cess_old, 2),
            "tax_old": round(tax_old, 2),
            "tax_new_before_cess": round(tax_new_before_cess, 2),
            "cess_new_4pct": round(cess_new, 2),
            "tax_new": round(tax_new, 2),
            "rebate_old": round(rebate_old, 2),
            "rebate_new": round(rebate_new, 2),
            "chosen_regime": chosen_regime,
            "chosen_tax": round(chosen_tax, 2),
            "tax_due": tax_due_raw,
            "refund": refund_raw,
            "breakdown": breakdown
        }
        return result

    def process(self, calc_result: Dict[str, Any], is_metro: bool = False, save_json: bool = False) -> Dict[str, Any]:
        """Main entry point. Accepts consolidated dict or single document result"""
        core = self._heuristic_extract(calc_result)
        taxflow = self._compute_tax_flow(core, is_metro=is_metro)
        
        if taxflow.get("breakdown", {}).get("professional_tax", 0) > 0:
            core["professional_tax"] = taxflow["breakdown"]["professional_tax"]
        if taxflow.get("breakdown", {}).get("section_24_deduction", 0) > 0:
            core["section_24_deduction"] = taxflow["breakdown"]["section_24_deduction"]

        out = {
            "schema_version": "CALC_V1",
            "pan": core.get("pan") or "UNKNOWN",
            "taxpayer": {"name": core.get("name"), "pan": core.get("pan"), "assessment_year": core.get("assessment_year")},
            "core_fields": core,
            "calculation": taxflow,
            "generated_on": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        # Remove zero values for cleaner output
        out_cleaned = _remove_zero_values(out)

        if save_json:
            pan = (core.get("pan") or "UNKNOWN").replace("/", "_")
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"calculation_{pan}_{ts}.json"
            path = os.path.join(self.output_dir, fname)
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(out_cleaned, f, indent=4, ensure_ascii=False)
                out_cleaned["output_path"] = path
            except Exception:
                out_cleaned["output_path"] = None

        return out_cleaned
