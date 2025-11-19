import os
import json
import streamlit as st
from datetime import datetime

from agents.document_agent import DocumentAgent
from agents.calculation_agent import CalculationAgent
from agents.rag_agent import RAGAgent


st.set_page_config(page_title="AI Multi-Agent Tax Filing System", layout="wide")
st.title("🧾 AI-Powered Multi-Agent Tax Filing Assistant")
st.caption("Extraction → Consolidation → Calculation → Report (Multi-Agent)")

# ----------------------------------------------------
# UPLOAD
# ----------------------------------------------------
uploaded_files = st.file_uploader(
    "📂 Upload Financial Documents (PDF / CSV / Image). Upload Payslip + AIS at minimum.",
    type=["pdf", "csv", "jpg", "jpeg", "png"],
    accept_multiple_files=True
)

if not uploaded_files:
    st.warning("Upload at least one file to proceed.")
    st.stop()

password_map = {}
for f in uploaded_files:
    if f.name.lower().endswith(".pdf"):
        password_map[f.name] = st.text_input(
            f"Password for {f.name} (if any)",
            type="password",
            key=f.name
        )

# ----------------------------------------------------
# AGENTS
# ----------------------------------------------------
document_agent = DocumentAgent()
calc_agent = CalculationAgent()


# ----------------------------------------------------
# EXTRACTION
# ----------------------------------------------------
if st.button("🧠 Extract & Analyze Documents"):
    with st.spinner("Extracting documents..."):
        extracted = []
        os.makedirs("data/uploads", exist_ok=True)

        for f in uploaded_files:
            path = os.path.join("data/uploads", f.name)
            with open(path, "wb") as fp:
                fp.write(f.getbuffer())

            pwd = password_map.get(f.name)
            out = document_agent.process(path, pwd, debug=False)
            out.pop("raw_text", None)
            extracted.append(out)

        st.session_state["extracted"] = extracted
        st.success("Extraction complete!")
        
        with st.expander("View Extracted Data"):
            st.json(extracted)


# ----------------------------------------------------
# CONSOLIDATION
# ----------------------------------------------------
if st.button("🧩 Consolidate Extracted Documents"):
    if "extracted" not in st.session_state:
        st.error("Run extraction first!")
        st.stop()

    with st.spinner("Consolidating..."):
        cons = document_agent.consolidate(st.session_state["extracted"])
        st.session_state["cons"] = cons
        st.success("Consolidation complete!")
        
        with st.expander("View Consolidated Data"):
            st.json(cons)


# ----------------------------------------------------
# TAX CALCULATION
# ----------------------------------------------------
if st.button("🧮 Calculate Tax"):
    if "cons" not in st.session_state:
        st.error("Run consolidation first!")
        st.stop()

    cons = st.session_state["cons"]["consolidated"]

    # -----------------------------
    # Correct Income Mapping (NO DOUBLE COUNTING)
    # -----------------------------
    gross_salary = cons.get("income_components", {}).get("Gross Salary", 0.0)
    interest_income = cons.get("income_components", {}).get("Interest Income", 0.0)
    total_income = cons.get("income_components", {}).get("Total Income", 0.0)

    # FIX: If gross_salary missing, derive from total − interest
    if not gross_salary or gross_salary == 0:
        gross_salary = total_income - interest_income

    # NPS from consolidation if present
    nps_val = cons.get("deductions", {}).get("NPS", 0.0) or \
              cons.get("deductions", {}).get("nps_employee", 0.0) or 0.0

    # Correct TDS extraction
    tds_val = cons.get("tds", 0.0)

    # Pass full consolidated data including deductions
    mapped = {
        "gross_total_income": float(total_income or gross_salary),
        "interest_income": float(interest_income),
        "deductions": cons.get("deductions", {}),  # Pass full deductions dict
        "tds": float(tds_val),
    }

    with st.spinner("Calculating tax..."):
        calc = calc_agent.process(mapped)
        st.session_state["calc"] = calc

        st.success("✔ Tax Calculation Complete")

        st.subheader("📊 Tax Computation Summary")
        st.write(f"**Gross Total Income:** ₹{calc['gross_total_income']:,.2f}")
        st.write(f"**Taxable Income (Old):** ₹{calc['taxable_income_old']:,.2f}")
        st.write(f"**Taxable Income (New):** ₹{calc['taxable_income_new']:,.2f}")
        st.write(f"**Tax (Old):** ₹{calc['tax_old']:,.2f}")
        st.write(f"**Tax (New):** ₹{calc['tax_new']:,.2f}")
        st.write(f"**Chosen Regime:** {calc['chosen_regime'].upper()}")
        st.write(f"**Final Tax:** ₹{calc['final_tax']:,.2f}")
        st.write(f"**TDS:** ₹{calc['tds']:,.2f}")
        st.write(f"**Tax Due:** ₹{calc['tax_due']:,.2f}")
        st.write(f"**Refund:** ₹{calc['refund']:,.2f}")

        # Show AI Regime Reasoning
        if calc.get("regime_reasoning"):
            st.subheader("🤖 AI Regime Selection Analysis")
            reasoning = calc["regime_reasoning"]
            st.info(f"**Recommended Regime:** {reasoning.get('recommended_regime', calc['chosen_regime'].upper())}")
            st.write(f"**Reasoning:** {reasoning.get('reasoning', 'N/A')}")
            with st.expander("📋 Detailed Analysis"):
                st.write(reasoning.get('detailed_analysis', 'N/A'))
                if reasoning.get('key_factors'):
                    st.write("**Key Factors:**")
                    for factor in reasoning.get('key_factors', []):
                        st.write(f"- {factor}")
                if reasoning.get('savings_amount'):
                    st.write(f"**Estimated Savings:** ₹{reasoning.get('savings_amount', 0):,.2f}")

        # Show Calculation Explanation
        if calc.get("calculation_explanation"):
            with st.expander("📐 Detailed Calculation Breakdown"):
                st.markdown(calc["calculation_explanation"])


# ----------------------------------------------------
# FINAL REPORT
# ----------------------------------------------------
if st.button("🧾 Generate Final AI Tax Report"):
    if "calc" not in st.session_state:
        st.error("Run tax calculation first!")
        st.stop()

    if "cons" not in st.session_state:
        st.error("Run consolidation first!")
        st.stop()

    calc = st.session_state["calc"]
    consolidated = st.session_state["cons"]["consolidated"]

    with st.spinner("Creating final report..."):
        result = calc_agent.generate_report(calc, consolidated)

        st.success("Report generated!")

        st.subheader("Summary Text")
        st.text(result.get("summary_text", ""))


# ----------------------------------------------------
# RAG Q&A (Enhanced)
# ----------------------------------------------------
st.header("💬 AI Chat Assistant - Ask Questions About Your Tax Filing")
st.caption("Ask about deductions, calculations, filing guidance, or tax planning strategies")

if "rag" not in st.session_state:
    try:
        st.session_state["rag"] = RAGAgent()
        # Add documents to RAG index if available
        if "extracted" in st.session_state:
            st.session_state["rag"].add_documents(st.session_state["extracted"])
        if "cons" in st.session_state:
            st.session_state["rag"].add_consolidated_data(st.session_state["cons"]["consolidated"])
    except Exception as e:
        st.warning(f"RAG not available: {e}")

rag = st.session_state.get("rag")
question = st.text_input("Ask a question about your filing", 
                         placeholder="e.g., 'What documents am I missing?', 'How much should I invest to reduce tax?', 'What regime should I choose?'")

if st.button("🔍 Ask") and rag:
    try:
        consolidated = st.session_state.get("cons", {}).get("consolidated") if "cons" in st.session_state else None
        calc_result = st.session_state.get("calc") if "calc" in st.session_state else None
        
        answer = rag.query(
            question, 
            n_results=3,
            consolidated_data=consolidated,
            calc_result=calc_result
        )
        
        st.write("**Answer:**")
        st.write(answer.get("answer", "No answer generated"))
        
        # Show example questions
        with st.expander("💡 Example Questions"):
            st.write("""
            - "What documents am I missing?"
            - "How much should I invest to reduce tax?"
            - "What regime should I choose and why?"
            - "Explain my salary breakup in simple words"
            - "What deductions am I eligible for?"
            - "How much tax can I save if I invest ₹20,000 more in NPS?"
            - "What is my HRA exemption?"
            - "Should I switch to old regime?"
            """)
    except Exception as e:
        st.error(f"Query failed: {e}")
