# agents/document_agent.py
"""DocumentAgent: Extracts and consolidates tax filing data from financial documents with AI-first approach"""
from __future__ import annotations

import os
import io
import re
import json
import time
from typing import Optional, Dict, Any, List

import pdfplumber
import PyPDF2
import pytesseract
import pandas as pd
from PIL import Image
from dotenv import load_dotenv

try:
    from pdf2image import convert_from_path
    _HAS_PDF2IMAGE = True
except ImportError:
    _HAS_PDF2IMAGE = False
    convert_from_path = None

try:
    from groq import Groq, GroqError
    _HAS_GROQ = True
except Exception:
    Groq = None
    GroqError = Exception
    _HAS_GROQ = False

load_dotenv()

_tesseract_cmd = os.environ.get("TESSERACT_CMD") or next(
    (p for p in [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        r"C:\Tesseract-OCR\tesseract.exe"] if os.path.exists(p)), None
)
if _tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = _tesseract_cmd

PAN_RE = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b")
AY_RE = re.compile(r"(Assessment Year|A\.Y\.|AY)[:\s]*([0-9]{4}(?:-?\d{2,4})?)", re.I)
CURRENCY_RE = re.compile(r"[₹Rs\.\s,]*([0-9]{1,3}(?:[,0-9]*)(?:\.\d{1,2})?)")

DEFAULT_STRUCTURE = {
    'name': None, 'pan': None, 'assessment_year': None, 'period': {'from': None, 'to': None},
    'basic_salary': 0.0, 'hra_received': 0.0, 'da': 0.0, 'gross_salary': 0.0, 'net_salary': 0.0,
    'pay_scale': None, 'standard_deduction': 0.0, 'professional_tax': 0.0,
    'deduction_80c': 0.0, 'deduction_80d': 0.0, 'deduction_80ccd': 0.0,
    'deduction_80tta': 0.0, 'deduction_80g': 0.0, 'total_deductions': 0.0,
    'interest_income': 0.0, 'rental_income': 0.0, 'other_income': 0.0,
    'tds_amount': 0.0, 'tds_ais': 0.0, 'taxable_income': 0.0,
    'interest_paid': 0.0, 'rent_paid': 0.0,
}


def _to_number(x: Any) -> float:
    """Convert text/number to float. Returns 0.0 on failure."""
    try:
        if x is None:
            return 0.0
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).replace('(', '-').replace(')', '')
        s = re.sub(r"[^0-9.\-]", "", s)
        if s in ('', '-', '--'):
            return 0.0
        if s.count('.') > 1:
            parts = s.split('.')
            s = parts[0] + '.' + ''.join(parts[1:])
        return float(s)
    except Exception:
        return 0.0


class DocumentAgent:
    """
    DocumentAgent: Extracts structured data from financial documents.
    
    EXTRACTION STRATEGY:
    1. PRIMARY: AI-based extraction (Groq LLM) - attempts first for accurate extraction
    2. FALLBACK: Manual regex patterns - used when AI fails or returns incomplete data
    
    The fallback mechanism ensures robust extraction even when:
    - AI API is unavailable/unconfigured
    - AI returns invalid/incomplete JSON
    - Document format is non-standard
    """

    def __init__(self, groq_api_key: Optional[str] = None):
        """Initialize DocumentAgent with Groq client for AI extraction."""
        # Initialize Groq client for AI-powered extraction (falls back to regex if unavailable)
        api_key = groq_api_key or os.getenv("GROQ_API_KEY")
        self.client = None
        if _HAS_GROQ and api_key:
            try:
                self.client = Groq(api_key=api_key)
            except TypeError as e:
                if "proxies" in str(e):
                    import httpx
                    self.client = Groq(api_key=api_key, http_client=httpx.Client())
            except Exception:
                self.client = None

    def detect_format(self, file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        return {".pdf": "pdf", ".jpg": "image", ".jpeg": "image", ".png": "image",
                ".tiff": "image", ".csv": "csv", ".xls": "excel", ".xlsx": "excel"}.get(ext, "unknown")

    def unlock_pdf(self, pdf_path: str, password: Optional[str] = None):
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(open(pdf_path, "rb").read()))
            if getattr(reader, "is_encrypted", False):
                if not password:
                    raise ValueError("PDF is encrypted — password required.")
                if reader.decrypt(password) == 0:
                    raise ValueError("Incorrect PDF password.")
            return reader
        except Exception as e:
            raise Exception(f"PDF unlock failed: {e}")

    def extract_text_from_pdf(self, pdf_path: str, password: Optional[str] = None) -> str:
        """
        Extract text from PDF using multiple methods:
        1. PyPDF2 text extraction (primary)
        2. pdfplumber fallback (better for tables)
        3. OCR fallback if text extraction yields < 200 chars
        """
        try:
            reader = self.unlock_pdf(pdf_path, password)
            pages_text = []
            for i, page in enumerate(reader.pages):
                try:
                    t = page.extract_text()
                    if t:
                        pages_text.append(t)
                        continue
                except Exception:
                    pass
                # Fallback to pdfplumber for better table extraction
                try:
                    with pdfplumber.open(pdf_path) as pp:
                        if i < len(pp.pages):
                            t2 = pp.pages[i].extract_text() or ""
                            if t2:
                                pages_text.append(t2)
                except Exception:
                    continue
            text = "\n".join(pages_text).strip()
            # If extracted text is too short, try OCR
            if len(text) < 200:
                try:
                    ocr_text = self.ocr_extract(pdf_path)
                    if len(ocr_text) > len(text):
                        return ocr_text
                except Exception:
                    if text:
                        return text
                    raise
            return text
        except Exception:
            # Final fallback: OCR
            try:
                return self.ocr_extract(pdf_path)
            except Exception as ocr_err:
                raise Exception(f"PDF extraction failed: {ocr_err}")

    def ocr_extract(self, file_path: str) -> str:
        imgs = []
        try:
            if file_path.lower().endswith(".pdf"):
                if not _HAS_PDF2IMAGE:
                    raise Exception("pdf2image not installed. Install with: pip install pdf2image")
                poppler_path = os.environ.get("POPPLER_PATH") or next(
                    (p for p in [r"C:\poppler\Library\bin", r"C:\Program Files\poppler\Library\bin"]
                     if os.path.exists(os.path.join(p, "pdftoppm.exe"))), None
                )
                imgs = convert_from_path(file_path, poppler_path=poppler_path) if poppler_path else convert_from_path(file_path)
            else:
                imgs = [Image.open(file_path)]
            texts = []
            for img in imgs:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                texts.append(pytesseract.image_to_string(img, lang="eng"))
            return "\n".join(texts)
        except Exception as e:
            raise Exception(f"OCR failed: {e}")

    def extract_from_csv_or_excel(self, file_path: str) -> str:
        try:
            df = pd.read_csv(file_path) if file_path.lower().endswith(".csv") else pd.read_excel(file_path)
            return df.to_csv(index=False)
        except Exception as e:
            raise Exception(f"Tabular extraction failed: {e}")

    def safe_groq_call(self, prompt: str, model: str = "llama-3.3-70b-versatile", 
                      temperature: float = 0.1, max_tokens: int = 1000) -> str:
        """
        AI extraction call with retry logic for rate limits.
        Handles Groq API rate limiting with exponential backoff.
        """
        if not self.client:
            raise Exception("GROQ client not configured")
        for attempt in range(3):
            try:
                resp = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You are a precise numeric extractor for Indian financial documents. Return strict JSON when asked."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return resp.choices[0].message.content
            except GroqError as e:
                # Handle rate limiting with exponential backoff
                if "rate" in str(e).lower():
                    time.sleep(3 * (attempt + 1))
                    continue
                raise
            except Exception:
                time.sleep(3)
        raise Exception("GROQ API failed after retries")

    def identify_document_type(self, text: str) -> str:
        if not text or not text.strip():
            return "Other"
        t = text.lower()
        if "annual information statement" in t or ("ais" in t and "annual" in t):
            return "AIS"
        if "taxpayer information summary" in t or ("tis" in t and "taxpayer" in t):
            return "TIS"
        if any(k in t for k in ["payslip", "pay slip", "earnings", "net pay"]) and "ais" not in t and "tis" not in t:
            return "Payslip"
        if any(k in t for k in ["loan certificate", "interest certificate", "loan account", "roi", "emi", "interest component"]):
            return "Loan Certificate"
        return "Other"

    def _ai_extract_payslip(self, file_path: str, text: str) -> Optional[Dict[str, Any]]:
        """
        PRIMARY: AI extraction. Returns None if fails, triggering regex fallback.
        Sets '_extraction_method' to 'ai' on success.
        """
        if not self.client:
            return None
        try:
            prompt = f"""Extract payslip data from the text below. Return ONLY valid JSON with exact values as they appear in the payslip.

{{
  "name": "string or null",
  "pan": "string or null",
  "assessment_year": "string or null",
  "basic_salary": number,
  "hra_received": number,
  "da": number,
  "gross_salary": number,
  "net_salary": number,
  "professional_tax": number,
  "total_deductions": number,
  "pay_scale": "string or null",
  "period": {{"from": "string or null", "to": "string or null"}}
}}

CRITICAL INSTRUCTIONS:
1. All values are MONTHLY (payslip is for one month only)
2. Extract EXACT values as shown - do NOT estimate, calculate, or validate
3. Basic Salary: Search for "Basic" or "Basic Salary" label in the earnings section. This is typically the LARGEST component in earnings (often 80000-120000 range). Extract the number immediately next to the label.
4. HRA (House Rent Allowance): Search for "HRA" or "House Rent Allowance" label. HRA is typically smaller than Basic and DA (often 5000-15000 range). Extract the number immediately next to the label.
5. DA (Dearness Allowance): Search for "DA" or "Dearness Allowance" label. DA is often 40000-60000 range. If you see a format like "HRA49100" where HRA and a number are concatenated, that number (49100) is DA, NOT HRA.
6. Pay Scale: Look for "Pay Scale" label and extract the range format (e.g., "79800-211500")
7. Professional Tax: Search for "Professional Tax" or "PT" label. Usually 200-300 monthly.
8. TDS/Income Tax: ALWAYS use 0 - payslips typically do NOT show TDS amounts.
9. IMPORTANT: Basic Salary should be LARGER than DA and HRA. If Basic appears smaller than DA, you're likely extracting the wrong value.
10. Do NOT swap values. Do NOT recalculate. Extract exactly as shown in the document.

Payslip text:
{text[:15000]}

Extract the exact values as they appear in the payslip text. Look carefully for each label and its corresponding number."""
            response = self.safe_groq_call(prompt, model="llama-3.3-70b-versatile", temperature=0.1, max_tokens=1000)
            cleaned = response.replace("```json", "").replace("```", "").strip()
            start, end = cleaned.find("{"), cleaned.rfind("}") + 1
            if start != -1 and end > start:
                parsed = json.loads(cleaned[start:end])
                result = DEFAULT_STRUCTURE.copy()
                for key in result.keys():
                    if key in parsed:
                        val = parsed[key]
                        if isinstance(val, (int, float)):
                            result[key] = float(val)
                        elif isinstance(val, dict) and key == "period":
                            result[key] = val
                        elif val:
                            result[key] = str(val)
                result['tds_amount'] = 0
                result['_extraction_method'] = 'ai'  # Mark as AI extraction
                return result
        except Exception as e:
            # LLM failed - will use regex fallback
            pass
        return None

    def _regex_extract_payslip(self, text: str) -> Dict[str, Any]:
        """
        FALLBACK: Regex extraction when AI fails.
        Extracts: Name, PAN, Period, Basic (80000-150000), HRA, DA, Pay Scale, Professional Tax, Gross/Net Salary
        Marks '_extraction_method' as 'regex' and '_llm_failed' as True.
        """
        s = DEFAULT_STRUCTURE.copy()
        s['_extraction_method'] = 'regex'
        s['_llm_failed'] = True
        s['_extraction_note'] = 'LLM extraction failed - using regex fallback'
        
        def extract_value(pattern, min_val=0, max_val=float('inf')):
            m = re.search(pattern, text, re.I)
            if m:
                val = _to_number(m.group(1))
                if min_val <= val <= max_val:
                    return val
            return 0
        
        m = re.search(r"Sri\s*/\s*Smt\s*[:\-\s]*([A-Za-z .,'\-\(\)0-9]{2,50})", text, re.I)
        if m:
            s['name'] = re.sub(r'\s+', ' ', m.group(1).strip())
        m = PAN_RE.search(text)
        if m:
            s['pan'] = m.group(1)
        m = re.search(r"Pay\s*Slip\s*(?:For\s*The\s*Month\s*Of|for\s*the\s*month\s*of)[:\-\s]*([A-Za-z]+)\s+(\d{4})", text, re.I)
        if m:
            period_str = f"{m.group(1).strip()} {m.group(2)}"
            s['period']['from'] = period_str
            year = int(m.group(2))
            if year >= 2020:
                fy = year - 1 if any(m in period_str.lower() for m in ['jan', 'feb', 'mar']) else year
                s['assessment_year'] = f"{fy}{str(fy+1)[-2:]}"
        pay_scale_match = re.search(r"Pay\s*Scale\s*[:\-\s]*([0-9]{4,6}[\s\-]+[0-9]{4,6})", text, re.I)
        if pay_scale_match:
            s['pay_scale'] = re.sub(r'\s+', '', pay_scale_match.group(1).replace(' ', '-'))
        
        s['gross_salary'] = extract_value(r"Gross\s*Salary[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)", 10000)
        # Basic Salary - try multiple patterns (Basic : 98200, Basic Salary: 98200, etc.)
        s['basic_salary'] = extract_value(r"Basic\s*[:\-]+\s*([0-9,]+(?:\.\d{1,2})?)", 50000, 150000)
        if s['basic_salary'] == 0:
            s['basic_salary'] = extract_value(r"Basic\s+Salary[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)", 50000, 150000)
        if s['basic_salary'] == 0:
            s['basic_salary'] = extract_value(r"\bBasic\s*[:\-]+\s*[₹Rs\.\s,]*([0-9,]{5,}(?:\.\d{1,2})?)", 50000, 150000)
        # Handle "HRA49100" format - if found, that number is DA (NOT HRA)
        hra_concat = re.search(r'HRA([0-9]{4,})', text, re.I)
        concat_val = _to_number(hra_concat.group(1)) if hra_concat else None
        # HRA - try patterns like "HRA 7856" or "HRA: 7856" (typically 1000-30000)
        s['hra_received'] = extract_value(r"\bHRA\s+([0-9,]+(?:\.\d{1,2})?)", 1000, 30000)
        if s['hra_received'] == 0:
            s['hra_received'] = extract_value(r"HRA[:\-\s]+[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)", 1000, 30000)
        # If HRA was extracted as concatenated value from "HRA49100", reset and find actual HRA
        if hra_concat and concat_val and abs(s['hra_received'] - concat_val) < 100:
            s['hra_received'] = 0
            pos = hra_concat.start()
            window = text[max(0, pos-500):min(len(text), pos+500)]
            for match in re.findall(r'\bHRA\s+([0-9,]+)', window, re.I):
                val = _to_number(match)
                if 1000 <= val <= 30000 and val != concat_val:
                    s['hra_received'] = val
                    break
        # DA - try patterns like "DA 49100" or "DA: 49100" (typically 10000-100000)
        s['da'] = extract_value(r"\bDA\s+([0-9,]+(?:\.\d{1,2})?)", 10000, 100000)
        if s['da'] == 0:
            s['da'] = extract_value(r"\bDA[:\-\s]+[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)", 10000, 100000)
        # Handle "HRA49100" format - the number after HRA is DA
        if hra_concat and concat_val and (s['da'] == 0 or abs(s['da'] - concat_val) > 100):
            if 10000 <= concat_val <= 100000:
                s['da'] = concat_val
        s['net_salary'] = extract_value(r"Net\s*Salary[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)", 10000, 500000)
        s['professional_tax'] = extract_value(r"Professional\s*Tax[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)", 100, 500)
        if s['professional_tax'] == 0:
            s['professional_tax'] = extract_value(r"\bPT[:\-\s]+[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)", 100, 500)
        s['tds_amount'] = 0
        s['total_deductions'] = extract_value(r"Total\s*Deductions[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)", 1)
        
        return s

    def _extract_ais(self, text: str) -> Dict[str, Any]:
        """Extract AIS/TIS data using regex patterns"""
        s = DEFAULT_STRUCTURE.copy()
        m = PAN_RE.search(text)
        if m:
            s['pan'] = m.group(1)
        name_match = re.search(r"([A-Z]{5}\d{4}[A-Z])\s+XXXX\s+XXXX\s+[0-9]+\s+([A-Z][A-Za-z\s]{2,30}?)(?:\s+(?:Date\s+of\s+Birth|Mobile\s+Number|E-mail|Address|Assessment)|$|\n)", text, re.I)
        if name_match:
            potential_name = name_match.group(2).strip()
            if not re.match(r'^(XXXX|Active|Status|Assessee|Name|Number|Date|Mobile|E-mail)', potential_name, re.I):
                if len(potential_name.split()) >= 2:
                    s['name'] = potential_name
        m = AY_RE.search(text)
        if m:
            s['assessment_year'] = m.group(2).replace('-', '')
        gross_match = re.search(r"GROSS\s*SALARY\s*U/S\s*17\(1\)", text, re.I)
        if gross_match:
            after_header = text[gross_match.end():gross_match.end()+300]
            num_match = re.search(r"([0-9]{1,2}(?:[,0-9]{2,}){2,}|[0-9]{6,})(?:\.\d{1,2})?", after_header)
            if num_match:
                val = _to_number(num_match.group(1))
                if 100000 <= val <= 10000000:
                    s['gross_salary'] = val
        if s['gross_salary'] == 0:
            tds_ann_match = re.search(r"TDS-Ann\.II-SAL[^\d]{0,500}?[0-9]+([0-9]{1,2}(?:[,0-9]{2,}){2,}|[0-9]{6,})(?:\.\d{1,2})?", text, re.I)
            if tds_ann_match:
                val = _to_number(tds_ann_match.group(1))
                if 100000 <= val <= 10000000:
                    s['gross_salary'] = val
        # TDS extraction - try multiple patterns
        if s['tds_ais'] == 0:
            for pattern in [
                r"Total\s*Tax\s*Deducted[#\s]+[^\n]{0,100}?([0-9,]{5,}(?:\.\d{1,2})?)",
                r"Total\s*TDS\s*Deposited[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
                r"TDS\s*Amount[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
                r"Tax\s*Deducted[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
                r"TDS[:\-\s]*[₹Rs\.\s,]*([0-9,]{5,}(?:\.\d{1,2})?)"
            ]:
                m = re.search(pattern, text, re.I)
                if m:
                    val = _to_number(m.group(1))
                    if 10000 <= val <= 1000000:
                        s['tds_ais'] = s['tds_amount'] = val
                        break
        # If still not found, try to extract from quarterly TDS entries
        if s['tds_ais'] == 0:
            quarterly_tds = []
            quarterly_matches = re.finditer(r"Q\d+\([^)]+\)\s+([0-9]{2}[/-][0-9]{2}[/-][0-9]{4})\s+([0-9,]+(?:\.\d{1,2})?)\s+([0-9,]+(?:\.\d{1,2})?)\s+([0-9,]+(?:\.\d{1,2})?)", text, re.I)
            for match in quarterly_matches:
                tds_deposited = _to_number(match.group(4))
                tds_deducted = _to_number(match.group(3))
                tds = tds_deposited if tds_deposited > 0 else tds_deducted
                if 1000 <= tds <= 50000:
                    quarterly_tds.append(tds)
            if quarterly_tds:
                total_tds = sum(quarterly_tds)
                if 10000 <= total_tds <= 1000000:
                    s['tds_ais'] = s['tds_amount'] = total_tds
        interest_entries = []
        for row in re.finditer(r"([0-9]{2}[/-][0-9]{2}[/-][0-9]{4})\s+([0-9]+)\s+Saving[^\d]{0,20}?([0-9,]+(?:\.\d{1,2})?)", text, re.I):
            interest_amt = _to_number(row.group(3))
            if 50 <= interest_amt <= 50000:
                interest_entries.append({"amount": interest_amt})
        if not interest_entries:
            for match in re.finditer(r"SFT-016\(SB\)[^\n]{0,300}?([A-Z\s]+?)\s*\([A-Z0-9.]+\)\s+[0-9]+\s+([0-9,]+(?:\.\d{1,2})?)", text, re.I):
                interest_amt = _to_number(match.group(2))
                if 50 <= interest_amt <= 50000:
                    interest_entries.append({"amount": interest_amt})
        if interest_entries:
            s['interest_income'] = sum([entry['amount'] for entry in interest_entries])
        return s

    def _extract_loan_certificate(self, text: str) -> Dict[str, Any]:
        """Extract loan certificate data"""
        s = DEFAULT_STRUCTURE.copy()
        m = re.search(r"Name of Borrower[:\-\s]*([A-Za-z .,'\-\(\)0-9]{3,100})", text, re.I)
        if m:
            s['name'] = m.group(1).strip()
        for pattern in [r"INTEREST\s+COMPONENT[^\d]{0,50}?Rs\.\s*([0-9,]+(?:\.\d{1,2})?)", r"Interest\s+Paid[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)", r"Interest\s+Component[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)"]:
            m2 = re.search(pattern, text, re.I)
            if m2:
                val = _to_number(m2.group(1))
                if val > 0:
                    s['interest_paid'] = val
                    break
        return s

    def _extract_rent_receipt(self, text: str) -> Dict[str, Any]:
        """Extract rent receipt data"""
        s = DEFAULT_STRUCTURE.copy()
        m = re.search(r"Rent[:\-\s]*[₹Rs\.\s]*([0-9,]+(?:\.\d{1,2})?)", text, re.I)
        if m:
            s['rent_paid'] = _to_number(m.group(1))
        return s

    def _filter_essential_fields(self, structured: Dict[str, Any], doc_type: str) -> Dict[str, Any]:
        """Filter to essential tax filing fields, removing 0/None/empty values"""
        essential_fields_map = {
            "Payslip": {'name', 'pan', 'assessment_year', 'period', 'basic_salary', 'hra_received', 'da', 'gross_salary', 'net_salary', 'pay_scale', 'professional_tax', 'total_deductions', 'tds_amount', '_doc_type'},
            "AIS": {'name', 'pan', 'assessment_year', 'gross_salary', 'interest_income', 'rental_income', 'other_income', 'tds_ais', 'tds_amount', '_doc_type'},
            "TIS": {'name', 'pan', 'assessment_year', 'gross_salary', 'interest_income', 'rental_income', 'other_income', 'tds_ais', 'tds_amount', '_doc_type'},
            "Loan Certificate": {'interest_paid', 'period', '_doc_type'},
            "Rent Receipt": {'rent_paid', 'period', '_doc_type'},
        }
        essential_fields = essential_fields_map.get(doc_type, {'name', 'pan', 'assessment_year', 'period', 'gross_salary', 'basic_salary', 'hra_received', 'da', 'net_salary', 'pay_scale', 'interest_income', 'rental_income', 'other_income', 'standard_deduction', 'professional_tax', 'total_deductions', 'deduction_80c', 'deduction_80d', 'deduction_80ccd', 'deduction_80tta', 'deduction_80g', 'tds_amount', 'tds_ais', 'rent_paid', 'interest_paid', 'taxable_income', '_doc_type'})
        filtered = {}
        for key in essential_fields:
            if key not in structured:
                continue
            val = structured[key]
            if val is None or (isinstance(val, str) and not val.strip()):
                continue
            if isinstance(val, (int, float)) and val == 0 and not key.startswith('_'):
                continue
            if isinstance(val, dict) and not any(v for v in val.values() if v is not None and v != 0 and v != ""):
                continue
            filtered[key] = val
        return filtered

    def process(self, file_path: str, password: Optional[str] = None, debug: bool = False) -> Dict[str, Any]:
        """
        Main processing pipeline:
        1. Extract text from file (PDF/Image/CSV/Excel)
        2. Identify document type (Payslip/AIS/TIS/etc.)
        3. AI extraction (primary) -> Regex fallback if AI fails
        4. Filter and normalize extracted data
        5. Return structured data
        """
        # Step 1: Extract text based on file format
        fmt = self.detect_format(file_path)
        if fmt == "pdf":
            text = self.extract_text_from_pdf(file_path, password)
        elif fmt == "image":
            text = self.ocr_extract(file_path)
        elif fmt in ("csv", "excel"):
            text = self.extract_from_csv_or_excel(file_path)
        else:
            raise ValueError(f"Unsupported file: {file_path}")

        # Step 2: Identify document type
        doc_type = self.identify_document_type(text)

        # Step 3: Extract structured data (AI-first with regex fallback)
        if doc_type == "Payslip":
            structured = self._ai_extract_payslip(file_path, text)
            # FALLBACK: Use regex if AI fails or returns incomplete data
            if not structured or structured.get('basic_salary', 0) == 0:
                structured = self._regex_extract_payslip(text)
                # Ensure fallback is marked even if AI returned partial data
                if not structured.get('_extraction_method'):
                    structured['_extraction_method'] = 'regex'
                    structured['_llm_failed'] = True
                    structured['_extraction_note'] = 'LLM extraction failed or incomplete - using regex fallback'
        elif doc_type in ("AIS", "TIS"):
            structured = self._extract_ais(text)
        elif doc_type == "Loan Certificate":
            structured = self._extract_loan_certificate(text)
        elif doc_type == "Rent Receipt":
            structured = self._extract_rent_receipt(text)
        else:
            # Generic extraction for unknown types
            structured = DEFAULT_STRUCTURE.copy()
            m = PAN_RE.search(text)
            if m:
                structured['pan'] = m.group(1)
            cand = [_to_number(mm.group(1)) for mm in CURRENCY_RE.finditer(text)]
            if cand:
                structured['gross_salary'] = max(cand)

        structured['_doc_type'] = doc_type
        
        # Payslips don't show TDS (annualized from Form 16/AIS)
        if doc_type == "Payslip":
            structured['tds_amount'] = 0
        
        # Step 4: Normalize numeric fields
        for k in ['gross_salary', 'total_deductions', 'taxable_income', 'tds_amount', 'interest_paid', 'rent_paid']:
            structured[k] = round(_to_number(structured.get(k, 0.0)), 2)
        
        # Step 5: Filter to essential fields only
        structured = self._filter_essential_fields(structured, doc_type)
        
        result = {
            "document_type": doc_type,
            "structured_data": structured,
        }
        
        if debug:
            result['raw_text'] = text

        return result

    # ============================================================
    # CONSOLIDATION METHODS
    # ============================================================
    
    def _normalize_data(self, data_list):
        """Extract structured_data from document list, handling both nested and flat structures."""
        return [item.get("structured_data") or item for item in data_list if isinstance(item, dict) and (item.get("structured_data") or item)]

    def _filter_unique(self, data_list):
        """
        Filter duplicates and keep only relevant AY 2024-25 records.
        Handles AIS deduplication (only one AIS per PAN+AY).
        """
        unique, seen_ais = {}, {}
        for block in data_list:
            pan = str(block.get("pan", "") or "").strip() or "UNKNOWNPAN"
            ay = str(block.get("assessment_year", "") or "").strip() or "UNKNOWNAY"
            doc_type = (block.get("document_type") or block.get("_doc_type") or "Unknown").lower()
            
            # Filter: Only AY 2024-25, exclude certain document types
            if ay != "202425" or any(x in doc_type for x in ["tis", "form 16", "form16", "form 26as", "26as", "bank", "statement"]):
                continue
            
            # AIS deduplication: Only one AIS per PAN+AY
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

    def _extract_value(self, block: Dict, keys: List[str], default: float = 0.0) -> float:
        """Extract value from block using multiple possible keys."""
        for key in keys:
            val = _to_number(block.get(key, 0))
            if val > 0:
                return val
        return default

    def _ai_consolidate(self, items: List[Dict]) -> Dict[str, Any]:
        """
        Use AI to intelligently consolidate and validate financial data.
        Annualizes monthly payslip values, validates ranges, merges duplicates.
        Returns None if AI unavailable or insufficient items.
        Marks '_consolidation_method' as 'ai' on success.
        """
        if not self.client or len(items) < 2:
            return None
        
        try:
            prompt = f"""You are a tax consolidation expert. Analyze and consolidate financial data from multiple documents.

DOCUMENTS:
{json.dumps(items, indent=2, default=str)}

Tasks:
1. Identify and merge duplicate entries
2. Annualize monthly values (payslips) - multiply by 12
3. Validate ranges (salary: 1L-5Cr, interest: 0-1L, etc.)
4. Ensure Professional Tax is at least ₹2400/year (₹200/month * 12)
5. Prefer AIS values for income, Form16 for TDS
6. Return consolidated JSON with income_components, deductions, tds

Return JSON:
{{
  "income_components": {{"Gross Salary": 0, "Interest Income": 0, ...}},
  "deductions": {{"Professional Tax": 2400, "Standard Deduction": 50000, ...}},
  "tds": 0,
  "validation_notes": ["..."]
}}"""
            
            response = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": "You are a tax consolidation expert. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=1500
            )
            
            raw = response.choices[0].message.content.strip()
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            start, end = cleaned.find("{"), cleaned.rfind("}") + 1
            
            if start != -1 and end > start:
                result = json.loads(cleaned[start:end])
                result['_consolidation_method'] = 'ai'
                return result
        except Exception as e:
            # LLM failed - will use rule-based fallback
            pass
        return None

    def _rule_based_combine(self, items: List[Dict]) -> Dict[str, Any]:
        """
        Rule-based fallback consolidation when AI unavailable.
        Annualizes monthly payslip values, combines income/deductions, handles TDS.
        Marks '_consolidation_method' as 'rule_based' and '_llm_failed' as True.
        """
        base = items[0]
        combined = {
            "name": base.get("name") or "N/A",
            "pan": base.get("pan") or "UNKNOWNPAN",
            "assessment_year": base.get("assessment_year") or "UNKNOWNAY",
            "income_components": {},
            "deductions": {},
            "tds": 0.0,
            "sources": [],
            "_consolidation_method": "rule_based",
            "_llm_failed": True,
            "_consolidation_note": "LLM consolidation failed - using rule-based fallback"
        }

        income_map = [
            (["gross_salary", "gross"], "Gross Salary", 100000, 50000000),
            (["basic_salary", "basic"], "Basic Salary", 10000, 50000000),
            (["hra_received", "hra"], "HRA", 0, 1000000),
            (["interest_income", "interest"], "Interest Income", 0, 100000),
            (["rental_income", "rental"], "Rental Income", 0, 10000000),
            (["other_income"], "Other Income", 0, 10000000),
        ]

        ded_map = [
            (["standard_deduction"], "Standard Deduction", 0, None),
            (["professional_tax", "prof_tax"], "Professional Tax", 0, None),
            (["deduction_80c", "80c"], "80C", 0, None),
            (["deduction_80d", "80d"], "80D", 0, None),
            (["deduction_80ccd", "80ccd"], "80CCD", 0, None),
            (["deduction_80tta", "80tta"], "80TTA", 0, None),
            (["deduction_80g", "80g"], "80G", 0, None),
            (["interest_paid"], "Section 24 (House Property Interest)", 0, 200000),
        ]

        seen_sources = set()
        for block in items:
            doc_type = (block.get("document_type") or block.get("_doc_type") or "").lower()
            is_payslip = "payslip" in doc_type
            
            if doc_type not in seen_sources:
                combined["sources"].append(doc_type)
                seen_sources.add(doc_type)

            if "income_components" in block and isinstance(block.get("income_components"), dict):
                for k, v in block.get("income_components", {}).items():
                    if k.upper() not in ["Q1", "Q2", "Q3", "Q4"]:
                        val = _to_number(v)
                        if val > 0:
                            combined["income_components"][k] = combined["income_components"].get(k, 0) + val
                
                for k, v in block.get("deductions", {}).items():
                    val = _to_number(v)
                    if val > 0:
                        combined["deductions"][k] = max(combined["deductions"].get(k, 0), val)
            else:
                for keys, label, min_val, max_val in income_map:
                    val = self._extract_value(block, keys)
                    if val > 0:
                        if is_payslip and label in ["HRA"] and 0 < val < 50000:
                            val *= 12
                        if min_val <= val <= (max_val or float('inf')):
                            combined["income_components"][label] = max(
                                combined["income_components"].get(label, 0), val
                            )

                for keys, label, min_val, max_val in ded_map:
                    val = self._extract_value(block, keys)
                    if val > 0:
                        if is_payslip and label == "Professional Tax" and 0 < val < 5000:
                            val = val * 12 if val < 10000 else val
                        if max_val:
                            val = min(val, max_val)
                        combined["deductions"][label] = max(combined["deductions"].get(label, 0), val)

            nps_val = max(self._extract_value(block, ["nps", "nps_employee", "deduction_nps", "deduction_80ccd"]), 0)
            if nps_val > 0 and is_payslip and nps_val < 20000:
                nps_val *= 12
            if nps_val > 100:
                combined["deductions"]["NPS"] = combined["deductions"].get("NPS", 0) + nps_val

            tds_val = max(
                self._extract_value(block, ["tds_ais"]),
                self._extract_value(block, ["tds_amount", "tds"])
            )
            if 1000 <= tds_val <= 1000000:
                combined["tds"] = max(combined["tds"], tds_val)

        return combined

    def _combine(self, items: List[Dict]) -> Dict[str, Any]:
        """
        Combine financial data from multiple documents.
        Tries AI consolidation first, falls back to rule-based if AI unavailable.
        Applies defaults (Professional Tax min ₹2400, Standard Deduction ₹50000).
        """
        if not items:
            raise ValueError("❌ No extracted financial data found.")

        # Try AI consolidation first
        ai_result = self._ai_consolidate(items)
        if ai_result:
            base = items[0]
            combined = {
                "name": base.get("name") or "N/A",
                "pan": base.get("pan") or "UNKNOWNPAN",
                "assessment_year": base.get("assessment_year") or "UNKNOWNAY",
                "income_components": ai_result.get("income_components", {}),
                "deductions": ai_result.get("deductions", {}),
                "tds": _to_number(ai_result.get("tds", 0)),
                "sources": list(set((item.get("document_type") or item.get("_doc_type") or "Unknown") for item in items)),
                "_consolidation_method": ai_result.get("_consolidation_method", "ai")
            }
        else:
            # Fallback to rule-based consolidation (LLM failed)
            combined = self._rule_based_combine(items)

        # Apply defaults and validations
        prof_tax = combined["deductions"].get("Professional Tax", 0)
        if prof_tax < 2400:
            combined["deductions"]["Professional Tax"] = 2400.0  # Minimum annual Professional Tax

        if combined["deductions"].get("Standard Deduction", 0) == 0 and combined["income_components"].get("Gross Salary", 0) > 0:
            combined["deductions"]["Standard Deduction"] = 50000.0  # Default Standard Deduction

        # Calculate totals
        combined["income_components"]["Total Income"] = round(sum(
            v for k, v in combined["income_components"].items() if k != "Total Income"
        ), 2)
        
        combined["deductions"]["Total Deductions"] = round(sum(
            v for k, v in combined["deductions"].items() if k != "Total Deductions"
        ), 2)

        return combined

    def consolidate(self, extracted_docs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Consolidate extracted documents into unified structure.
        Pipeline: Normalize -> Filter duplicates -> Combine -> Calculate taxable income.
        """
        if not extracted_docs:
            raise ValueError("❌ No extracted documents provided")

        # Normalize data structure
        data = self._normalize_data(extracted_docs)
        if not data:
            raise ValueError("❌ No valid extracted data found")

        # Filter duplicates and irrelevant documents
        unique = self._filter_unique(data)
        
        # Combine financial data
        combined = self._combine(unique)

        # Calculate taxable income
        return {
            "consolidated": combined,
            "taxable_income": round(max(
                combined["income_components"].get("Total Income", 0) - combined["deductions"].get("Total Deductions", 0),
                0
            ), 2),
        }
