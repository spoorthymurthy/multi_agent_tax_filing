# agents/calculation_agent.py
"""Tax calculation agent for Indian Income Tax (AY 2025-26) - Old vs New Regime"""
import os
import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

try:
    from groq import Groq
    _HAS_GROQ = True
except Exception:
    Groq = None
    _HAS_GROQ = False

load_dotenv()


def _fmt_inr(x: float) -> str:
    """Format number as Indian-rupee style: ₹1,23,456"""
    try:
        return f"₹{float(x):,.2f}"
    except Exception:
        return f"₹{x}"


def _align_label_value(label: str, value: str, width: int = 20) -> str:
    """Create aligned label : value with label padded to width."""
    return f"{label[:width].ljust(width)} : {value}"


class CalculationAgent:
    """Tax calculation agent matching exact Indian tax rules (AY 2025-26)"""

    def __init__(self, groq_api_key: Optional[str] = None):
        # Tax constants for AY 2025-26
        self.STANDARD_DEDUCTION = 50000.0
        self.CESS_RATE = 0.04
        
        # Initialize Groq client for AI-powered regime reasoning
        self.client = None
        api_key = groq_api_key or os.getenv("GROQ_API_KEY")
        if _HAS_GROQ and api_key:
            try:
                self.client = Groq(api_key=api_key)
            except TypeError as e:
                if "proxies" in str(e):
                    import httpx
                    self.client = Groq(api_key=api_key, http_client=httpx.Client())
            except Exception:
                self.client = None

    def _to_float(self, v) -> float:
        """Convert value to float safely, handling currency strings."""
        try:
            if isinstance(v, str):
                v = v.replace(",", "").replace("₹", "").strip()
            return float(v or 0.0)
        except Exception:
            return 0.0

    def _extract_income(self, data: dict) -> float:
        """Extract gross total income from various data structures."""
        # Try direct key first
        gross = self._to_float(data.get("gross_total_income", 0.0))
        if gross > 0:
            return gross

        # Try nested income_components
        inc_comp = data.get("income_components") or data.get("income", {})
        if isinstance(inc_comp, dict):
            gross = self._to_float(inc_comp.get("Total Income") or inc_comp.get("Total") or inc_comp.get("total_income"))
            if gross > 0:
                return gross

        # Fallback: Sum individual components
        return (self._to_float(data.get("salary", 0.0) or data.get("gross_salary", 0.0)) +
                self._to_float(data.get("interest_income", 0.0)) +
                self._to_float(data.get("rental_income", 0.0)) +
                self._to_float(data.get("other_income", 0.0)))

    def _extract_deductions(self, data: dict) -> dict:
        """Extract and process all deductions with proper caps and combinations."""
        ded_dict = data.get("deductions", {})
        
        # Extract deduction values (check both nested dict and top-level)
        prof_tax = self._to_float(ded_dict.get("Professional Tax", 0.0) or data.get("professional_tax", 0.0))
        deduction_80c = self._to_float(ded_dict.get("80C", 0.0) or data.get("deduction_80c", 0.0))
        nps_employee = self._to_float(ded_dict.get("NPS", 0.0) or data.get("nps_employee", 0.0))
        deduction_80d = self._to_float(ded_dict.get("80D", 0.0) or data.get("deduction_80d", 0.0))
        deduction_80ccd = self._to_float(ded_dict.get("80CCD", 0.0) or data.get("deduction_80ccd", 0.0))
        section_24 = self._to_float(ded_dict.get("Section 24 (House Property Interest)", 0.0) or data.get("section_24", 0.0))

        # Combined 80C + 80CCD(1) cap = ₹1,50,000
        combined_80c_80ccd1 = min(deduction_80c + nps_employee, 150000.0)
        
        # Additional NPS under 80CCD(1B) up to ₹50,000 (separate from 80C limit)
        nps_consumed = max(0.0, min(nps_employee, max(0.0, 150000.0 - deduction_80c)))
        nps_remaining = max(0.0, nps_employee - nps_consumed)
        extra_nps_80ccd1b = min(nps_remaining, 50000.0)

        return {
            "professional_tax": prof_tax,
            "80C_and_80CCD1": combined_80c_80ccd1,
            "80CCD_1B_extra": extra_nps_80ccd1b,
            "80D": deduction_80d,
            "80CCD": deduction_80ccd,
            "section_24": min(section_24, 200000.0)  # Cap at ₹2L
        }

    def process(self, data: dict) -> dict:
        """
        Main tax calculation process for AY 2025-26.
        
        Calculates tax under both Old and New regimes, selects optimal regime,
        and generates AI-powered reasoning.
        
        Returns: Complete calculation dict with 2-decimal precision.
        """
        # Extract income and deductions
        gross_total_income = round(self._extract_income(data), 2)
        ded = self._extract_deductions(data)

        # Calculate deductions for both regimes
        old_deductions = (self.STANDARD_DEDUCTION + ded["professional_tax"] + 
                         ded["80C_and_80CCD1"] + ded["80CCD_1B_extra"] + 
                         ded["80D"] + ded["80CCD"] + ded["section_24"])
        new_deductions = self.STANDARD_DEDUCTION  # New regime: Only Standard Deduction

        # Calculate taxable income
        taxable_old = max(0.0, gross_total_income - old_deductions)
        taxable_new = max(0.0, gross_total_income - new_deductions)

        # Calculate tax (before cess) using slab rates
        tax_old_before_cess = self._tax_old_regime(taxable_old)
        tax_new_before_cess = self._tax_new_regime(taxable_new)

        # Apply 4% cess
        cess_old = round(tax_old_before_cess * self.CESS_RATE, 2)
        cess_new = round(tax_new_before_cess * self.CESS_RATE, 2)
        tax_old = round(tax_old_before_cess + cess_old, 2)
        tax_new = round(tax_new_before_cess + cess_new, 2)

        # Calculate TDS, tax due, and refund (using both regimes for comparison)
        tds = round(self._to_float(data.get("tds", 0.0)), 2)
        tax_due_old = round(max(0.0, tax_old - tds), 2)
        tax_due_new = round(max(0.0, tax_new - tds), 2)
        refund_old = round(max(0.0, tds - tax_old), 2)
        refund_new = round(max(0.0, tds - tax_new), 2)

        # Let AI choose the optimal regime based on comprehensive analysis
        # (not just simple tax comparison, but considers deductions, future planning, etc.)
        ai_regime_choice = self._ai_choose_regime(
            gross_total_income, taxable_old, taxable_new, 
            tax_old, tax_new, old_deductions, new_deductions, tds
        )
        
        # Use AI choice if available, otherwise fall back to simple comparison
        if ai_regime_choice:
            chosen_regime = ai_regime_choice.get("chosen_regime", "new" if tax_new <= tax_old else "old")
            regime_reasoning = ai_regime_choice.get("reasoning", {})
        else:
            # Fallback: Simple comparison
            chosen_regime = "new" if tax_new <= tax_old else "old"
            regime_reasoning = self._get_regime_reasoning(
                gross_total_income, taxable_old, taxable_new, 
                tax_old, tax_new, chosen_regime, old_deductions, new_deductions
            )
        
        final_tax = tax_new if chosen_regime == "new" else tax_old
        tax_due = tax_due_new if chosen_regime == "new" else tax_due_old
        refund = refund_new if chosen_regime == "new" else refund_old

        return {
            "gross_total_income": round(gross_total_income, 2),
            "deductions_applied": {
                "standard_deduction": round(self.STANDARD_DEDUCTION, 2),
                "professional_tax": round(ded["professional_tax"], 2),
                "80C_and_80CCD1_applied": round(ded["80C_and_80CCD1"], 2),
                "80CCD_1B_extra_nps": round(ded["80CCD_1B_extra"], 2),
                "80D": round(ded["80D"], 2),
                "other_80CCD": round(ded["80CCD"], 2),
                "section_24": round(ded["section_24"], 2),
                "total_old_deductions": round(old_deductions, 2),
                "total_new_deductions": round(new_deductions, 2),
            },
            "taxable_income_old": round(taxable_old, 2),
            "taxable_income_new": round(taxable_new, 2),
            "tax_old": round(tax_old, 2),
            "tax_new": round(tax_new, 2),
            "chosen_regime": chosen_regime,
            "final_tax": round(final_tax, 2),
            "tds": tds,
            "tax_due": tax_due,
            "refund": refund,
            "regime_reasoning": regime_reasoning,
            "calculation_explanation": self._get_calculation_explanation(
                gross_total_income, taxable_old, taxable_new, tax_old, tax_new, 
                old_deductions, new_deductions, ded["professional_tax"], tds, tax_due, refund
            ),
            "generated_on": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _tax_old_regime(self, income: float) -> float:
        """
        OLD REGIME tax calculation (before cess) - Progressive slab system:
        - 0-2.5L: 0%
        - 2.5-5L: 5%
        - 5-10L: 20%
        - 10L+: 30%
        """
        if income <= 250000:
            return 0.0
        
        tax = 0.0
        if income > 250000:
            tax += (min(income, 500000) - 250000) * 0.05
        if income > 500000:
            tax += (min(income, 1000000) - 500000) * 0.20
        if income > 1000000:
            tax += (income - 1000000) * 0.30
        
        return round(tax, 2)

    def _tax_new_regime(self, income: float) -> float:
        """
        NEW REGIME tax calculation (before cess) - Post-2023 slabs:
        - 0-3L: 0%, 3-6L: 5%, 6-9L: 10%, 9-12L: 15%, 12-15L: 20%, 15L+: 30%
        """
        if income <= 0:
            return 0.0
        
        slabs = [
            (300000, 0.00), (600000, 0.05), (900000, 0.10),
            (1200000, 0.15), (1500000, 0.20), (float("inf"), 0.30)
        ]
        
        tax = 0.0
        prev_limit = 0.0
        
        for limit, rate in slabs:
            if income > prev_limit:
                taxable = min(income, limit) - prev_limit
                tax += taxable * rate
                if income <= limit:
                    break
            prev_limit = limit
        
        return round(tax, 2)

    def _ai_choose_regime(self, gross_income: float, taxable_old: float, taxable_new: float,
                         tax_old: float, tax_new: float, old_ded: float, new_ded: float, tds: float) -> Optional[Dict[str, Any]]:
        """
        Let AI choose the optimal tax regime based on comprehensive analysis.
        Considers not just tax amount, but deductions, future planning, investment opportunities, etc.
        Returns None if AI unavailable, triggering fallback to simple comparison.
        """
        if not self.client:
            return None
        
        try:
            prompt = f"""You are an expert Chartered Accountant helping a client choose between Old and New tax regimes for AY 2025-26.

FINANCIAL DATA:
- Gross Total Income: ₹{gross_income:,.2f}
- Old Regime Deductions: ₹{old_ded:,.2f} (includes Professional Tax, 80C, 80D, etc.)
- New Regime Deductions: ₹{new_ded:,.2f} (Standard Deduction only)
- Taxable Income (Old): ₹{taxable_old:,.2f}
- Taxable Income (New): ₹{taxable_new:,.2f}
- Tax Liability (Old): ₹{tax_old:,.2f}
- Tax Liability (New): ₹{tax_new:,.2f}
- TDS Already Paid: ₹{tds:,.2f}
- Tax Due (Old): ₹{max(0.0, tax_old - tds):,.2f}
- Tax Due (New): ₹{max(0.0, tax_new - tds):,.2f}
- Savings (Old vs New): ₹{abs(tax_old - tax_new):,.2f}

ANALYSIS REQUIRED:
1. Compare tax liabilities under both regimes
2. Consider deduction benefits (Old regime allows more deductions)
3. Consider future investment planning (Old regime incentivizes investments)
4. Consider simplicity (New regime is simpler, no investment tracking)
5. Consider income level and tax bracket impact
6. Make a professional recommendation

Return JSON with your choice and reasoning:
{{
  "chosen_regime": "old" or "new",
  "reasoning": {{
    "recommended_regime": "OLD" or "NEW",
    "reasoning": "Brief 2-3 sentence explanation of why this regime is better",
    "detailed_analysis": "Detailed explanation covering: 1) Tax comparison, 2) Deduction benefits, 3) Future planning considerations, 4) Recommendation",
    "key_factors": ["Factor 1", "Factor 2", "Factor 3"],
    "savings_amount": {abs(tax_old - tax_new):.2f},
    "considerations": ["Consideration 1", "Consideration 2"]
  }}
}}

Be professional, clear, and consider all factors - not just the tax amount, but also long-term financial planning."""
            
            response = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": "You are an expert Chartered Accountant. Analyze comprehensively and choose the best regime. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=1000
            )
            
            # Parse JSON from response
            raw = response.choices[0].message.content.strip()
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            start, end = cleaned.find("{"), cleaned.rfind("}") + 1
            
            if start != -1 and end > start:
                result = json.loads(cleaned[start:end])
                # Validate chosen_regime
                if result.get("chosen_regime") in ["old", "new"]:
                    return result
        except Exception:
            pass
        return None

    def _get_regime_reasoning(self, gross_income: float, taxable_old: float, taxable_new: float,
                             tax_old: float, tax_new: float, chosen: str, 
                             old_ded: float, new_ded: float) -> Dict[str, Any]:
        """
        Get AI-powered reasoning for regime selection.
        Falls back to rule-based reasoning if AI client unavailable.
        """
        if not self.client:
            # Fallback: Rule-based reasoning without AI
            savings = abs(tax_old - tax_new)
            return {
                "recommended_regime": chosen.upper(),
                "reasoning": f"Based on calculations, {chosen.upper()} regime results in lower tax liability. "
                           f"Tax under Old Regime: ₹{tax_old:,.2f}, Tax under New Regime: ₹{tax_new:,.2f}. "
                           f"Savings by choosing {chosen.upper()} regime: ₹{savings:,.2f}.",
                "detailed_analysis": f"Old Regime allows deductions of ₹{old_ded:,.2f} including Professional Tax, "
                                    f"while New Regime allows only ₹{new_ded:,.2f} (Standard Deduction). "
                                    f"Despite fewer deductions, New Regime's lower tax slabs result in lower tax."
            }
        
        # AI-powered reasoning using Groq API
        try:
            prompt = f"""You are a Chartered Accountant providing tax regime selection advice.

FINANCIAL SUMMARY:
- Gross Total Income: ₹{gross_income:,.2f}
- Old Regime Deductions: ₹{old_ded:,.2f} (includes Professional Tax ₹{old_ded - 50000:,.2f})
- New Regime Deductions: ₹{new_ded:,.2f} (Standard Deduction only)
- Taxable Income (Old): ₹{taxable_old:,.2f}
- Taxable Income (New): ₹{taxable_new:,.2f}
- Tax Liability (Old): ₹{tax_old:,.2f}
- Tax Liability (New): ₹{tax_new:,.2f}
- Recommended Regime: {chosen.upper()}

Provide professional CA-style reasoning in JSON format:
{{
  "recommended_regime": "{chosen.upper()}",
  "reasoning": "Brief 2-3 sentence explanation of why this regime is better",
  "detailed_analysis": "Detailed explanation covering: 1) Why deductions differ, 2) Impact of tax slabs, 3) Net savings, 4) Recommendation",
  "key_factors": ["Factor 1", "Factor 2", "Factor 3"],
  "savings_amount": {abs(tax_old - tax_new):.2f}
}}

Be professional, clear, and concise like a CA would explain to a client."""
            
            response = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": "You are a Chartered Accountant providing tax advice. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=800
            )
            
            # Parse JSON from response (handle code fences)
            raw = response.choices[0].message.content.strip()
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            start, end = cleaned.find("{"), cleaned.rfind("}") + 1
            
            if start != -1 and end > start:
                return json.loads(cleaned[start:end])
        except Exception:
            pass
        
        # Final fallback if AI parsing fails
        savings = abs(tax_old - tax_new)
        return {
            "recommended_regime": chosen.upper(),
            "reasoning": f"{chosen.upper()} regime is recommended as it results in lower tax liability of ₹{tax_new if chosen == 'new' else tax_old:,.2f} compared to ₹{tax_old if chosen == 'new' else tax_new:,.2f}.",
            "detailed_analysis": f"Old Regime allows ₹{old_ded:,.2f} in deductions (including Professional Tax), while New Regime allows only ₹{new_ded:,.2f}. However, New Regime's lower tax slabs offset the deduction advantage, resulting in ₹{savings:,.2f} savings.",
            "key_factors": ["Tax slab differences", "Deduction availability", "Net tax liability"],
            "savings_amount": round(savings, 2)
        }

    def _get_calculation_explanation(self, gross: float, taxable_old: float, taxable_new: float,
                                    tax_old: float, tax_new: float, old_ded: float, 
                                    new_ded: float, prof_tax: float, tds: float, tax_due: float, refund: float) -> str:
        """Generate CA-style calculation explanation in markdown format."""
        return f"""**Tax Calculation Breakdown (AY 2025-26):**

**1. Income Summary:**
- Gross Total Income: ₹{gross:,.2f}

**2. Deductions Applied:**

*Old Regime:*
- Standard Deduction: ₹50,000.00
- Professional Tax: ₹{prof_tax:,.2f}
- Total Deductions: ₹{old_ded:,.2f}
- Taxable Income: ₹{taxable_old:,.2f}

*New Regime:*
- Standard Deduction: ₹50,000.00
- Professional Tax: Not allowed
- Total Deductions: ₹{new_ded:,.2f}
- Taxable Income: ₹{taxable_new:,.2f}

**3. Tax Computation:**

*Old Regime Tax:*
- Tax on ₹{taxable_old:,.2f} = ₹{tax_old:,.2f} (including 4% cess)

*New Regime Tax:*
- Tax on ₹{taxable_new:,.2f} = ₹{tax_new:,.2f} (including 4% cess)

**4. Final Tax Position:**
- TDS Already Paid: ₹{tds:,.2f}
- Tax Due: ₹{tax_due:,.2f}
- Refund: ₹{refund:,.2f}

*Note: Calculations follow Income Tax Act provisions for AY 2025-26.*"""

    def generate_report(self, calc_result: Dict[str, Any], consolidated_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate formatted text report from calculation result."""
        # Extract profile from consolidated data
        consolidated = consolidated_data or {}
        name = consolidated.get("name") or "UNKNOWN"
        pan = consolidated.get("pan") or "UNKNOWN"
        ay = consolidated.get("assessment_year") or "N/A"
        income_components = consolidated.get("income_components", {}) or {}
        deductions = consolidated.get("deductions", {}) or {}
        
        # Build summary lines
        lines: List[str] = []
        lines.append("=" * 50 + " TAX SUMMARY " + "=" * 50)
        lines.append(_align_label_value("Name", name, 20))
        lines.append(_align_label_value("PAN", pan, 20))
        lines.append(_align_label_value("Assessment Year", ay, 20))
        lines.append("-" * 110)
        lines.append(_align_label_value("GROSS TOTAL INCOME", _fmt_inr(calc_result.get("gross_total_income", 0)), 20))
        lines.append(_align_label_value("TAXABLE (OLD)", _fmt_inr(calc_result.get("taxable_income_old", 0)), 20))
        lines.append(_align_label_value("TAXABLE (NEW)", _fmt_inr(calc_result.get("taxable_income_new", 0)), 20))
        lines.append("-" * 110)
        lines.append(_align_label_value("TAX (OLD)", _fmt_inr(calc_result.get("tax_old", 0)), 20))
        lines.append(_align_label_value("TAX (NEW)", _fmt_inr(calc_result.get("tax_new", 0)), 20))
        lines.append(_align_label_value("CHOSEN REGIME", (calc_result.get("chosen_regime", "N/A") or "N/A").upper(), 20))
        lines.append("-" * 110)
        lines.append(_align_label_value("TDS", _fmt_inr(calc_result.get("tds", 0)), 20))
        lines.append(_align_label_value("TAX DUE", _fmt_inr(calc_result.get("tax_due", 0)), 20))
        lines.append("=" * 110)
        lines.append("")
        
        # Income breakdown
        lines.append("INCOME BREAKDOWN")
        if income_components:
            for k, v in income_components.items():
                if k != "Total Income" and isinstance(v, (int, float)) and v > 0:
                    lines.append(f" - {k.ljust(18)} : {_fmt_inr(float(v))}")
        else:
            lines.append(" - No income components found.")
        
        lines.append("")
        
        # Deductions
        lines.append("DEDUCTIONS")
        if deductions:
            for k, v in deductions.items():
                if k != "Total Deductions" and isinstance(v, (int, float)) and v > 0:
                    lines.append(f" - {k.ljust(18)} : {_fmt_inr(float(v))}")
        else:
            lines.append(" - No deductions reported.")
        
        lines.append("=" * 110)
        lines.append("")
        
        # Add reasoning
        lines.append("REASONING:")
        if calc_result.get("regime_reasoning"):
            reasoning = calc_result["regime_reasoning"]
            if reasoning.get("reasoning"):
                lines.append(f"• {reasoning.get('reasoning')}")
            if reasoning.get("detailed_analysis"):
                lines.append(f"• {reasoning.get('detailed_analysis')}")
        
        if calc_result.get("calculation_explanation"):
            lines.append("• Calculation follows Income Tax Act provisions for AY 2025-26")
        
        summary_text = "\n".join(lines)
        
        return {
            "summary_text": summary_text,
            "summary_lines": lines,
            "summary_map": {
                "name": name,
                "pan": pan,
                "assessment_year": ay,
                "gross_total_income": calc_result.get("gross_total_income", 0.0),
                "taxable_income_old": calc_result.get("taxable_income_old", 0.0),
                "taxable_income_new": calc_result.get("taxable_income_new", 0.0),
                "tax_old": calc_result.get("tax_old", 0.0),
                "tax_new": calc_result.get("tax_new", 0.0),
                "chosen_regime": (calc_result.get("chosen_regime") or "").upper(),
                "final_tax": calc_result.get("final_tax", 0.0),
                "tds": calc_result.get("tds", 0.0),
                "tax_due": calc_result.get("tax_due", 0.0),
                "refund": calc_result.get("refund", 0.0),
                "income_components": income_components,
                "deductions": deductions,
                "generated_on": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }
