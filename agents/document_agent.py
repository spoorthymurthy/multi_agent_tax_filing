# agents/document_agent.py
"""DocumentAgent: Extracts tax filing data from financial documents"""
from __future__ import annotations

import os
import io
import re
import json
import time
from typing import Optional, Dict, Any, List
from datetime import datetime

# External libs (assumed installed)
import pdfplumber
import PyPDF2
import pytesseract
import pandas as pd
from PIL import Image
from dotenv import load_dotenv

# Optional pdf2image for OCR (graceful fallback if not installed)
try:
    from pdf2image import convert_from_path
    _HAS_PDF2IMAGE = True
except ImportError:
    _HAS_PDF2IMAGE = False
    convert_from_path = None

# Optional Groq client
try:
    from groq import Groq, GroqError  # type: ignore
    _HAS_GROQ = True
except Exception:
    Groq = None  # type: ignore
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

# Regex patterns
PAN_RE = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b")
AY_RE = re.compile(r"(Assessment Year|A\.Y\.|AY)[:\s]*([0-9]{4}(?:-?\d{2,4})?)", re.I)
CURRENCY_RE = re.compile(r"[₹Rs\.\s,]*([0-9]{1,3}(?:[,0-9]*)(?:\.\d{1,2})?)")

# Essential fields for tax filing only
DEFAULT_STRUCTURE = {
    # Basic Info
    'name': None,
    'pan': None,
    'assessment_year': None,
    'period': {'from': None, 'to': None},
    # Salary Components (for tax calculation)
    'basic_salary': 0.0,
    'hra_received': 0.0,
    'da': 0.0,  # Dearness Allowance
    'gross_salary': 0.0,
    'net_salary': 0.0,  # Net Salary Payable
    'pay_scale': None,  # Pay Scale range (e.g., "79800-211500")
    # Deductions & Exemptions
    'standard_deduction': 0.0,
    'professional_tax': 0.0,
    'deduction_80c': 0.0,
    'deduction_80d': 0.0,
    'deduction_80ccd': 0.0,
    'deduction_80tta': 0.0,
    'deduction_80g': 0.0,
    'total_deductions': 0.0,
    # Other Income
    'interest_income': 0.0,
    'rental_income': 0.0,
    'other_income': 0.0,
    # TDS (for reconciliation)
    'tds_amount': 0.0,
    'tds_ais': 0.0,
    # For calculations
    'taxable_income': 0.0,
    'interest_paid': 0.0,  # For Section 24(b) - home loan interest
    'rent_paid': 0.0,  # For HRA exemption calculation
}


def _to_number(x: Any) -> float:
    """Normalize different numeric/text shapes to float. Returns 0.0 on failure."""
    try:
        if x is None:
            return 0.0
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x)
        s = s.replace('(', '-').replace(')', '')
        # keep digits, dot and minus
        s = re.sub(r"[^0-9.\-]", "", s)
        if s in ('', '-', '--'):
            return 0.0
        # collapse multiple dots if any
        if s.count('.') > 1:
            parts = s.split('.')
            s = parts[0] + '.' + ''.join(parts[1:])
        return float(s)
    except Exception:
        return 0.0


class DocumentAgent:
    """DocumentAgent: minimal extraction for multiple financial document types."""

    def __init__(self, groq_api_key: Optional[str] = None):
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

    # ------------------------
    # Format detection
    # ------------------------
    def detect_format(self, file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return "pdf"
        if ext in {".jpg", ".jpeg", ".png", ".tiff"}:
            return "image"
        if ext == ".csv":
            return "csv"
        if ext in {".xls", ".xlsx"}:
            return "excel"
        return "unknown"

    def unlock_pdf(self, pdf_path: str, password: Optional[str] = None):
        try:
            with open(pdf_path, "rb") as f:
                raw = f.read()
            reader = PyPDF2.PdfReader(io.BytesIO(raw))
            if getattr(reader, "is_encrypted", False):
                if not password:
                    raise ValueError("PDF is encrypted — password required.")
                try:
                    dec = reader.decrypt(password)
                except Exception:
                    dec = 0
                if dec == 0:
                    raise ValueError("Incorrect PDF password.")
            return reader
        except Exception as e:
            raise Exception(f"PDF unlock failed: {e}")

    def extract_text_from_pdf(self, pdf_path: str, password: Optional[str] = None) -> str:
        try:
            reader = self.unlock_pdf(pdf_path, password)
            pages_text = []
            # try PyPDF2 extraction page-by-page
            for i, page in enumerate(reader.pages):
                try:
                    t = page.extract_text()
                    if t:
                        pages_text.append(t)
                        continue
                except Exception:
                    pass
                try:
                    with pdfplumber.open(pdf_path) as pp:
                        if i < len(pp.pages):
                            t2 = pp.pages[i].extract_text() or ""
                            if t2:
                                pages_text.append(t2)
                except Exception:
                    continue

            text = "\n".join(pages_text).strip()
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
                try:
                    texts.append(pytesseract.image_to_string(img, lang="eng"))
                except Exception as tesseract_err:
                    raise Exception(f"Tesseract OCR failed: {tesseract_err}")
            return "\n".join(texts)
        except Exception as e:
            raise Exception(f"OCR failed: {e}")

    def extract_from_csv_or_excel(self, file_path: str) -> str:
        try:
            if file_path.lower().endswith(".csv"):
                df = pd.read_csv(file_path)
            else:
                df = pd.read_excel(file_path)
            return df.to_csv(index=False)
        except Exception as e:
            raise Exception(f"Tabular extraction failed: {e}")

    def safe_groq_call(self, prompt: str, model: str = "llama-3.3-70b-versatile", temperature: float = 0.1, max_tokens: int = 600) -> str:
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

    def _extract_payslip(self, text: str) -> Dict[str, Any]:
        """Extract essential fields from payslip for tax filing"""
        s = DEFAULT_STRUCTURE.copy()
        
        # Helper: Extract value using patterns with validation
        def extract_value(patterns, min_val=0, max_val=float('inf')):
            for pattern in patterns:
                m = re.search(pattern, text, re.I)
                if m:
                    val = _to_number(m.group(1))
                    if min_val <= val <= max_val:
                        return val
            return 0
        
        # Helper: Extract from columnar format (label, then find value nearby)
        def extract_columnar(label_pattern, value_range=(1000, 200000), exclude_context=None):
            label_match = re.search(label_pattern, text, re.I)
            if not label_match:
                return 0
            pos = label_match.start()
            context = text[max(0, pos-200):pos].lower()
            if exclude_context and exclude_context in context:
                return 0
            window = text[max(0, pos-300):min(len(text), pos+500)]
            numbers = re.findall(r'\b([0-9]{4,})\b', window)
            candidates = []
            for num_str in numbers:
                val = _to_number(num_str)
                if value_range[0] <= val <= value_range[1]:
                    num_pos = window.find(num_str)
                    ctx = window[max(0, num_pos-10):min(len(window), num_pos+len(num_str)+10)]
                    if '-' not in ctx or (num_str not in ctx.split('-')[0] and num_str not in ctx.split('-')[1]):
                        candidates.append(val)
            return max(candidates) if candidates else 0
        
        # Name
        m = re.search(r"Sri\s*/\s*Smt\s*[:\-\s]*([A-Za-z .,'\-\(\)0-9]{2,50})", text, re.I)
        if m:
            s['name'] = re.sub(r'\s+', ' ', m.group(1).strip())

        # PAN
        m = PAN_RE.search(text) or re.search(r"Group\s*[:\-\s]*([A-Z]{5}\d{4}[A-Z])", text, re.I)
        if m:
            s['pan'] = m.group(1)

        # Period & Assessment Year
        m = re.search(r"Pay\s*Slip\s*(?:For\s*The\s*Month\s*Of|for\s*the\s*month\s*of)[:\-\s]*([A-Za-z]+)\s+(\d{4})", text, re.I)
        if m:
            period_str = f"{m.group(1).strip()} {m.group(2)}"
            s['period']['from'] = period_str
            year = int(m.group(2))
            if year >= 2020:
                fy = year - 1 if any(m in period_str.lower() for m in ['jan', 'feb', 'mar']) else year
                s['assessment_year'] = f"{fy}{str(fy+1)[-2:]}"

        # Basic Salary
        s['basic_salary'] = extract_value([
            r"Basic\s+Salary[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
            r"Basic[:\-\s]+[₹Rs\.\s,]*([0-9,]{4,}(?:\.\d{1,2})?)"
        ], 20000, 200000) or extract_columnar(r"Basic\s*:", (20000, 200000), "pay scale")
        
        # HRA - Handle "HRA49100" format (concatenated with DA value)
        # Exclude years (2020-2030) from HRA candidates
        hra_concat = re.search(r'HRA([0-9]{4,})', text, re.I)
        if hra_concat:
            concat_val = _to_number(hra_concat.group(1))
            pos = hra_concat.start()
            window = text[max(0, pos-600):min(len(text), pos+600)]
            # Extract numbers, excluding years (2020-2030) and the concatenated DA value
            nums = [_to_number(m.group(1)) for m in re.finditer(r'([0-9]{4,})(?=[A-Z]|$|\s|\n)', window) 
                   if 1000 <= _to_number(m.group(1)) <= 200000 
                   and not (2020 <= _to_number(m.group(1)) <= 2030)]  # Exclude years
            unique_nums = list(dict.fromkeys(nums))
            if unique_nums:
                hra_candidates = [v for v in unique_nums if 1000 <= v <= 15000 and v != concat_val]
                s['hra_received'] = min(hra_candidates) if hra_candidates else min([v for v in unique_nums if v != concat_val] or [0])
        
        # Fallback: Allowances section or direct pattern
        if s['hra_received'] == 0:
            allowances = re.search(r"Allowances\s*:[\s\S]{0,1000}?(?=Deductions|Gross|Recoveries|$)", text, re.I)
            if allowances and re.search(r'\bHRA\b', allowances.group(0), re.I):
                # Extract numbers from Allowances section, excluding years
                nums = [_to_number(m.group(1)) for m in re.finditer(r'([0-9]{4,})(?=[A-Z]|$|\s|\n)', allowances.group(0))
                       if 1000 <= _to_number(m.group(1)) <= 200000 
                       and not (2020 <= _to_number(m.group(1)) <= 2030)]  # Exclude years
                unique_nums = list(dict.fromkeys(nums))
                if unique_nums:
                    hra_candidates = [v for v in unique_nums if 1000 <= v <= 15000]
                    s['hra_received'] = min(hra_candidates) if hra_candidates else min(unique_nums) if len(unique_nums) >= 2 else 0
            else:
                s['hra_received'] = extract_value([r"HRA[:\-\s]+([0-9,]+(?:\.\d{1,2})?)(?=\s|$|\n|[^0-9,])"], 1000, 15000)
        
        # DA - Extract from "HRA49100" (concatenated value is DA) or Allowances section
        hra_concat = re.search(r'HRA([0-9]{4,})', text, re.I)
        if hra_concat:
            concat_val = _to_number(hra_concat.group(1))
            if 10000 <= concat_val <= 200000:
                s['da'] = concat_val
        if s['da'] == 0:
            s['da'] = extract_value([
                r"\bDA[:\-\s]+[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
                r"\bDA([0-9]{4,})(?=\s|$|\n|[A-Z])"
            ], 10000, 200000)
            if s['da'] == 0:
                allowances = re.search(r"Allowances\s*:[\s\S]{0,1000}?(?=Deductions|Gross|Recoveries|$)", text, re.I)
                if allowances and re.search(r'\bDA\b', allowances.group(0), re.I):
                    nums = [_to_number(n) for n in re.findall(r'([0-9]{4,})(?=[A-Z]|$|\s|\n)', allowances.group(0)[allowances.group(0).find("DA"):])
                           if 10000 <= _to_number(n) <= 200000]
                    s['da'] = nums[0] if nums else 0

        # Gross Salary
        s['gross_salary'] = extract_value([
            r"Gross\s*Salary\s*\([^)]*\)[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
            r"Gross\s*Salary\s*\([^\n)]*\)[^\n]{0,50}?[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
            r"Rec\.\)\s*:[^\d]{0,5}?([0-9,]{5,}(?:\.\d{1,2})?)",
            r"Gross\s*(?:Salary|Earnings|Total)[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
            r"Gross[:\-\s]*[₹Rs\.\s,]*([0-9,]{5,}(?:\.\d{1,2})?)"
        ], 10000)
        if s['gross_salary'] == 0 and s['basic_salary'] > 0 and s['hra_received'] > 0:
            s['gross_salary'] = s['basic_salary'] + s['hra_received'] + (s['basic_salary'] * 0.3)

        # Net Salary
        s['net_salary'] = extract_value([
            r"Net\s*Salary\s*(?:Payable)?[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
            r"Net\s*Pay[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
            r"Net\s*Salary\s*:[^\d]{0,10}?([0-9,]{4,}(?:\.\d{1,2})?)",
            r"Net\s*Salary\s*:[^\d]*?([0-9]{5,})"
        ], 10000, 500000)

        # Professional Tax
        s['professional_tax'] = extract_value([
            r"Professional\s*Tax[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
            r"\bPT[:\-\s]+[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
            r"\bPT([0-9]{3,})"
        ], 100, 5000)
        if s['professional_tax'] == 0:
            pt_match = re.search(r"\bPT\b", text, re.I)
            if pt_match:
                window = text[pt_match.end():pt_match.end()+200]
                nums = [_to_number(n) for n in re.findall(r'\b([0-9]{3,4})\b', window) if 100 <= _to_number(n) <= 5000]
                s['professional_tax'] = nums[0] if nums else 0

        # Pay Scale
        m = re.search(r"Pay\s*Scale\s*[:\-\s]*([0-9,]+(?:\s*-\s*[0-9,]+)?)", text, re.I)
        if m:
            s['pay_scale'] = m.group(1).strip()

        # Total Deductions
        s['total_deductions'] = extract_value([
            r"Sum\s*of\s*Deductions[^\d]{0,50}?[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
            r"Total\s*Deductions[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
            r"Deductions[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)"
        ], 1)

        # TDS
        s['tds_amount'] = extract_value([
            r"\bIT[:\-\s]+[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
            r"\bTDS[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
            r"Tax\s*Deducted[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)"
        ], 1000, 50000)
        if s['tds_amount'] == 0:
            for it_match in re.finditer(r"(?<![0-9])\bIT\b(?![0-9])", text, re.I):
                window = text[it_match.end():it_match.end()+300]
                nums = [_to_number(n) for n in re.findall(r'\b([0-9]{4,5})\b', '\n'.join(window.split('\n')[:10])) 
                       if 1000 <= _to_number(n) <= 50000]
                if nums:
                    s['tds_amount'] = nums[0]
                    break

        return s



    # Bank Statement extraction removed - not needed for tax filing
    # Interest income is extracted from AIS/TIS instead

    def _extract_ais(self, text: str) -> Dict[str, Any]:
        s = DEFAULT_STRUCTURE.copy()
        
        # Part A: Personal Info
        m = PAN_RE.search(text)
        if m:
            s['pan'] = m.group(1)
        
        # Name
        name_match = re.search(
            r"([A-Z]{5}\d{4}[A-Z])\s+XXXX\s+XXXX\s+[0-9]+\s+([A-Z][A-Za-z\s]{2,30}?)(?:\s+(?:Date\s+of\s+Birth|Mobile\s+Number|E-mail|Address|Assessment)|$|\n)",
            text, re.I
        )
        if name_match:
            potential_name = name_match.group(2).strip()
            if not re.match(r'^(XXXX|Active|Status|Assessee|Name|Number|Date|Mobile|E-mail)', potential_name, re.I):
                if len(potential_name.split()) >= 2:
                    s['name'] = potential_name
        
        # Date of Birth
        dob_match = re.search(r"Date\s+of\s+Birth\s*([0-9]{2}[/-][0-9]{2}[/-][0-9]{4})", text, re.I)
        if dob_match:
            s['_dob'] = dob_match.group(1)  # Store in _dob for profile
        
        # Address
        address_match = re.search(r"Address\s*([^\n]{10,200})", text, re.I)
        if address_match:
            s['_address'] = address_match.group(1).strip()  # Store in _address for profile
        
        # Mobile Number
        mobile_match = re.search(r"Mobile\s+Number\s*([0-9]{10})", text, re.I)
        if mobile_match:
            s['_mobile'] = mobile_match.group(1)  # Store in _mobile for profile
        
        # Email Address
        email_match = re.search(r"E-mail\s+Address\s*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", text, re.I)
        if email_match:
            s['_email'] = email_match.group(1)  # Store in _email for profile
        
        # Assessment Year
        m = AY_RE.search(text)
        if m:
            s['assessment_year'] = m.group(2).replace('-', '')
        
        # Part B7: Gross Salary
        gross_match = re.search(r"GROSS\s*SALARY\s*U/S\s*17\(1\)", text, re.I)
        if gross_match:
            # Look for number after the header (next line typically)
            after_header = text[gross_match.end():gross_match.end()+300]
            # Pattern: Find large number with commas like "20,32,474" or "2032474"
            # Match numbers with 2+ comma groups (e.g., "20,32,474") or 6+ digits without commas
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
        
        
        
        # Part B1: TDS
        # Format: "1GOVERNMENT FIRST GRADE COLLEGE TIPTURBLRG09945F1567698.00197200.00197200.00"
        # Or: "1TDS-192 Salary received (Section 192) GOVERNMENT FIRST GRADE COLLEGE TIPTUR (BLRG09945F) 1016,30,610"
        # Then detail rows with quarterly TDS
        
        if s['tds_ais'] == 0:
            for pattern in [r"Total\s*Tax\s*Deducted[#\s]+[^\n]{0,100}?([0-9,]{5,}(?:\.\d{1,2})?)",
                           r"Total\s*TDS\s*Deposited[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)"]:
                m = re.search(pattern, text, re.I)
                if m:
                    val = _to_number(m.group(1))
                    if 10000 <= val <= 1000000:
                        s['tds_ais'] = s['tds_amount'] = val
                        break
        
        quarterly_tds = []
        quarterly_matches = re.finditer(r"Q\d+\([^)]+\)\s+([0-9]{2}[/-][0-9]{2}[/-][0-9]{4})\s+([0-9,]+(?:\.\d{1,2})?)\s+([0-9,]+(?:\.\d{1,2})?)\s+([0-9,]+(?:\.\d{1,2})?)", text, re.I)
        for match in quarterly_matches:
            date = match.group(1)
            amount = _to_number(match.group(2))
            tds_deducted = _to_number(match.group(3))
            tds_deposited = _to_number(match.group(4))
            tds = tds_deposited if tds_deposited > 0 else tds_deducted
            if 1000 <= tds <= 50000:  # Valid monthly/quarterly TDS
                quarterly_tds.append({"date": date, "amount_paid": amount, "tds": tds})
        
        # Sum quarterly TDS if not already extracted
        if quarterly_tds and s['tds_ais'] == 0:
            total_tds = sum([entry['tds'] for entry in quarterly_tds])
            if 10000 <= total_tds <= 1000000:
                s['tds_ais'] = total_tds
                s['tds_amount'] = total_tds
        
        
        # Part B2: Interest Income (SFT-016)
        interest_entries = []
        detail_rows = re.finditer(r"([0-9]{2}[/-][0-9]{2}[/-][0-9]{4})\s+([0-9]+)\s+Saving[^\d]{0,20}?([0-9,]+(?:\.\d{1,2})?)", text, re.I)
        for row in detail_rows:
            date_reported = row.group(1)
            account = row.group(2)
            interest_amt = _to_number(row.group(3))
            if 50 <= interest_amt <= 50000:
                # Try to find bank name from preceding SFT-016 line
                row_start = row.start()
                before_row = text[max(0, row_start-200):row_start]
                bank_match = re.search(r"SFT-016\(SB\)[^\n]{0,200}?([A-Z\s]+?)\s*\([A-Z0-9.]+\)", before_row, re.I)
                bank_name = bank_match.group(1).strip() if bank_match else "Unknown"
                
                interest_entries.append({
                    "bank": bank_name,
                    "account": account,
                    "amount": interest_amt,
                    "date": date_reported
                })
        
        # Method 2: Extract from SFT-016 summary lines (if detail rows not found)
        if not interest_entries:
            sft_matches = re.finditer(r"SFT-016\(SB\)[^\n]{0,300}?([A-Z\s]+?)\s*\([A-Z0-9.]+\)\s+[0-9]+\s+([0-9,]+(?:\.\d{1,2})?)", text, re.I)
            for match in sft_matches:
                bank_name = match.group(1).strip()
                interest_amt = _to_number(match.group(2))
                if 50 <= interest_amt <= 50000:
                    interest_entries.append({
                        "bank": bank_name,
                        "account": None,
                        "amount": interest_amt,
                        "date": None
                    })
        
        # Sum all interest entries
        if interest_entries:
            total_interest = sum([entry['amount'] for entry in interest_entries])
            s['interest_income'] = total_interest
        
        return s

    def _extract_loan_certificate(self, text: str) -> Dict[str, Any]:
        s = DEFAULT_STRUCTURE.copy()
        m = re.search(r"Name of Borrower[:\-\s]*([A-Za-z .,'\-\(\)0-9]{3,100})", text, re.I)
        if m:
            s['name'] = m.group(1).strip()
        
        # Interest Paid - look for "INTEREST COMPONENT Rs. 3,07,998.00"
        interest_patterns = [
            r"INTEREST\s+COMPONENT[^\d]{0,50}?Rs\.\s*([0-9,]+(?:\.\d{1,2})?)",  # "INTEREST COMPONENT Rs. 3,07,998.00"
            r"Interest\s+Paid[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
            r"Interest\s+Component[:\-\s]*[₹Rs\.\s,]*([0-9,]+(?:\.\d{1,2})?)",
        ]
        for pattern in interest_patterns:
            m2 = re.search(pattern, text, re.I)
            if m2:
                val = _to_number(m2.group(1))
                if val > 0:
                    s['interest_paid'] = val
                    break
        return s

    def _extract_rent_receipt(self, text: str) -> Dict[str, Any]:
        s = DEFAULT_STRUCTURE.copy()
        m = re.search(r"Rent[:\-\s]*[₹Rs\.\s]*([0-9,]+(?:\.\d{1,2})?)", text, re.I)
        if m:
            s['rent_paid'] = _to_number(m.group(1))
        return s

    def _llm_numeric_cleanup(self, text: str, struct: Dict[str, Any]) -> Dict[str, Any]:
        """Use LLM only when regex fails to extract essential fields"""
        if not self.client:
            return struct
        
        doc_type = struct.get('_doc_type', 'Unknown')
        if 'AIS' in doc_type or 'TIS' in doc_type:
            essential_fields = ['tds_ais', 'interest_income']
        elif 'Payslip' in doc_type:
            essential_fields = ['gross_salary', 'basic_salary']
        else:
            essential_fields = ['gross_salary', 'basic_salary', 'tds_amount']
        
        if any(struct.get(k, 0) > 0 for k in essential_fields):
            return struct
        
        try:
            if 'AIS' in doc_type or 'TIS' in doc_type:
                prompt = f'Extract from AIS/TIS. Return JSON: {{"tds_ais": 0, "tds_amount": 0, "interest_income": 0, "rental_income": 0, "other_income": 0}}\n\nText:\n{text[:6000]}'
            elif 'Payslip' in doc_type:
                prompt = f'Extract from Payslip. Return JSON: {{"basic_salary": 0, "hra_received": 0, "gross_salary": 0, "net_salary": 0, "tds_amount": 0, "professional_tax": 0}}\n\nText:\n{text[:6000]}'
            else:
                return struct
            
            raw = self.safe_groq_call(prompt, max_tokens=500)
            s = raw.replace("```json", "").replace("```", "").strip()
            if "{" in s:
                start, end = s.find("{"), s.rfind("}") + 1
                if end > start:
                    try:
                        parsed = json.loads(s[start:end])
                        for k, v in parsed.items():
                            if k in struct:
                                val = _to_number(v)
                                if val > 0 and (struct.get(k, 0) == 0 or struct.get(k) is None):
                                    struct[k] = val
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass
        return struct

    def save_output(self, file_path: str, doc_type: str, structured: Dict[str, Any], text: Optional[str] = None, save_raw: bool = False) -> Dict[str, Any]:
        payload = {
            "document_type": doc_type,
            "structured_data": structured,
        }
        return payload

    def process(self, file_path: str, password: Optional[str] = None, debug: bool = False) -> Dict[str, Any]:
        fmt = self.detect_format(file_path)
        if fmt == "pdf":
            text = self.extract_text_from_pdf(file_path, password)
        elif fmt == "image":
            text = self.ocr_extract(file_path)
        elif fmt in ("csv", "excel"):
            text = self.extract_from_csv_or_excel(file_path)
        else:
            raise ValueError(f"Unsupported file: {file_path}")

        doc_type = self.identify_document_type(text)

        # route to extractor
        if doc_type == "Payslip":
            structured = self._extract_payslip(text)
        elif doc_type == "AIS":
            structured = self._extract_ais(text)
        elif doc_type == "TIS":
            structured = self._extract_ais(text)  # TIS similar to AIS
        elif doc_type == "Loan Certificate":
            structured = self._extract_loan_certificate(text)
        elif doc_type == "Rent Receipt":
            structured = self._extract_rent_receipt(text)
        else:
            # minimal generic fallback: PAN + largest currency -> gross
            structured = DEFAULT_STRUCTURE.copy()
            m = PAN_RE.search(text)
            if m:
                structured['pan'] = m.group(1)
            cand = [ _to_number(mm.group(1)) for mm in CURRENCY_RE.finditer(text) ]
            if cand:
                structured['gross_salary'] = max(cand)

        structured['_doc_type'] = doc_type
        structured = self._llm_numeric_cleanup(text, structured)

        for k in ['gross_salary', 'total_deductions', 'taxable_income', 'tds_amount', 'interest_paid', 'rent_paid']:
            structured[k] = round(_to_number(structured.get(k, 0.0)), 2)
        structured = self._filter_essential_fields(structured, doc_type)
        
        result = self.save_output(file_path, doc_type, structured, text=text if debug else None, save_raw=debug)

        if debug:
            result['raw_text'] = text

        return result

    def _filter_essential_fields(self, structured: Dict[str, Any], doc_type: str) -> Dict[str, Any]:
        """Filter to essential tax filing fields, removing 0/None/empty values"""
        # Document-specific essential fields
        if doc_type == "Payslip":
            essential_fields = {
                'name', 'pan', 'assessment_year', 'period',
                'basic_salary', 'hra_received', 'da', 'gross_salary', 'net_salary', 'pay_scale',
                'professional_tax', 'total_deductions', 'tds_amount',
                '_doc_type'
            }
        elif doc_type in ["AIS", "TIS"]:
            essential_fields = {
                'name', 'pan', 'assessment_year',
                'gross_salary',  # From Part B7 - PRIMARY for tax calculation
                'interest_income', 'rental_income', 'other_income',
                'tds_ais', 'tds_amount',
                '_doc_type'
            }
        elif doc_type == "Loan Certificate":
            essential_fields = {
                'interest_paid', 'period',
                '_doc_type'
            }
        elif doc_type == "Rent Receipt":
            essential_fields = {
                'rent_paid', 'period',
                '_doc_type'
            }
        else:
            essential_fields = {
                'name', 'pan', 'assessment_year', 'period',
                'gross_salary', 'basic_salary', 'hra_received', 'da', 'net_salary', 'pay_scale',
                'interest_income', 'rental_income', 'other_income',
                'standard_deduction', 'professional_tax', 'total_deductions',
                'deduction_80c', 'deduction_80d', 'deduction_80ccd', 
                'deduction_80tta', 'deduction_80g',
                'tds_amount', 'tds_ais',
                'rent_paid', 'interest_paid', 'taxable_income',
                '_doc_type'
            }
        
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

