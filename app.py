import os
import json
import streamlit as st
from datetime import datetime

from agents.document_agent import DocumentAgent
from agents.consolidation_agent import ConsolidationAgent
from agents.calculation_agent import CalculationAgent
from agents.filing_agent import FilingAgent
from agents.decision_agent import DecisionAgent
from agents.report_agent import ReportAgent
from agents.rag_agent import RAGAgent

# -------------------------------------------------------------
# 🌐 Streamlit Setup
# -------------------------------------------------------------
st.set_page_config(page_title="AI Multi-Agent Tax Filing System", layout="wide")
st.title("🧾 AI-Powered Multi-Agent Tax Filing Assistant")
st.caption("Automating Traditional CA Process • Extraction • Reconciliation • Calculation • Filing (Powered by Groq)")

# -------------------------------------------------------------
# 📋 Traditional CA Workflow (Now Automated)
# -------------------------------------------------------------
with st.expander("📖 How This System Automates Traditional CA Process"):
    st.markdown("""
    ### Traditional CA Process (9 Steps) - Now Fully Automated:
    
    1. **Collect Documents** → ✅ Automated document upload & processing
    2. **Extract Salary Components** (Form 16 + Payslip) → ✅ Enhanced extraction with Basic, HRA, LTA, etc.
    3. **Extract Additional Incomes** (Bank Statement & AIS) → ✅ Interest, rental, capital gains extraction
    4. **Reconcile TDS** (Form 16 vs 26AS vs AIS) → ✅ **Handled in Consolidation step**
    5. **Calculate Gross Total Income** → ✅ Automated calculation
    6. **Apply Deductions** (Chapter VI-A: 80C, 80D, 80CCD, etc.) → ✅ Enhanced with proper caps
    7. **Apply Exemptions** (HRA, LTA, Children Education) → ✅ **NEW: Proper HRA/LTA calculation matching CA method**
    8. **Compute Tax** (Old vs New Regime) → ✅ Enhanced with proper rebate 87A
    9. **Final Filing Preparation** → ✅ ITR JSON generation & PDF reports
    
    **This system replicates the exact manual calculation process that CAs do!**
    """)

# -------------------------------------------------------------
# 📤 MULTI-FILE UPLOAD
# -------------------------------------------------------------
uploaded_files = st.file_uploader(
    "📂 Upload Multiple Financial Documents (PDF / CSV / Image)",
    type=["pdf", "csv", "jpg", "png"],
    accept_multiple_files=True
)

if not uploaded_files:
    st.warning("Upload at least one document to continue.")
    st.stop()

st.markdown("### 🔐 Enter password for each PDF (if protected)")
password_map = {}

for file in uploaded_files:
    if file.name.lower().endswith(".pdf"):
        pwd = st.text_input(
            f"Password for **{file.name}**",
            type="password",
            key=file.name
        )
        password_map[file.name] = pwd or None

st.markdown("---")

document_agent = DocumentAgent()


# -------------------------------------------------------------
# 🧠 STEP 1 — Extract & Analyze All Documents
# -------------------------------------------------------------
if st.button("🧠 Extract & Analyze All Documents"):
    with st.spinner("Processing all uploaded documents..."):
        try:
            uploads_dir = "data/uploads"
            os.makedirs(uploads_dir, exist_ok=True)

            extracted_docs = []

            for file in uploaded_files:
                temp_path = os.path.join(uploads_dir, file.name)
                with open(temp_path, "wb") as f:
                    f.write(file.getbuffer())

                pwd = password_map.get(file.name)

                result = document_agent.process(temp_path, pwd, debug=False)  # Disable debug to exclude raw text

                # Remove raw_text from display
                if "raw_text" in result:
                    del result["raw_text"]

                extracted_docs.append(result)

            # -------------------------------
            # SAFE FIX: avoid index out of range
            # -------------------------------
            if not extracted_docs:
                raise ValueError("No files were extracted. All failed.")

            valid_docs = [d for d in extracted_docs if d.get("structured_data")]

            if not valid_docs:
                raise ValueError("Extraction succeeded but no structured data was found.")

            st.session_state["all_extracted"] = extracted_docs
            st.session_state["extracted_docs_for_consolidation"] = extracted_docs
            st.session_state["calc_result"] = valid_docs[-1]

            st.success("✅ All documents processed successfully!")
            st.subheader("📊 Extracted & Structured Data")
            st.json(extracted_docs)

        except Exception as e:
            st.error(f"❌ Extraction failed: {e}")
            st.stop()


# -------------------------------------------------------------
# 🧩 STEP 1.5 — Consolidation (Traditional CA Step 1-2)
# -------------------------------------------------------------
if st.button("🧩 Consolidate Extracted Documents"):
    with st.spinner("Combining extracted data (CA Step 1-2)..."):
        try:
            # Check for extracted documents (try both keys for backward compatibility)
            extracted_docs = st.session_state.get("extracted_docs_for_consolidation") or st.session_state.get("all_extracted")
            if not extracted_docs:
                raise ValueError("⚠️ Extract documents first.")
            
            cons = ConsolidationAgent()
            consolidated = cons.process(extracted_docs)

            st.session_state["calc_result"] = consolidated

            st.success("✅ Consolidation complete!")
            st.json(consolidated)

        except Exception as e:
            st.error(f"❌ Consolidation error: {e}")


# -------------------------------------------------------------
# 🧮 STEP 1.6 — Tax Calculation (Traditional CA Step 5-8)
# -------------------------------------------------------------
if st.button("🧮 Calculate Tax (CA Step 5-8)"):
    with st.spinner("Computing tax using traditional CA method..."):
        try:
            if "calc_result" not in st.session_state:
                raise ValueError("⚠️ Extract or consolidate documents first.")
            
            calc_agent = CalculationAgent()
            calc_result = st.session_state["calc_result"]
            
            calculation = calc_agent.process(calc_result, is_metro=False)
            st.session_state["calculation"] = calculation
            st.session_state["calc_result"] = calculation  # Update for filing agent

            st.success("✅ Tax calculation complete!")
            
            st.subheader("📊 Tax Computation Summary")
            tax_comp = calculation.get("calculation", {})
            st.write(f"**Gross Total Income:** ₹{tax_comp.get('breakdown', {}).get('gross_total_income', 0):,.2f}")
            st.write(f"**Taxable Income (Old):** ₹{tax_comp.get('taxable_old', 0):,.2f}")
            st.write(f"**Taxable Income (New):** ₹{tax_comp.get('taxable_new', 0):,.2f}")
            st.write(f"**Tax (Old Regime):** ₹{tax_comp.get('tax_old', 0):,.2f}")
            st.write(f"**Tax (New Regime):** ₹{tax_comp.get('tax_new', 0):,.2f}")
            st.write(f"**Chosen Regime:** {tax_comp.get('chosen_regime', 'N/A').upper()}")
            st.write(f"**Final Tax:** ₹{tax_comp.get('chosen_tax', 0):,.2f}")
            st.write(f"**TDS:** ₹{tax_comp.get('breakdown', {}).get('tds_total', 0):,.2f}")
            st.write(f"**Tax Due/Refund:** ₹{tax_comp.get('tax_due', 0):,.2f} / ₹{tax_comp.get('refund', 0):,.2f}")
            
            st.subheader("📋 Detailed Breakdown")
            # Remove zero values for cleaner display
            def remove_zeros(obj):
                if isinstance(obj, dict):
                    return {k: remove_zeros(v) for k, v in obj.items() 
                           if v not in (0, 0.0, None, "") and not (isinstance(v, (dict, list)) and len(v) == 0)}
                elif isinstance(obj, list):
                    return [remove_zeros(item) for item in obj 
                           if item not in (0, 0.0, None, "") and not (isinstance(item, (dict, list)) and len(item) == 0)]
                return obj
            cleaned_calculation = remove_zeros(calculation)
            st.json(cleaned_calculation)

        except Exception as e:
            st.error(f"❌ Tax calculation error: {e}")


# -------------------------------------------------------------
# 📊 STEP 2 — Filing Summary
# -------------------------------------------------------------
if st.button("📊 Generate Tax Filing Summary"):
    with st.spinner("Generating ITR JSON and Filing Summary..."):
        try:
            if "calc_result" not in st.session_state:
                raise ValueError("⚠️ Extract or consolidate documents first.")

            filing_agent = FilingAgent()
            calc_result = st.session_state["calc_result"]

            filing_output = filing_agent.process(calc_result)
            st.session_state["filing_output"] = filing_output

            st.success("✅ Filing Summary Generated!")

            st.subheader("📄 ITR Form Selection")
            st.write("Form:", filing_output["itr_form"]["itr_form"])
            st.write("Reason:", filing_output["itr_form"]["reason"])

            st.subheader("📋 ITR JSON")
            st.json(filing_output["itr_json"])

            st.subheader("📋 AI Review Findings")
            st.json(filing_output["review"])

        except Exception as e:
            st.error(f"❌ Filing summary failed: {e}")


# -------------------------------------------------------------
# 🧠 STEP 3 — Final AI Report (PDF)
# -------------------------------------------------------------
if st.button("🧾 Generate Final AI Tax Report"):
    with st.spinner("Creating final CA-style report..."):
        try:
            if "filing_output" not in st.session_state:
                raise ValueError("⚠️ Generate filing summary first.")

            filing_output = st.session_state["filing_output"]

            # Get ITR JSON directly from filing output
            itr_json = filing_output.get("itr_json", {})

            # -------------------------------------------------
            # FIX REVIEW FORMAT (avoids list index out of range)
            # -------------------------------------------------
            review = filing_output.get("review", {})

            if isinstance(review, list):
                review = {
                    "review_findings": review,
                    "filing_advice": [],
                    "optimization": []
                }

            review.setdefault("review_findings", [])
            review.setdefault("filing_advice", [])
            review.setdefault("optimization", [])

            # Decision Agent
            decision_agent = DecisionAgent()
            advisory_output = decision_agent.process(itr_json, review)

            advisory = advisory_output["advisory"]

            # Report Agent
            report_agent = ReportAgent()
            pdf_bytes = report_agent.process(itr_json, review, advisory)

            st.success("🎉 Final AI Tax Report Generated!")

            pan = itr_json.get("taxpayer", {}).get("pan", "UNKNOWN")
            filename = f"Final_Report_{pan}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            
            st.download_button(
                "⬇️ Download Final AI Tax Report PDF",
                pdf_bytes,
                file_name=filename,
                mime="application/pdf",
            )

            st.subheader("💡 Advisory Insights")
            st.json(advisory)

        except Exception as e:
            st.error(f"❌ Failed to generate final report: {e}")


# -------------------------------------------------------------
# 💬 STEP 7 — RAG Q&A Agent (Ask Questions About Your Tax Filing)
# -------------------------------------------------------------
st.markdown("---")
st.header("💬 Ask Questions About Your Tax Filing")

# Initialize RAG agent
if "rag_agent" not in st.session_state:
    try:
        st.session_state["rag_agent"] = RAGAgent()
        st.session_state["rag_documents_added"] = False
    except Exception as e:
        st.warning(f"⚠️ RAG Agent not available: {e}")

# Add documents to RAG when extraction is complete
if "extracted_docs_for_consolidation" in st.session_state and not st.session_state.get("rag_documents_added", False):
    try:
        rag_agent = st.session_state.get("rag_agent")
        if rag_agent:
            extracted_docs = st.session_state["extracted_docs_for_consolidation"]
            rag_agent.add_documents(extracted_docs)
            st.session_state["rag_documents_added"] = True
            st.success("✅ Documents indexed for Q&A")
    except Exception as e:
        st.warning(f"⚠️ Failed to index documents: {e}")

# Add consolidated data when available
if "calc_result" in st.session_state and st.session_state.get("rag_documents_added", False):
    try:
        calc_result = st.session_state["calc_result"]
        if isinstance(calc_result, dict) and "consolidated" in calc_result:
            rag_agent = st.session_state.get("rag_agent")
            if rag_agent:
                rag_agent.add_consolidated_data(calc_result["consolidated"])
    except Exception:
        pass

# Q&A Interface
rag_agent = st.session_state.get("rag_agent")
if rag_agent:
    # Display index stats
    stats = rag_agent.get_index_stats()
    if stats["total_documents"] > 0:
        st.info(f"📚 {stats['total_documents']} documents indexed and ready for questions")
    
    # Example questions (show first so selection happens before text input)
    st.subheader("💡 Example Questions")
    example_questions = [
        "What is my total income?",
        "How much TDS was deducted?",
        "What deductions are available?",
        "What is my gross salary?",
        "How much interest income do I have?",
        "What is my taxable income?",
        "Can I claim home loan interest deduction?",
    ]
    
    # Initialize selected_example if not exists
    if "selected_example" not in st.session_state:
        st.session_state["selected_example"] = ""
    
    # Handle example button clicks
    cols = st.columns(3)
    for i, example in enumerate(example_questions):
        with cols[i % 3]:
            if st.button(example, key=f"example_{i}"):
                st.session_state["selected_example"] = example
                st.rerun()
    
    # Question input (use selected_example as value)
    question = st.text_input(
        "Ask a question about your tax filing:",
        value=st.session_state.get("selected_example", ""),
        placeholder="e.g., What is my gross salary? How much TDS was deducted? What deductions can I claim?",
        key="rag_question"
    )
    
    # Clear selected_example after it's been used
    if st.session_state.get("selected_example"):
        st.session_state["selected_example"] = ""
    
    if st.button("🔍 Ask", key="rag_ask_button"):
        if question:
            with st.spinner("Searching documents and generating answer..."):
                try:
                    result = rag_agent.query(question, n_results=3)
                    
                    st.subheader("💡 Answer")
                    st.write(result["answer"])
                    
                    if result["sources"]:
                        st.subheader("📄 Sources")
                        for i, source in enumerate(result["sources"], 1):
                            with st.expander(f"Source {i}: {source['document_type']} (Relevance: {source['relevance_score']:.3f})"):
                                st.write(f"**PAN:** {source['pan']}")
                                st.write(f"**Relevance Score:** {source['relevance_score']:.3f}")
                    
                    # Show context in expander
                    if result.get("context"):
                        with st.expander("🔍 View Retrieved Context"):
                            st.text(result["context"])
                            
                except Exception as e:
                    st.error(f"❌ Failed to answer question: {e}")
        else:
            st.warning("Please enter a question")
else:
    st.info("📚 Upload and extract documents first to enable Q&A functionality")


# -------------------------------------------------------------
# Footer
# -------------------------------------------------------------
st.markdown("---")
st.caption("🚀 Built using Groq • OCR • Multi-Agent AI • Automated Tax Filing System • RAG with FAISS")
