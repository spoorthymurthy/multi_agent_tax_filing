# agents/report_agent.py
import os
import json
import requests
from datetime import datetime
from fpdf import FPDF
from dotenv import load_dotenv

load_dotenv()

def ensure_unicode_font():
    font_dir = os.path.join(os.getcwd(), "assets", "fonts")
    os.makedirs(font_dir, exist_ok=True)

    font_path = os.path.join(font_dir, "NotoSans-Regular.ttf")
    if not os.path.exists(font_path):
        try:
            print("📥 Downloading NotoSans-Regular.ttf...")
            url = "https://github.com/googlefonts/noto-fonts/raw/main/hinted/ttf/NotoSans/NotoSans-Regular.ttf"
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            with open(font_path, "wb") as f:
                f.write(r.content)
        except Exception as e:
            print(f"⚠️ Failed to download NotoSans font: {e}")
            return None

    return font_path


class ReportAgent:
    def __init__(self):
        self.font_path = ensure_unicode_font()

    def clean_json(self, text):
        if isinstance(text, dict):
            return text
        if not isinstance(text, str):
            return {"summary": str(text)}
        s = text.strip()
        if s.startswith("```"):
            s = s.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(s)
        except Exception:
            pass
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(s[start:end+1])
            except Exception:
                pass
        return {"summary": s}

    def setup_font(self, pdf):
        if self.font_path:
            try:
                pdf.add_font("NotoSans", "", self.font_path, uni=True)
                pdf.set_font("NotoSans", "", 12)
                return
            except Exception as e:
                print(f"⚠️ Failed to register NotoSans font: {e}")
        try:
            pdf.set_font("Helvetica", "", 12)
        except Exception:
            pass

    def normalize_advisory(self, advisory):
        if isinstance(advisory, str):
            advisory = self.clean_json(advisory)
        if not isinstance(advisory, dict):
            return {
                "insights": [str(advisory)],
                "warnings": [],
                "actions": [],
                "summary": str(advisory),
                "optimization": [],
                "review_findings": [],
                "filing_advice": []
            }
        advisory.setdefault("insights", [])
        advisory.setdefault("warnings", [])
        advisory.setdefault("actions", [])
        advisory.setdefault("summary", advisory.get("summary", ""))
        advisory.setdefault("optimization", advisory.get("optimization", []))
        advisory.setdefault("review_findings", advisory.get("review_findings", []))
        advisory.setdefault("filing_advice", advisory.get("filing_advice", []))
        for k in ["insights", "warnings", "actions", "optimization", "review_findings", "filing_advice"]:
            v = advisory.get(k)
            if v is None:
                advisory[k] = []
            elif not isinstance(v, list):
                advisory[k] = [v]
        return advisory

    def _line_text(self, v):
        if v is None:
            return "N/A"
        if isinstance(v, (int, float)):
            try:
                if float(v).is_integer():
                    return f"{int(v):,}"
                return f"{float(v):,}"
            except Exception:
                return str(v)
        if isinstance(v, (dict, list)):
            try:
                return json.dumps(v, ensure_ascii=False)
            except Exception:
                return str(v)
        return str(v)

    def generate_final_report(self, itr_json, review, advisory):
        if not isinstance(itr_json, dict):
            raise ValueError("itr_json must be a dict")
        if isinstance(review, str):
            review = self.clean_json(review)
        if not isinstance(review, dict):
            review = {"review_findings": [str(review)]}
        review.setdefault("review_findings", [])
        if not isinstance(review["review_findings"], list):
            review["review_findings"] = [review["review_findings"]]
        advisory = self.normalize_advisory(advisory)

        # Create PDF
        pdf = FPDF()
        pdf.add_page()
        self.setup_font(pdf)

        def line(text, size=11):
            try:
                pdf.set_font("NotoSans", "", size)
            except Exception:
                try:
                    pdf.set_font("Helvetica", "", size)
                except Exception:
                    pass
            t = self._line_text(text)
            pdf.multi_cell(0, 8, t)
            pdf.ln(1)

        taxpayer = itr_json.get("taxpayer", {}) or {}
        tax_comp = itr_json.get("tax_computed", {}) or {}

        # Title
        pdf.set_font_size(16)
        pdf.multi_cell(0, 10, "AI-Generated Final Income Tax Filing Report", align="C")
        pdf.ln(5)

        # Basic Info
        line(f"Name: {self._line_text(taxpayer.get('name'))}")
        line(f"PAN: {self._line_text(taxpayer.get('pan'))}")
        line(f"Assessment Year: {self._line_text(taxpayer.get('assessment_year'))}")
        line(f"Selected Form: {self._line_text(itr_json.get('form_name'))}")
        line(f"Reason: {self._line_text(itr_json.get('reason_for_itr'))}")
        pdf.ln(3)

        # Income
        pdf.set_font_size(13)
        pdf.cell(0, 8, "Income Summary", ln=True)
        pdf.set_font_size(10)
        for k, v in (itr_json.get('income_details') or {}).items():
            line(f"{k}: ₹{self._line_text(v)}")

        pdf.ln(2)

        # Deductions
        pdf.set_font_size(13)
        pdf.cell(0, 8, "Deductions", ln=True)
        pdf.set_font_size(10)
        for k, v in (itr_json.get('deductions') or {}).items():
            line(f"{k}: ₹{self._line_text(v)}")

        # Tax Summary
        pdf.ln(2)
        pdf.set_font_size(13)
        pdf.cell(0, 8, "Tax Summary", ln=True)
        pdf.set_font_size(10)
        line(f"Taxable Income: ₹{self._line_text(itr_json.get('taxable_income'))}")
        line(f"Final Tax Payable: ₹{self._line_text(tax_comp.get('final_tax'))}")

        pdf.ln(3)

        # Review Findings
        pdf.set_font_size(13)
        pdf.cell(0, 8, "Review Findings (AI Review)", ln=True)
        pdf.set_font_size(10)
        for item in review.get('review_findings', []) or []:
            line(f"• {self._line_text(item)}")

        pdf.ln(2)

        # Advisory Insights
        pdf.set_font_size(13)
        pdf.cell(0, 8, "AI Insights", ln=True)
        pdf.set_font_size(10)
        for item in advisory.get('insights', []) or []:
            line(f"💡 {self._line_text(item)}")

        pdf.ln(2)

        # Warnings
        pdf.set_font_size(13)
        pdf.cell(0, 8, "Warnings / Risk Alerts", ln=True)
        pdf.set_font_size(10)
        for item in advisory.get('warnings', []) or []:
            line(f"⚠️ {self._line_text(item)}")

        pdf.ln(2)

        # Actions
        pdf.set_font_size(13)
        pdf.cell(0, 8, "Recommended Actions", ln=True)
        pdf.set_font_size(10)
        for item in advisory.get('actions', []) or []:
            line(f"→ {self._line_text(item)}")

        pdf.ln(3)

        # Optimization
        pdf.set_font_size(13)
        pdf.cell(0, 8, "Tax Optimization", ln=True)
        pdf.set_font_size(10)
        for item in advisory.get('optimization', []) or []:
            line(f"💡 {self._line_text(item)}")

        pdf.ln(4)

        # Footer
        pdf.set_font_size(9)
        line("Generated by AI-Powered Multi-Agent Tax Filing System (Groq API)")

        # Generate PDF in memory
        try:
            pdf_bytes = pdf.output(dest='S')
            # Convert bytearray to bytes for Streamlit compatibility
            if isinstance(pdf_bytes, bytearray):
                pdf_bytes = bytes(pdf_bytes)
        except Exception as e:
            raise Exception(f"Failed to generate PDF: {e}")

        return pdf_bytes

    def process(self, itr_json, review, advisory):
        advisory = self.clean_json(advisory) if isinstance(advisory, str) else advisory
        return self.generate_final_report(itr_json, review or {}, advisory or {})
