# Tax Filing - Document Extraction Requirements

## PAYSLIP (Monthly) - What to Extract:

### ✅ MUST EXTRACT:
1. **Basic Salary** - For HRA exemption calculation
2. **HRA Received** - For HRA exemption calculation  
3. **Gross Salary** - Total income
4. **Professional Tax** - Deduction under Section 16
5. **TDS Amount** - Monthly tax deducted (for reconciliation)
6. **Period (Month/Year)** - To determine assessment year
7. **Name** - For identification
8. **PAN** - If available

### ❌ SHOULD NOT EXTRACT (comes from other documents):
- Standard Deduction - Usually in Form 16 (annual)
- Section 80C/80D/80CCD - From Form 16
- Interest Income - From AIS/TIS
- Interest Paid (home loan) - From Loan Certificate
- Rent Paid - From Rent Receipts

### 🔍 CURRENT ISSUES:
1. **HRA** - Extracting 49100 instead of 7856 (columnar format issue)
2. **Basic Salary** - Not extracted (showing 0)
3. **Assessment Year** - Not derived from period
4. **TDS** - May not be extracting correctly from columnar format

---

## FORM 16 (Annual) - What to Extract:

### ✅ MUST EXTRACT:
1. **Gross Salary** - Total annual salary
2. **Basic Salary** - Annual basic
3. **HRA Received** - Annual HRA (if shown)
4. **Standard Deduction** - Usually ₹50,000
5. **Professional Tax** - Annual total
6. **Section 80C** - Deductions under 80C
7. **Section 80D** - Health insurance
8. **Section 80CCD** - NPS contributions
9. **TDS (tds_form16)** - Total TDS deducted
10. **Taxable Income** - If shown
11. **Assessment Year** - From form
12. **PAN** - Employee PAN
13. **Name** - Employee name

---

## AIS/TIS - What to Extract:

### ✅ MUST EXTRACT:

#### Part A - General Information (for ITR/profile):
1. **PAN** - Taxpayer PAN
2. **Name** - Name of Assessee
3. **Date of Birth** - For profile
4. **Address** - For profile
5. **Mobile Number** - For profile
6. **Email Address** - For profile
7. **Assessment Year** - Financial year

#### Part B1 - TDS/TCS (Salary) - For cross-verification:
1. **Employer Name** - For validation
2. **Employer TAN** - For validation
3. **Total Amount Paid/Credited** - Salary credited (₹16,30,610)
4. **Total TDS Deducted** - Sum of all months
5. **Monthly/Quarterly Breakdown** - Date, Amount, TDS (for validation)

#### Part B2 - Interest Income (SFT-016) - For "Income from Other Sources":
1. **Bank Name** - Source of interest
2. **Account Number** - For reference
3. **Interest Amount** - Per bank (e.g., CPRC: ₹1,371, SBI: ₹1,298, Canara: ₹704)
4. **Date Reported** - When reported
5. **Total Interest Income** - Sum of all bank interests

#### Part B7 - Salary (TDS Annexure II) - **MOST IMPORTANT**:
1. **Gross Salary u/s 17(1)** - ₹20,32,474 (PRIMARY salary figure)
2. **Perquisites u/s 17(2)** - Usually 0
3. **Profits in lieu of salary u/s 17(3)** - Usually 0
4. **Total Gross Salary** - ₹20,32,474
5. **Employment Start Date** - For reference
6. **Employment End Date** - For reference

#### Part B3 - Tax Payments (if present):
1. **Advance Tax** - If paid
2. **Self-assessment Tax** - If paid
3. **Challan Details** - BSR Code, Date, Serial Number

#### Part B4 - Refund Information:
1. **Refund Amount** - Previous year refunds (e.g., ₹6,100)
2. **Assessment Year** - For which refund was received
3. **Date of Payment** - When refund was credited

---

## LOAN CERTIFICATE - What to Extract:

### ✅ MUST EXTRACT:
1. **Interest Paid** - For Section 24(b) deduction
2. **Principal Component** - For reference
3. **Period** - Financial year (for which interest is paid)

---

## FORM 26AS - What to Extract:

### ✅ MUST EXTRACT:
1. **TDS (tds_26as)** - Total TDS from all sources
2. **PAN** - Taxpayer PAN
3. **Assessment Year** - Financial year

---

## RENT RECEIPTS - What to Extract:

### ✅ MUST EXTRACT:
1. **Rent Paid** - Monthly/annual rent (for HRA exemption)

