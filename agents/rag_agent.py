# agents/rag_agent.py
"""RAG Agent for answering tax filing questions using document context with FAISS vector search"""
import os
import json
import pickle
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

# Optional dependencies
try:
    import faiss
    _HAS_FAISS = True
except ImportError:
    _HAS_FAISS = False

try:
    from groq import Groq, GroqError
    import numpy as np
    _HAS_GROQ = True
except ImportError:
    _HAS_GROQ = False
    np = None

try:
    from sentence_transformers import SentenceTransformer
    _HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    _HAS_SENTENCE_TRANSFORMERS = False

load_dotenv()


class RAGAgent:
    """RAG Agent for answering tax filing questions using document context with FAISS"""

    def __init__(self, groq_api_key: Optional[str] = None, index_name: str = "tax_filing_index"):
        # Validate required dependencies
        if not _HAS_FAISS:
            raise ImportError("faiss-cpu is required. Install with: pip install faiss-cpu")
        if not _HAS_GROQ:
            raise ImportError("groq is required. Install with: pip install groq")
        if np is None:
            raise ImportError("numpy is required. Install with: pip install numpy")
        if not _HAS_SENTENCE_TRANSFORMERS:
            raise ImportError("sentence-transformers is required. Install with: pip install sentence-transformers")

        # Initialize Groq client for AI responses
        api_key = groq_api_key or os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("❌ Missing GROQ_API_KEY")
        
        try:
            self.client = Groq(api_key=api_key)
        except TypeError as e:
            if "proxies" in str(e):
                import httpx
                self.client = Groq(api_key=api_key, http_client=httpx.Client())
            else:
                raise
        except Exception as e:
            raise ValueError(f"Failed to initialize Groq client: {e}")

        # Initialize sentence-transformers embedding model for vectorization
        try:
            import torch
            os.environ['CUDA_VISIBLE_DEVICES'] = ''
            device = torch.device('cpu')
            self.embedding_model_name = "all-MiniLM-L6-v2"
            
            try:
                self.embedding_model = SentenceTransformer(self.embedding_model_name, device=str(device))
            except Exception:
                self.embedding_model = SentenceTransformer(self.embedding_model_name)
                try:
                    self.embedding_model.to(device)
                except:
                    pass
            
            self.embedding_model.eval()
            self.dimension = 384
        except Exception as e:
            raise ValueError(f"Failed to load embedding model: {str(e)}")

        # Initialize FAISS index for vector search
        self.db_path = os.path.join(os.getcwd(), "data", "faiss_db")
        os.makedirs(self.db_path, exist_ok=True)
        
        self.index_name = index_name
        self.index_path = os.path.join(self.db_path, f"{index_name}.index")
        self.metadata_path = os.path.join(self.db_path, f"{index_name}_metadata.pkl")
        
        self.index = None
        self.metadata = []
        self._load_index()

    def _load_index(self):
        """Load existing FAISS index and metadata, or create new one."""
        if os.path.exists(self.index_path) and os.path.exists(self.metadata_path):
            try:
                self.index = faiss.read_index(self.index_path)
                with open(self.metadata_path, 'rb') as f:
                    self.metadata = pickle.load(f)
            except:
                self._create_new_index()
        else:
            self._create_new_index()

    def _create_new_index(self):
        """Create a new FAISS index (Inner Product for normalized vectors)."""
        self.index = faiss.IndexFlatIP(self.dimension)
        self.metadata = []

    def _save_index(self):
        """Save FAISS index and metadata to disk."""
        try:
            if self.index and len(self.metadata) > 0:
                faiss.write_index(self.index, self.index_path)
                with open(self.metadata_path, 'wb') as f:
                    pickle.dump(self.metadata, f)
        except:
            pass

    def _normalize_vectors(self, vectors: np.ndarray) -> np.ndarray:
        """Normalize vectors for cosine similarity (Inner Product)."""
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return vectors / norms

    def _get_embeddings(self, texts: List[str]) -> np.ndarray:
        """Generate embeddings for texts using sentence-transformers."""
        embeddings = self.embedding_model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        if len(embeddings.shape) == 1:
            embeddings = embeddings.reshape(1, -1)
        return embeddings.astype(np.float32)

    def add_documents(self, documents: List[Dict[str, Any]], user_id: Optional[str] = None):
        """Add documents to FAISS index for retrieval."""
        if not documents:
            return

        texts = []
        metadatas = []

        # Process each document and create searchable text
        for doc in documents:
            doc_type = doc.get("document_type") or doc.get("_doc_type", "Unknown")
            structured = doc.get("structured_data") or doc

            text_parts = [
                f"Type: {doc_type}",
                f"AY: {structured.get('assessment_year', 'N/A')}",
                f"PAN: {structured.get('pan', 'N/A')}",
            ]

            if structured.get('gross_salary'):
                text_parts.append(f"Gross Salary: {structured['gross_salary']}")
            if structured.get('interest_income'):
                text_parts.append(f"Interest: {structured['interest_income']}")
            if structured.get('tds_amount') or structured.get('tds_ais'):
                tds = structured.get("tds_amount") or structured.get("tds_ais")
                text_parts.append(f"TDS: {tds}")

            text = "\n".join(text_parts)
            texts.append(text)

            metadata = {
                "text": text,
                "document_type": doc_type,
                "pan": structured.get("pan"),
                "assessment_year": structured.get("assessment_year"),
            }
            metadatas.append(metadata)

        # Generate embeddings and add to index
        embeddings = self._normalize_vectors(self._get_embeddings(texts))

        if self.index is None:
            self._create_new_index()

        self.index.add(embeddings)
        self.metadata.extend(metadatas)
        self._save_index()

    def add_consolidated_data(self, consolidated: Dict[str, Any], user_id: Optional[str] = None):
        """Add consolidated tax data to index for context-aware answers."""
        income = consolidated.get("income_components", {})
        deductions = consolidated.get("deductions", {})

        text = "\n".join([
            f"Total Income: {income.get('Total Income', 0)}",
            f"Gross Salary: {income.get('Gross Salary', 0)}",
            f"Interest: {income.get('Interest Income', 0)}",
            f"Total Deductions: {deductions.get('Total Deductions', 0)}",
        ])

        embeddings = self._normalize_vectors(self._get_embeddings([text]))

        metadata = {
            "text": text,
            "document_type": "consolidated",
            "pan": consolidated.get("pan")
        }

        self.index.add(embeddings)
        self.metadata.append(metadata)
        self._save_index()

    def query(self, question: str, n_results: int = 3, user_id: Optional[str] = None, 
              consolidated_data: Optional[Dict[str, Any]] = None, 
              calc_result: Optional[Dict[str, Any]] = None):
        """
        Query RAG system with question, using vector search and AI generation.
        Falls back to direct AI answer if index is empty.
        """
        # If no index, try to answer using consolidated data directly
        if self.index is None or self.index.ntotal == 0:
            if consolidated_data or calc_result:
                return self._answer_without_index(question, consolidated_data, calc_result)
            return {
                "answer": "I don't have enough information yet. Please upload your tax documents.",
                "sources": [],
                "context": ""
            }

        # Vector search: Find similar documents
        query_emb = self._normalize_vectors(self._get_embeddings([question]))
        k = min(n_results * 3, self.index.ntotal)
        distances, indices = self.index.search(query_emb, k)

        # Extract context from top results
        final_idx = indices[0][:n_results]
        context_docs = []
        for idx in final_idx:
            if idx < len(self.metadata):
                context_docs.append(self.metadata[idx]["text"])

        context = "\n\n".join(context_docs)
        
        # Add consolidated data and calculation results to context
        additional_context = ""
        if consolidated_data:
            income = consolidated_data.get("income_components", {})
            deductions = consolidated_data.get("deductions", {})
            additional_context += f"\n\nCurrent Tax Data:\n"
            additional_context += f"Gross Salary: ₹{income.get('Gross Salary', 0):,.2f}\n"
            additional_context += f"Total Income: ₹{income.get('Total Income', 0):,.2f}\n"
            additional_context += f"Total Deductions: ₹{deductions.get('Total Deductions', 0):,.2f}\n"
            additional_context += f"TDS: ₹{consolidated_data.get('tds', 0):,.2f}\n"
        
        if calc_result:
            additional_context += f"\nTax Calculation:\n"
            additional_context += f"Tax (Old Regime): ₹{calc_result.get('tax_old', 0):,.2f}\n"
            additional_context += f"Tax (New Regime): ₹{calc_result.get('tax_new', 0):,.2f}\n"
            additional_context += f"Chosen Regime: {calc_result.get('chosen_regime', 'N/A').upper()}\n"
            additional_context += f"Tax Due: ₹{calc_result.get('tax_due', 0):,.2f}\n"
            additional_context += f"Refund: ₹{calc_result.get('refund', 0):,.2f}\n"

        # Generate AI answer using context
        prompt = f"""
You are a clear, friendly Chartered Accountant who explains tax matters in simple,
professional language — never too technical, never too informal.

You can answer questions about:
- Tax calculations and regimes
- Deductions eligibility
- Document requirements
- Filing guidance
- Investment suggestions
- Tax planning strategies

Use the information below ONLY for your own understanding.  
Do NOT mention "documents", "context", or how you know it.

Information for understanding:
{context}
{additional_context}

User's Question:
{question}

Your Goal:
Answer cleanly and confidently, like a CA speaking to a client.
- For calculation questions, show the math clearly
- For "what-if" questions, provide scenarios
- For deduction questions, explain eligibility and limits
- Give reasoning for every number in one or two lines
- Keep it friendly, simple, and accurate
- Use Indian Rupee formatting (₹)
- If asked about missing documents, suggest what to upload
- If asked about tax savings, provide actionable suggestions
- Do NOT list sources. Do NOT mention any document type.

Now give the final answer:
"""

        try:
            response = self.client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": "You are a friendly and clear Chartered Accountant."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.25,
                max_tokens=500
            )
            answer = response.choices[0].message.content.strip()
        except Exception as e:
            answer = f"Error generating answer: {str(e)}"

        return {
            "answer": answer,
            "sources": [],
            "context": ""
        }
    
    def _answer_without_index(self, question: str, consolidated_data: Optional[Dict[str, Any]] = None,
                              calc_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Answer questions using consolidated data even without index."""
        context = ""
        if consolidated_data:
            income = consolidated_data.get("income_components", {})
            deductions = consolidated_data.get("deductions", {})
            context += f"Taxpayer Data:\n"
            context += f"- Gross Salary: ₹{income.get('Gross Salary', 0):,.2f}\n"
            context += f"- Total Income: ₹{income.get('Total Income', 0):,.2f}\n"
            context += f"- Deductions: ₹{deductions.get('Total Deductions', 0):,.2f}\n"
        
        if calc_result:
            context += f"\nTax Calculation:\n"
            context += f"- Tax (Old): ₹{calc_result.get('tax_old', 0):,.2f}\n"
            context += f"- Tax (New): ₹{calc_result.get('tax_new', 0):,.2f}\n"
            context += f"- Chosen: {calc_result.get('chosen_regime', 'N/A')}\n"
        
        prompt = f"""You are a friendly tax advisor. Answer this question using the provided tax data.

{context}

Question: {question}

Provide a clear, helpful answer. If you need more information, ask for it politely.
"""
        
        try:
            response = self.client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": "You are a friendly and clear Chartered Accountant."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.25,
                max_tokens=500
            )
            answer = response.choices[0].message.content.strip()
        except Exception as e:
            answer = f"I can help answer your question, but I need more context. Please upload your tax documents first. Error: {str(e)}"
        
        return {
            "answer": answer,
            "sources": [],
            "context": ""
        }

    def clear_index(self, user_id: Optional[str] = None):
        """Clear the FAISS index and metadata."""
        self._create_new_index()
        self.metadata = []
        self._save_index()

    def get_index_stats(self):
        """Get statistics about the FAISS index."""
        return {
            "total_documents": self.index.ntotal if self.index else 0,
            "index_name": self.index_name,
            "dimension": self.dimension
        }
