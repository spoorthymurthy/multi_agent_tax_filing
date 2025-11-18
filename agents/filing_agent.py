# agents/filing_agent.py
import os
import json
import math
import glob
from datetime import datetime
from dotenv import load_dotenv

# optional Groq client (graceful fallback if not available or API key missing)
try:
    from groq import Groq, GroqError  # type: ignore
    _HAS_GROQ = True
except Exception:
    _HAS_GROQ = False

load_dotenv()


def safe_num(val, default=0.0):
    """Convert val to float when possible, else return default."""
    try:
        if val is None:
            return float(default)
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).strip().replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "")
        return float(s) if s != "" else float(default)
    except Exception:
        return float(default)


def fmt_money(val):
    """Format numeric as rupee string."""
    try:
        n = safe_num(val, 0.0)
        if math.isclose(n, round(n)):
            return f"₹{int(round(n)):,}"
        else:
            return f"₹{n:,.2f}"
    except Exception:
        return str(val)


# ---------------------------
# FilingAgent
# ---------------------------
class FilingAgent:
    def __init__(self, groq_api_key=None):
        api_key = groq_api_key or os.getenv("GROQ_API_KEY")
        self.client = None
        if _HAS_GROQ and api_key:
            try:
                try:
                    self.client = Groq(api_key=api_key)
                except TypeError as e:
                    if "proxies" in str(e):
                        import httpx
                        http_client = httpx.Client()
                        self.client = Groq(api_key=api_key, http_client=http_client)
                    else:
                        raise
            except Exception:
                self.client = None

    # -------------------------
    # Normalize calc_result
    # -------------------------
    def _normalize_calc_result(self, calc_result):
        # already normalized
        if isinstance(calc_result, dict) and "consolidated" in calc_result:
            # Format assessment year if needed
            consolidated = calc_result.get("consolidated", {})
            ay = consolidated.get("assessment_year", "")
            if isinstance(ay, str) and len(ay) == 6 and ay.isdigit():
                consolidated["assessment_year"] = f"{ay[:4]}-{ay[4:]}"
            return calc_result

        # CalculationAgent output format
        if isinstance(calc_result, dict) and "core_fields" in calc_result and "calculation" in calc_result:
            core = calc_result.get("core_fields", {})
            calc = calc_result.get("calculation", {})
            breakdown = calc.get("breakdown", {})
            income_breakdown = breakdown.get("income_breakdown", {})
            
            # Reconstruct consolidated structure from calculation output
            ay = core.get("assessment_year") or calc_result.get("taxpayer", {}).get("assessment_year") or ""
            # Format assessment year (202425 -> 2024-25)
            if isinstance(ay, str) and len(ay) == 6 and ay.isdigit():
                ay = f"{ay[:4]}-{ay[4:]}"
            
            # Build income components, only including non-zero values
            income_comps = {
                "Gross Salary": income_breakdown.get("salary", core.get("gross_salary", 0)),
                "Interest Income": income_breakdown.get("interest", core.get("interest_income", 0)),
                "Total Income": breakdown.get("gross_total_income", 0)
            }
            # Only add rental/other income if non-zero
            rental = income_breakdown.get("rental", core.get("rental_income", 0))
            if rental and safe_num(rental) > 0:
                income_comps["Rental Income"] = rental
            other = income_breakdown.get("other", core.get("other_income", 0))
            if other and safe_num(other) > 0:
                income_comps["Other Income"] = other
            
            consolidated = {
                "name": core.get("name") or calc_result.get("taxpayer", {}).get("name"),
                "pan": core.get("pan") or calc_result.get("pan") or calc_result.get("taxpayer", {}).get("pan"),
                "assessment_year": ay,
                "address": core.get("address") or core.get("_address"),
                "income_components": income_comps,
                "deductions": {
                    "Standard Deduction": breakdown.get("standard_deduction", core.get("standard_deduction", 0)),
                    "Professional Tax": breakdown.get("professional_tax", core.get("professional_tax", 0)),
                    "Section 24 (House Property Interest)": breakdown.get("section_24_deduction", core.get("section_24_deduction", 0)),
                    "Total Deductions": breakdown.get("total_deductions", 0)
                },
                "sources": [],
                "tds": breakdown.get("tds_total", core.get("tds", 0))
            }
            
            # Add Chapter VI-A deductions from breakdown
            chapter_vi_a_breakdown = breakdown.get("chapter_vi_a", {})
            if isinstance(chapter_vi_a_breakdown, dict):
                for k, v in chapter_vi_a_breakdown.items():
                    if v > 0:
                        consolidated["deductions"][k] = v
            else:
                # Fallback to core_fields chapter_vi_a
                chapter_vi_a = core.get("chapter_vi_a", {})
                if isinstance(chapter_vi_a, dict):
                    for k, v in chapter_vi_a.items():
                        if v > 0:
                            consolidated["deductions"][k] = v
            
            result = {
                "consolidated": consolidated,
                "capped_deductions": consolidated.get("deductions", {}),
                "taxable_income": calc.get("taxable_new", calc.get("taxable_old", 0)),
                "regimes": {
                    "chosen": {
                        "regime": calc.get("chosen_regime", "new"),
                        "final_tax": calc.get("chosen_tax", 0)
                    }
                },
                "refund_info": {
                    "status": "pending" if calc.get("tax_due", 0) > 0 else "refund",
                    "balance": calc.get("refund", 0)
                }
            }
            return result

        # single parsed file result from DocumentAgent
        if isinstance(calc_result, dict) and "structured_data" in calc_result:
            s = calc_result.get("structured_data") or {}
            ay = s.get("assessment_year", "")
            # Format assessment year (202425 -> 2024-25)
            if isinstance(ay, str) and len(ay) == 6 and ay.isdigit():
                ay = f"{ay[:4]}-{ay[4:]}"
            consolidated = {
                "name": s.get("name") or s.get("employee_name") or s.get("taxpayer"),
                "pan": (s.get("pan") or "").strip() or None,
                "assessment_year": ay,
                "address": s.get("_address") or s.get("address"),
                "income_components": s.get("income_components", {}),
                "deductions": s.get("deductions", {}),
                "sources": [calc_result.get("document_type", "Document")],
                "tds": safe_num(s.get("tds_amount", s.get("tds", 0))),
            }

            # compute simple taxable_income fallback
            income_vals = [safe_num(v) for v in consolidated["income_components"].values()]
            deductions_vals = [safe_num(v) for v in consolidated["deductions"].values()]

            taxable_income = 0.0
            if consolidated["income_components"].get("Taxable Income"):
                taxable_income = safe_num(consolidated["income_components"].get("Taxable Income"))
            elif income_vals:
                taxable_income = max(sum(income_vals) - sum(deductions_vals), 0.0)

            result = {
                "consolidated": consolidated,
                "capped_deductions": consolidated.get("deductions", {}),
                "taxable_income": taxable_income,
                "regimes": {"chosen": {"regime": "new", "final_tax": 0.0}},
                "refund_info": {"status": "pending", "balance": 0.0},
            }
            return result

        # path to json file
        if isinstance(calc_result, str) and os.path.exists(calc_result):
            try:
                with open(calc_result, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                    return obj
            except Exception:
                pass

        raise ValueError("Unsupported calc_result format passed to FilingAgent.process")

    # -------------------------
    # Try to auto-find a PAN from parsed files if missing
    # -------------------------

    # -------------------------
    # ITR selection + explanation
    # -------------------------
    def detect_itr_form(self, consolidated):
        ic = consolidated.get("income_components", {}) or {}
        income_keys = [k.lower() for k in ic.keys()]

        reason_lines = []
        form = "ITR-1"  # default

        # checks - only trigger if actual income > 0
        capital_gains = safe_num(ic.get("Capital Gains", 0)) or safe_num(ic.get("Long Term Capital Gains", 0)) or safe_num(ic.get("Short Term Capital Gains", 0))
        if capital_gains > 0:
            form = "ITR-2"
            reason_lines.append("Capital gains detected → ITR-2 required.")
        
        rental_income = safe_num(ic.get("Rental Income", 0))
        if rental_income > 0:
            form = "ITR-2"
            reason_lines.append("Rental income detected → ITR-2 required.")
        
        business_income = safe_num(ic.get("Business Income", 0)) or safe_num(ic.get("Profession Income", 0)) or safe_num(ic.get("Income from Business", 0))
        if business_income > 0:
            form = "ITR-3"
            reason_lines.append("Business or professional income detected → ITR-3 required.")

        # total income check (fallback to Total Income if present)
        total_income = safe_num(
            ic.get("Total Income")
            or ic.get("total")
            or ic.get("Gross Salary")
            or sum(safe_num(v) for v in ic.values())
        )

        if total_income > 5_000_000 and form == "ITR-1":
            form = "ITR-2"
            reason_lines.append(f"Total income {fmt_money(total_income)} exceeds ₹50L → ITR-2 selected.")

        if not reason_lines:
            reason_lines.append("Income predominantly from salary and interest within ₹50L — ITR-1 eligible.")

        reason = " ".join(reason_lines)
        explanation = {
            "selected_form": form,
            "why_selected": reason,
            "checks_performed": {"income_keys": list(ic.keys()), "total_income": total_income}
        }
        return {"itr_form": form, "reason": reason, "explanation": explanation}

    # -------------------------
    # Input validation
    # -------------------------
    def validate_inputs(self, calc_result):
        missing = []
        c = (calc_result.get("consolidated") or {})
        if not c.get("name"):
            missing.append("Name")
        if not c.get("pan"):
            missing.append("PAN")
        if not c.get("assessment_year"):
            missing.append("Assessment Year")
        if not c.get("income_components"):
            missing.append("Income details")
        return {"missing_fields": missing, "is_valid": len(missing) == 0}

    # -------------------------
    # Tax computations
    # -------------------------
    def compute_tax_new_regime(self, taxable_income):
        ti = safe_num(taxable_income)
        # Correct new regime slabs for AY 2024-25
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
        cess = round(tax_before_cess * 0.04, 2)
        return round(tax_before_cess + cess, 2)

    def compute_tax_old_regime(self, taxable_income):
        ti = safe_num(taxable_income)
        tax = 0.0
        if ti <= 250000:
            tax = 0
        elif ti <= 500000:
            tax = (ti - 250000) * 0.05
        elif ti <= 1000000:
            tax = 12500 + (ti - 500000) * 0.20
        else:
            tax = 12500 + 100000 + (ti - 1000000) * 0.30
        tax = round(tax * 1.04, 2)
        return tax

    # -------------------------
    # AI review via Groq (safe)
    # -------------------------
    def ai_review(self, itr_json, model="llama-3.1-8b-instant"):
        if not self.client:
            return {"review_findings": ["AI review unavailable (GROQ not configured)."], "filing_advice": []}

        prompt = f"""
You are an experienced Indian Chartered Accountant providing a neutral summary of the ITR JSON data.
Provide a factual summary of what information is present in the ITR JSON and explain the rationale behind the calculations.

Focus on:
1. Summary of income sources found (salary, interest, rental, etc.)
2. Summary of deductions claimed and why they are applicable
3. Explanation of tax regime selection and tax computation
4. Overall filing status (refund/tax due/no refund)

Do NOT include:
- Warnings or negative feedback
- Errors or discrepancies
- Suggestions for improvement
- Critical comments

Return valid JSON with keys: review_findings (list), filing_advice (list).
Keep review_findings as a neutral summary. Keep filing_advice empty.

ITR JSON:
{json.dumps(itr_json, indent=2, ensure_ascii=False)}
"""
        try:
            resp = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a CA providing a neutral, factual summary of ITR data. Focus on what is found and why, without warnings or negative feedback."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.25,
                max_tokens=1000
            )
            text = resp.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.replace("```json", "").replace("```", "").strip()
            try:
                parsed = json.loads(text)
                parsed.setdefault("review_findings", [])
                parsed.setdefault("filing_advice", [])
                return parsed
            except Exception:
                return {"review_findings": [text], "filing_advice": []}
        except Exception as e:
            error_msg = str(e)
            if "401" in error_msg or "Invalid API Key" in error_msg or "invalid_api_key" in error_msg:
                return {"review_findings": ["AI review unavailable: Please configure a valid GROQ_API_KEY in your .env file."], "filing_advice": []}
            elif "429" in error_msg or "rate limit" in error_msg.lower():
                return {"review_findings": ["AI review temporarily unavailable due to rate limits. Please try again later."], "filing_advice": []}
            else:
                return {"review_findings": ["AI review temporarily unavailable. Tax calculations are still accurate."], "filing_advice": []}

    # -------------------------
    # Checklist generation
    # -------------------------
    def generate_checklist(self, calc_result):
        checklist = []
        c = (calc_result.get("consolidated") or {})
        docs = " ".join([str(x) for x in c.get("sources", [])])
        if "AIS" not in docs:
            checklist.append("Fetch and upload AIS from Income Tax portal for reconciliation.")
        if not c.get("deductions") or safe_num(c.get("deductions", {}).get("Total Deductions", 0)) == 0:
            checklist.append("Upload proofs for claimed deductions (80C, 80D, home loan interest, etc.).")
        checklist.append("Verify bank account details for refund.")
        return checklist

    # -------------------------
    # PDF generation
    # -------------------------
    # -------------------------
    # Main entrypoint
    # -------------------------
    def process(self, calc_result):
        norm = self._normalize_calc_result(calc_result)

        # If pan missing, attempt to auto-find
        consolidated = norm.get("consolidated", {})
        if not consolidated.get("pan"):
            found = self._auto_find_pan()
            if found:
                consolidated["pan"] = found
                norm["consolidated"] = consolidated

        v = self.validate_inputs(norm)
        if not v["is_valid"]:
            # Give a clearer error message that shows what's missing
            raise ValueError(f"Missing essential data: {v['missing_fields']}")

        consolidated = norm["consolidated"]

        itr_form = self.detect_itr_form(consolidated)

        taxable_income = safe_num(norm.get("taxable_income", 0.0))
        
        # Use CalculationAgent's tax computation if available, otherwise compute here
        if isinstance(calc_result, dict) and "calculation" in calc_result:
            calc = calc_result.get("calculation", {})
            new_tax = calc.get("tax_new", self.compute_tax_new_regime(taxable_income))
            old_tax = calc.get("tax_old", self.compute_tax_old_regime(taxable_income))
            chosen_regime = calc.get("chosen_regime", "new" if new_tax <= old_tax else "old")
            chosen_tax = calc.get("chosen_tax", min(new_tax, old_tax))
        else:
            new_tax = self.compute_tax_new_regime(taxable_income)
            old_tax = self.compute_tax_old_regime(taxable_income)
            chosen_regime = "new" if new_tax <= old_tax else "old"
            chosen_tax = min(new_tax, old_tax)
        
        regimes = {"chosen": {"regime": chosen_regime, "final_tax": chosen_tax}}

        # Format assessment year (202425 -> 2024-25)
        ay = consolidated.get("assessment_year", "")
        if isinstance(ay, str) and len(ay) == 6 and ay.isdigit():
            ay = f"{ay[:4]}-{ay[4:]}"
        
        # Calculate total income from components (must include all income sources)
        income_components = consolidated.get("income_components", {})
        gross_salary = safe_num(income_components.get("Gross Salary", 0))
        interest_income = safe_num(income_components.get("Interest Income", 0))
        rental_income = safe_num(income_components.get("Rental Income", 0))
        other_income = safe_num(income_components.get("Other Income", 0))
        total_income_calc = gross_salary + interest_income + rental_income + other_income
        
        # Update Total Income if missing or incorrect
        if "Total Income" not in income_components or abs(safe_num(income_components.get("Total Income", 0)) - total_income_calc) > 100:
            income_components["Total Income"] = total_income_calc
        
        # Remove zero income components from output (only show salary and interest if present)
        filtered_income = {}
        if gross_salary > 0:
            filtered_income["Gross Salary"] = gross_salary
        if interest_income > 0:
            filtered_income["Interest Income"] = interest_income
        # Only include rental/other income if they have non-zero values
        if rental_income > 0:
            filtered_income["Rental Income"] = rental_income
        if other_income > 0:
            filtered_income["Other Income"] = other_income
        filtered_income["Total Income"] = total_income_calc
        
        # Ensure standard deduction is always present (mandatory for salaried)
        deductions = norm.get("capped_deductions", consolidated.get("deductions", {}))
        if safe_num(deductions.get("Standard Deduction", 0)) == 0:
            deductions["Standard Deduction"] = 50000.0
        
        # Calculate total deductions (EXCLUDE "Total Deductions" itself to avoid doubling)
        total_deductions = sum(safe_num(v) for k, v in deductions.items() if k != "Total Deductions")
        
        # Use taxable income from CalculationAgent if available, otherwise calculate
        if isinstance(calc_result, dict) and "calculation" in calc_result:
            calc = calc_result.get("calculation", {})
            # Use the correct taxable income from calculation (new regime for chosen regime)
            if chosen_regime == "new":
                taxable_income = calc.get("taxable_new", max(0, total_income_calc - total_deductions))
            else:
                taxable_income = calc.get("taxable_old", max(0, total_income_calc - total_deductions))
        else:
            taxable_income = max(0, total_income_calc - total_deductions)
        
        # Update deductions total
        deductions["Total Deductions"] = total_deductions
        
        # Calculate refund status
        tds = safe_num(consolidated.get("tds", 0))
        tax_due = max(0, chosen_tax - tds)
        refund = max(0, tds - chosen_tax)
        
        # Get address from consolidated
        address = consolidated.get("address") or consolidated.get("_address")
        
        taxpayer_info = {
            "name": consolidated.get("name"),
            "pan": consolidated.get("pan") or "UNKNOWN",
            "assessment_year": ay,
        }
        if address:
            taxpayer_info["address"] = address
        
        itr_json = {
            "schema_version": "ITR_AY2024_25_v1",
            "form_name": itr_form["itr_form"],
            "filing_date": datetime.now().strftime("%Y-%m-%d"),
            "taxpayer": taxpayer_info,
            "income_details": filtered_income,
            "deductions": deductions,
            "taxable_income": round(taxable_income, 2),
            "tax_computed": regimes["chosen"],
            "refund_info": {
                "status": "refund" if refund > 0 else ("pending" if tax_due > 0 else "no_refund"),
                "balance": round(refund, 2),
                "tax_due": round(tax_due, 2)
            },
            "reason_for_itr": itr_form.get("reason"),
            "explanation_for_selection": itr_form.get("explanation", {}),
        }

        review = self.ai_review(itr_json)
        checklist = self.generate_checklist(norm)

        result = {
            "itr_form": {"itr_form": itr_json["form_name"], "reason": itr_json["reason_for_itr"]},
            "reason": itr_json["reason_for_itr"],
            "itr_json": itr_json,
            "review": review,
            "checklist": checklist,
            "regimes": regimes,
        }

        print("✅ FilingAgent.process completed:", {
            "pan": itr_json["taxpayer"].get("pan"),
            "form": itr_json["form_name"],
            "taxable_income": fmt_money(itr_json["taxable_income"]),
            "final_tax": fmt_money(itr_json["tax_computed"].get("final_tax"))
        })

        return result
