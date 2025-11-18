# Tesseract OCR Installation Guide for Windows

## What is Tesseract?

Tesseract is an OCR (Optical Character Recognition) engine used to extract text from images. It's required by `pytesseract` to perform OCR on scanned PDFs and images.

## Why Do You Need It?

When a PDF contains scanned images or cannot be read directly, the system uses OCR to extract text. This requires:
1. **Poppler** - to convert PDF pages to images (see `POPPLER_INSTALLATION.md`)
2. **Tesseract** - to extract text from those images

## Installation for Windows

### Option 1: Download and Install (Recommended)

1. **Download Tesseract for Windows:**
   - Visit: https://github.com/UB-Mannheim/tesseract/wiki
   - Download the latest installer (e.g., `tesseract-ocr-w64-setup-5.x.x.exe`)
   - Run the installer

2. **During Installation:**
   - Choose installation directory (default: `C:\Program Files\Tesseract-OCR`)
   - **Important:** Check "Add to PATH" option if available, OR manually add to PATH later

3. **Add to System PATH (if not done during installation):**
   - Open System Properties → Environment Variables
   - Under "System variables", find "Path" and click "Edit"
   - Click "New" and add: `C:\Program Files\Tesseract-OCR`
   - Click OK to save

4. **Restart your terminal/IDE** for PATH changes to take effect

5. **Verify Installation:**
   ```bash
   tesseract --version
   ```
   If you see version information, Tesseract is installed correctly.

### Option 2: Set Environment Variable (Alternative)

If you don't want to modify PATH, you can set an environment variable:

1. Install Tesseract (same as Option 1, step 1-2)

2. Set environment variable:
   - Open System Properties → Environment Variables
   - Under "User variables" or "System variables", click "New"
   - Variable name: `TESSERACT_CMD`
   - Variable value: Full path to tesseract.exe (e.g., `C:\Program Files\Tesseract-OCR\tesseract.exe`)
   - Click OK to save

3. Restart your terminal/IDE

### Option 3: Use Conda (If using Anaconda/Miniconda)

```bash
conda install -c conda-forge tesseract
```

## Quick Test

After installation, test if it works:

```python
import pytesseract
from PIL import Image

# If Tesseract is not in PATH, set it manually:
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Test with an image
image = Image.open("test.png")
text = pytesseract.image_to_string(image)
print(f"Extracted text: {text}")
```

## Troubleshooting

### Error: "tesseract is not installed or it's not in your PATH"

**Solution:**
1. Verify Tesseract is installed: Check if `C:\Program Files\Tesseract-OCR\tesseract.exe` exists
2. If installed but not in PATH:
   - Add to PATH (see Option 1, step 3), OR
   - Set `TESSERACT_CMD` environment variable (see Option 2)
3. Restart your terminal/IDE after making changes

### Error: "TesseractNotFoundError"

**Solution:**
- Make sure you downloaded the Windows version
- Ensure the `tesseract.exe` path is correct
- Restart your terminal/IDE after adding to PATH or setting environment variable
- The code will automatically check common installation paths, but you can also set `TESSERACT_CMD` in your `.env` file

## Configuration in Code

The code automatically detects Tesseract in common locations:
- `C:\Program Files\Tesseract-OCR\tesseract.exe`
- `C:\Program Files (x86)\Tesseract-OCR\tesseract.exe`
- `C:\Tesseract-OCR\tesseract.exe`

Or set in `.env` file:
```
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

## Alternative: Skip OCR

If you don't want to install Tesseract, the system will still work for most PDFs that have extractable text. OCR is only needed for:
- Scanned PDFs (image-based)
- PDFs with poor text extraction
- Handwritten documents

For most Form 16, Payslip, and Bank Statement PDFs, direct text extraction works fine without Tesseract.

