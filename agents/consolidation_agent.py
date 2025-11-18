import os
import json
from datetime import datetime


class ConsolidationAgent:
    """Consolidates extracted financial data from multiple documents"""

    def _normalize_data(self, data_list):
        items = []
        for item in data_list:
            if isinstance(item, dict):
                extracted = item.get("structured_data") or item
                if extracted:
                    extracted["document_type"] = item.get("document_type") or extracted.get("_doc_type")
                    items.append(extracted)
        return items

    def _filter_unique(self, data_list):
        unique = {}
        seen_ais = {}
        for block in data_list:
            pan = (str(block.get("pan", "") or "")).strip() or "UNKNOWNPAN"
            ay = (str(block.get("assessment_year", "") or "")).strip() or "UNKNOWNAY"
            doc_type = (block.get("document_type") or block.get("_doc_type") or "Unknown").lower()

            if ay != "202425" or any(x in doc_type for x in ["tis", "form 16", "form16", "form 26as", "26as", "bank", "statement"]):
                continue

            if "ais" in doc_type:
                ais_key = f"{pan}_{ay}_ais"
                if ais_key in seen_ais:
                    continue
                seen_ais[ais_key] = True
                key = ais_key
            else:
                key = f"{pan}_{ay}_{doc_type}"

            if key not in unique:
                unique[key] = block
        return list(unique.values())

    def _num(self, v):
        try:
            if isinstance(v, str):
                v = v.replace(",", "").replace("₹", "")
            return float(v)
        except:
            return 0.0

    def _combine(self, items):
        if not items:
            raise ValueError("❌ No extracted financial data found.")

        base = items[0]

        combined = {
            "name": base.get("name") or "N/A",
            "pan": base.get("pan") or "UNKNOWNPAN",
            "assessment_year": base.get("assessment_year") or "UNKNOWNAY",
            "income_components": {},
            "deductions": {},
            "tds": 0.0,
            "sources": [],
        }

        total_salary = 0.0

        seen_sources = set()
        for block in items:
            src = block.get("document_type") or block.get("_doc_type") or "Unknown"
            if src not in seen_sources:
                combined["sources"].append(src)
                seen_sources.add(src)
            
            # Collect address from AIS documents
            if not combined.get("address") and (block.get("_address") or block.get("address")):
                combined["address"] = block.get("_address") or block.get("address")

            if "income_components" in block and isinstance(block.get("income_components"), dict):
                for k, v in block.get("income_components", {}).items():
                    if k.upper() not in ["Q1", "Q2", "Q3", "Q4"]:
                        val = self._num(v)
                        combined["income_components"][k] = combined["income_components"].get(k, 0) + val
                        if "salary" in k.lower() or "gross" in k.lower():
                            total_salary += val
                for k, v in block.get("deductions", {}).items():
                    combined["deductions"][k] = combined["deductions"].get(k, 0) + self._num(v)
            else:
                doc_type = (block.get("document_type") or block.get("_doc_type") or "").lower()
                
                gross = self._num(block.get("gross_salary", 0))
                if gross > 0 and 100000 <= gross <= 50000000:
                    if "ais" in doc_type:
                        combined["income_components"]["Gross Salary"] = max(
                            combined["income_components"].get("Gross Salary", 0), gross
                        )
                        total_salary = max(total_salary, gross)
                
                basic = self._num(block.get("basic_salary", 0))
                if basic > 0 and 10000 <= basic <= 50000000:
                    combined["income_components"]["Basic Salary"] = max(
                        combined["income_components"].get("Basic Salary", 0), basic
                    )
                
                hra = self._num(block.get("hra_received", 0))
                if "payslip" in doc_type and hra > 0 and hra < 50000:
                    hra = hra * 12
                if hra > 0 and hra <= 1000000:
                    combined["income_components"]["HRA"] = max(
                        combined["income_components"].get("HRA", 0), hra
                    )
                
                interest = self._num(block.get("interest_income", 0))
                if interest > 0 and interest <= 100000:
                    if "ais" in doc_type:
                        combined["income_components"]["Interest Income"] = max(
                            combined["income_components"].get("Interest Income", 0), interest
                        )
                
                rental = self._num(block.get("rental_income", 0))
                if rental > 0 and rental <= 10000000:
                    combined["income_components"]["Rental Income"] = max(
                        combined["income_components"].get("Rental Income", 0), rental
                    )
                
                other = self._num(block.get("other_income", 0))
                if other > 0 and other <= 10000000:
                    combined["income_components"]["Other Income"] = max(
                        combined["income_components"].get("Other Income", 0), other
                    )
                
                std_ded = self._num(block.get("standard_deduction", 0))
                if std_ded > 0:
                    combined["deductions"]["Standard Deduction"] = max(combined["deductions"].get("Standard Deduction", 0), std_ded)
                
                prof_tax = self._num(block.get("professional_tax", 0))
                if "payslip" in doc_type and 0 < prof_tax < 5000:
                    prof_tax *= 12
                if prof_tax > 0:
                    combined["deductions"]["Professional Tax"] = max(combined["deductions"].get("Professional Tax", 0), prof_tax)
                
                for ded_key, block_key in [("80C", "deduction_80c"), ("80D", "deduction_80d"), ("80CCD", "deduction_80ccd")]:
                    val = self._num(block.get(block_key, 0))
                    if val > 0:
                        combined["deductions"][ded_key] = max(combined["deductions"].get(ded_key, 0), val)
                
                interest_paid = self._num(block.get("interest_paid", 0))
                if interest_paid > 0:
                    combined["deductions"]["Section 24 (House Property Interest)"] = max(
                        combined["deductions"].get("Section 24 (House Property Interest)", 0), min(interest_paid, 200000.0)
                    )
                
                if "payslip" not in doc_type:
                    total_ded = self._num(block.get("total_deductions", 0))
                    if total_ded > 0:
                        combined["deductions"]["Other Deductions"] = max(
                            combined["deductions"].get("Other Deductions", 0), total_ded
                        )

            tds_val = self._num(block.get("tds_amount", 0)) or self._num(block.get("tds_ais", 0))
            if 10000 <= tds_val <= 1000000:
                combined["tds"] = max(combined["tds"], tds_val)

        # Ensure standard deduction is always present (mandatory for salaried employees)
        if combined["deductions"].get("Standard Deduction", 0) == 0 and combined["income_components"].get("Gross Salary", 0) > 0:
            combined["deductions"]["Standard Deduction"] = 50000.0

        # Calculate Total Income from all income components
        total_income = (
            combined["income_components"].get("Gross Salary", 0) +
            combined["income_components"].get("Interest Income", 0) +
            combined["income_components"].get("Rental Income", 0) +
            combined["income_components"].get("Other Income", 0)
        )
        combined["income_components"]["Total Income"] = round(total_income, 2)
        # Calculate Total Deductions (EXCLUDE "Total Deductions" itself to avoid doubling)
        total_ded = sum(v for k, v in combined["deductions"].items() if k != "Total Deductions")
        combined["deductions"]["Total Deductions"] = round(total_ded, 2)

        return combined

    def process(self, extracted_docs=None):
        if extracted_docs is None:
            raise ValueError("❌ No extracted documents provided")
        
        data = self._normalize_data(extracted_docs)

        if not data:
            raise ValueError("❌ No valid extracted data found")

        unique = self._filter_unique(data)
        combined = self._combine(unique)

        result = {
            "consolidated": combined,
            "taxable_income": round(max(
                combined["income_components"].get("Total Income", 0) - combined["deductions"].get("Total Deductions", 0), 0
            ), 2),
        }
        return result
