# Poppler Installation Guide for Windows

## What is Poppler?

Poppler is a PDF rendering library required by `pdf2image` to convert PDF pages to images for OCR (Optical Character Recognition). It's a system-level dependency, not a Python package.

## Why Do You Need It?

When a PDF contains scanned images or cannot be read directly, the system uses OCR to extract text. This requires converting PDF pages to images first, which needs Poppler.

## Installation for Windows

### Option 1: Download and Add to PATH (Recommended)

1. **Download Poppler for Windows:**
   - Visit: https://github.com/oschwartz10612/poppler-windows/releases
   - Download the latest release (e.g., `Release-XX.XX.X-X.zip`)
   - Extract the ZIP file to a location like `C:\poppler` or `C:\Program Files\poppler`

2. **Add to System PATH:**
   - Open System Properties → Environment Variables
   - Under "System variables", find "Path" and click "Edit"
   - Click "New" and add the path to the `bin` folder (e.g., `C:\poppler\Library\bin`)
   - Click OK to save

3. **Restart your terminal/IDE** for PATH changes to take effect

4. **Verify Installation:**
   ```bash
   pdftoppm -h
   ```
   If you see help text, Poppler is installed correctly.

### Option 2: Set Environment Variable (Alternative)

If you don't want to modify PATH, you can set an environment variable:

1. Download and extract Poppler (same as Option 1, step 1)

2. Set environment variable:
   - Open System Properties → Environment Variables
   - Under "User variables" or "System variables", click "New"
   - Variable name: `POPPLER_PATH`
   - Variable value: Path to the `bin` folder (e.g., `C:\poppler\Library\bin`)

3. Restart your terminal/IDE

### Option 3: Use Conda (If using Anaconda/Miniconda)

```bash
conda install -c conda-forge poppler
```

## Quick Test

After installation, test if it works:

```python
from pdf2image import convert_from_path
images = convert_from_path("test.pdf")
print(f"Converted {len(images)} pages")
```

## Troubleshooting

### Error: "Unable to get page count. Is poppler installed and in PATH?"

**Solution:**
1. Verify Poppler is in PATH: Open Command Prompt and type `pdftoppm -h`
2. If not found, re-add to PATH and restart terminal
3. If using environment variable, ensure `POPPLER_PATH` is set correctly

### Error: "poppler not found"

**Solution:**
- Make sure you downloaded the Windows version (not Linux/Mac)
- Ensure the `bin` folder path is correct
- Restart your terminal/IDE after adding to PATH

### Still Not Working?

The system will try to extract text using PyPDF2 and pdfplumber first, which don't require Poppler. OCR is only used as a last resort when direct text extraction fails.

## Alternative: Skip OCR

If you don't want to install Poppler, the system will still work for most PDFs that have extractable text. OCR is only needed for:
- Scanned PDFs (image-based)
- PDFs with poor text extraction
- Handwritten documents

For most Form 16, Payslip, and Bank Statement PDFs, direct text extraction works fine without Poppler.

