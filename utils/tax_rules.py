# utils/tax_rules.py

# Indian tax rules helper (simplified but extensible)
# Keep this updated each FY. Values shown are example placeholders; change to current FY as needed.

OLD_REGIME_SLABS = [
    (250000, 0.0),
    (500000, 0.05),
    (1000000, 0.20),
    (0, 0.30)  # 0 means "rest"
]

NEW_REGIME_SLABS = [
    (250000, 0.0),
    (500000, 0.05),
    (750000, 0.10),
    (1000000, 0.15),
    (1250000, 0.20),
    (1500000, 0.25),
    (0, 0.30)
]

CESS_RATE = 0.04  # 4% health & education cess

# Basic rebate under Sec 87A (example)
REBATE_87A_LIMIT = 500000  # taxable income threshold for rebate
REBATE_87A_AMOUNT = 12500  # maximum rebate amount (approx for example)

# Surcharge thresholds (simplified)
SURCHARGE_RULES = [
    (5000000, 0.10),   # 10% surcharge above 50 Lakh
    (10000000, 0.15),  # 15% above 1 Crore
    (20000000, 0.25),
    (50000000, 0.37),
]
