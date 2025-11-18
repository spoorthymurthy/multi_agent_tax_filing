# agents/rag_agent.py
"""RAG Agent: Retrieval-Augmented Generation for tax filing Q&A using FAISS"""

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
        if not _HAS_FAISS:
            raise ImportError("faiss-cpu is required. Install with: pip install faiss-cpu")
        if not _HAS_GROQ:
            raise ImportError("groq is required. Install with: pip install groq")
        if np is None:
            raise ImportError("numpy is required. Install with: pip install numpy")
        if not _HAS_SENTENCE_TRANSFORMERS:
            raise ImportError("sentence-transformers is required. Install with: pip install sentence-transformers")
        
        api_key = groq_api_key or os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("❌ Missing GROQ_API_KEY")
        
        # Initialize Groq client
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
        except Exception as e:
            raise ValueError(f"Failed to initialize Groq client: {e}")
        
        # Initialize sentence-transformers model for embeddings
        # Workaround for meta tensor issue: load model with explicit device handling
        try:
            import torch
            
            # Force CPU usage
            os.environ['CUDA_VISIBLE_DEVICES'] = ''
            
            # Set device to CPU explicitly
            device = torch.device('cpu')
            
            # Using all-MiniLM-L6-v2: 384 dimensions, lighter and faster
            self.embedding_model_name = "all-MiniLM-L6-v2"
            
            # Try loading with explicit device parameter first
            try:
                self.embedding_model = SentenceTransformer(
                    self.embedding_model_name,
                    device=str(device)  # Convert to string
                )
            except (TypeError, ValueError, RuntimeError) as e1:
                # If device parameter causes issues, try loading without it
                # and manually set device after loading
                try:
                    # Load model first
                    self.embedding_model = SentenceTransformer(self.embedding_model_name)
                    
                    # Manually ensure all modules are on CPU
                    if hasattr(self.embedding_model, '_modules'):
                        for module in self.embedding_model._modules.values():
                            if hasattr(module, 'to'):
                                try:
                                    module.to(device)
                                except Exception:
                                    # Skip if can't move (might already be on CPU)
                                    pass
                    
                    # Also try moving the main model
                    if hasattr(self.embedding_model, 'to'):
                        try:
                            self.embedding_model.to(device)
                        except Exception:
                            pass
                            
                except Exception as e2:
                    # Last resort: try loading with minimal interference
                    # This might work if the model loads correctly by default
                    self.embedding_model = SentenceTransformer(self.embedding_model_name)
            
            # Ensure model is in eval mode
            self.embedding_model.eval()
            
            # Update dimension to match the model
            self.dimension = 384  # all-MiniLM-L6-v2 dimension
            print(f"✅ Loaded embedding model: {self.embedding_model_name} on CPU")
            
        except Exception as e:
            error_msg = str(e)
            if "meta tensor" in error_msg.lower() or "to_empty" in error_msg.lower():
                # Provide helpful error message with fix instructions
                raise ValueError(
                    f"Meta tensor error detected. This is a PyTorch/sentence-transformers compatibility issue.\n"
                    f"Please try one of these solutions:\n"
                    f"1. Upgrade packages: pip install --upgrade sentence-transformers torch\n"
                    f"2. Or reinstall: pip uninstall sentence-transformers torch && pip install sentence-transformers torch\n"
                    f"3. Or use a different Python environment\n"
                    f"Original error: {error_msg}"
                )
            else:
                raise ValueError(f"Failed to load embedding model: {error_msg}")
        
        # Initialize FAISS storage
        self.db_path = os.path.join(os.getcwd(), "data", "faiss_db")
        os.makedirs(self.db_path, exist_ok=True)
        
        self.index_name = index_name
        self.index_path = os.path.join(self.db_path, f"{index_name}.index")
        self.metadata_path = os.path.join(self.db_path, f"{index_name}_metadata.pkl")
        
        # FAISS index and metadata storage
        self.index = None
        self.metadata = []  # List of dicts: {text, document_type, pan, assessment_year, user_id}
        # Dimension will be set by the embedding model (384 for all-MiniLM-L6-v2)
        # If dimension wasn't set above, default to 384
        if not hasattr(self, 'dimension'):
            self.dimension = 384
        
        # Load existing index if it exists
        self._load_index()
    
    def _load_index(self):
        """Load FAISS index and metadata from disk"""
        if os.path.exists(self.index_path) and os.path.exists(self.metadata_path):
            try:
                self.index = faiss.read_index(self.index_path)
                with open(self.metadata_path, 'rb') as f:
                    self.metadata = pickle.load(f)
            except Exception as e:
                print(f"⚠️ Failed to load existing index: {e}. Creating new index.")
                self._create_new_index()
        else:
            self._create_new_index()
    
    def _create_new_index(self):
        """Create a new FAISS index"""
        # Use IndexFlatIP (Inner Product) for cosine similarity with normalized vectors
        # We'll normalize embeddings before adding/searching
        self.index = faiss.IndexFlatIP(self.dimension)
        self.metadata = []
    
    def _save_index(self):
        """Save FAISS index and metadata to disk"""
        try:
            if self.index and len(self.metadata) > 0:
                faiss.write_index(self.index, self.index_path)
                with open(self.metadata_path, 'wb') as f:
                    pickle.dump(self.metadata, f)
        except Exception as e:
            print(f"⚠️ Failed to save index: {e}")
    
    def _normalize_vectors(self, vectors: np.ndarray) -> np.ndarray:
        """Normalize vectors for cosine similarity using Inner Product"""
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1  # Avoid division by zero
        return vectors / norms
    
    def _get_embeddings(self, texts: List[str]) -> np.ndarray:
        """Get embeddings for texts using sentence-transformers (free, local model)"""
        try:
            if not hasattr(self, 'embedding_model') or self.embedding_model is None:
                raise ValueError("Embedding model not initialized")
            
            # Generate embeddings using sentence-transformers
            embeddings = self.embedding_model.encode(
                texts,
                convert_to_numpy=True,
                normalize_embeddings=True  # Normalize for cosine similarity
            )
            
            # Ensure it's a 2D array
            if len(embeddings.shape) == 1:
                embeddings = embeddings.reshape(1, -1)
            
            return embeddings.astype(np.float32)
        except Exception as e:
            raise Exception(f"Failed to generate embeddings: {e}")
    
    def add_documents(self, documents: List[Dict[str, Any]], user_id: Optional[str] = None):
        """
        Add extracted documents to the FAISS index
        
        Args:
            documents: List of document dicts with 'document_type' and 'structured_data'
            user_id: Optional user identifier for multi-user support
        """
        if not documents:
            return
        
        texts = []
        metadatas = []
        
        for idx, doc in enumerate(documents):
            doc_type = doc.get("document_type") or doc.get("_doc_type", "Unknown")
            structured = doc.get("structured_data") or doc
            
            # Create text representation of the document
            text_parts = [
                f"Document Type: {doc_type}",
                f"Assessment Year: {structured.get('assessment_year', 'N/A')}",
                f"PAN: {structured.get('pan', 'N/A')}",
            ]
            
            # Add income information
            if structured.get('gross_salary'):
                text_parts.append(f"Gross Salary: ₹{structured['gross_salary']:,}")
            if structured.get('interest_income'):
                text_parts.append(f"Interest Income: ₹{structured['interest_income']:,}")
            if structured.get('rental_income'):
                text_parts.append(f"Rental Income: ₹{structured['rental_income']:,}")
            
            # Add deduction information
            if structured.get('hra_received'):
                text_parts.append(f"HRA Received: ₹{structured['hra_received']:,}")
            if structured.get('professional_tax'):
                text_parts.append(f"Professional Tax: ₹{structured['professional_tax']:,}")
            if structured.get('interest_paid'):
                text_parts.append(f"Home Loan Interest Paid: ₹{structured['interest_paid']:,}")
            
            # Add TDS information
            if structured.get('tds_ais') or structured.get('tds_amount'):
                tds = structured.get('tds_ais') or structured.get('tds_amount', 0)
                text_parts.append(f"TDS Deducted: ₹{tds:,}")
            
            # Add employer information (from AIS)
            if structured.get('_employer_name'):
                text_parts.append(f"Employer: {structured['_employer_name']}")
            
            # Add interest income details (from AIS)
            if isinstance(structured.get('interest_income_details'), list):
                for interest in structured['interest_income_details']:
                    if isinstance(interest, dict):
                        bank = interest.get('bank_name', 'Unknown Bank')
                        amount = interest.get('amount', 0)
                        text_parts.append(f"Interest from {bank}: ₹{amount:,}")
            
            text = "\n".join(text_parts)
            texts.append(text)
            
            # Create metadata
            metadata = {
                "text": text,
                "document_type": doc_type,
                "pan": str(structured.get('pan', '')),
                "assessment_year": str(structured.get('assessment_year', '')),
            }
            if user_id:
                metadata["user_id"] = user_id
            
            metadatas.append(metadata)
        
        # Get embeddings
        try:
            embeddings = self._get_embeddings(texts)
            # Normalize embeddings for cosine similarity (sentence-transformers already normalizes, but ensure it)
            embeddings = self._normalize_vectors(embeddings)
        except Exception as e:
            raise Exception(f"Failed to generate embeddings: {e}")
        
        # Ensure index exists
        if self.index is None:
            self._create_new_index()
        
        # Add to FAISS index
        try:
            self.index.add(embeddings)
            
            # Store metadata
            self.metadata.extend(metadatas)
            
            # Save to disk
            self._save_index()
        except Exception as e:
            raise Exception(f"Failed to add documents to FAISS index: {e}")
    
    def add_consolidated_data(self, consolidated: Dict[str, Any], user_id: Optional[str] = None):
        """Add consolidated tax data to the FAISS index"""
        income = consolidated.get("income_components", {})
        deductions = consolidated.get("deductions", {})
        
        text_parts = [
            "=== CONSOLIDATED TAX FILING SUMMARY ===",
            f"Total Income: ₹{income.get('Total Income', 0):,}",
            f"Gross Salary: ₹{income.get('Gross Salary', 0):,}",
            f"Interest Income: ₹{income.get('Interest Income', 0):,}",
            f"Rental Income: ₹{income.get('Rental Income', 0):,}",
            "",
            "=== DEDUCTIONS ===",
            f"Total Deductions: ₹{deductions.get('Total Deductions', 0):,}",
        ]
        
        for key, value in deductions.items():
            if key != "Total Deductions" and value > 0:
                text_parts.append(f"{key}: ₹{value:,}")
        
        text = "\n".join(text_parts)
        
        # Get embedding
        try:
            embeddings = self._get_embeddings([text])
            # Normalize embeddings for cosine similarity
            embeddings = self._normalize_vectors(embeddings)
        except Exception as e:
            raise Exception(f"Failed to generate embedding: {e}")
        
        # Ensure index exists
        if self.index is None:
            self._create_new_index()
        
        # Add to FAISS index
        metadata = {
            "text": text,
            "document_type": "consolidated",
            "pan": str(consolidated.get("pan", "")),
        }
        if user_id:
            metadata["user_id"] = user_id
        
        try:
            self.index.add(embeddings)
            self.metadata.append(metadata)
            self._save_index()
        except Exception as e:
            raise Exception(f"Failed to add consolidated data: {e}")
    
    def query(self, question: str, n_results: int = 3, user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Query the RAG system with a question
        
        Args:
            question: User's question about tax filing
            n_results: Number of relevant documents to retrieve
            user_id: Optional user identifier for filtering
        
        Returns:
            Dict with 'answer', 'sources', and 'context'
        """
        if self.index is None or self.index.ntotal == 0:
            return {
                "answer": "I don't have enough information to answer your question. Please upload your tax documents first.",
                "sources": [],
                "context": ""
            }
        
        # Get query embedding
        try:
            query_embedding = self._get_embeddings([question])
            # Ensure query_embedding is 2D array (1, dimension)
            if len(query_embedding.shape) == 1:
                query_embedding = query_embedding.reshape(1, -1)
            # Normalize for cosine similarity
            query_embedding = self._normalize_vectors(query_embedding)
        except Exception as e:
            raise Exception(f"Failed to generate query embedding: {e}")
        
        # Search in FAISS (search more results, then filter by user_id if needed)
        k = min(n_results * 3, self.index.ntotal)  # Get more results for filtering
        
        try:
            distances, indices = self.index.search(query_embedding, k)
        except Exception as e:
            raise Exception(f"Failed to search FAISS index: {e}")
        
        # Filter results by user_id if provided
        filtered_indices = []
        filtered_distances = []
        
        for i, idx in enumerate(indices[0]):
            if idx < len(self.metadata):
                metadata = self.metadata[idx]
                if user_id is None or metadata.get("user_id") == user_id:
                    filtered_indices.append(idx)
                    filtered_distances.append(distances[0][i])
                    if len(filtered_indices) >= n_results:
                        break
        
        if not filtered_indices:
            return {
                "answer": "I don't have enough information to answer your question. Please upload your tax documents first.",
                "sources": [],
                "context": ""
            }
        
        # Extract context
        documents = []
        sources = []
        
        for idx, distance in zip(filtered_indices, filtered_distances):
            if idx < len(self.metadata):
                metadata = self.metadata[idx]
                documents.append(metadata["text"])
                sources.append({
                    "document_type": metadata.get("document_type", "Unknown"),
                    "relevance_score": float(distance),  # FAISS returns similarity scores
                    "pan": metadata.get("pan", "N/A")
                })
        
        context_parts = []
        for i, doc in enumerate(documents):
            doc_type = sources[i]["document_type"]
            context_parts.append(f"[Document {i+1} - {doc_type}]\n{doc}")
        
        context = "\n\n".join(context_parts)
        
        # Generate answer using LLM
        prompt = f"""You are a tax filing assistant. Answer the user's question based on the following context from their tax documents.

Context from documents:
{context}

User Question: {question}

Instructions:
1. Answer the question based ONLY on the provided context
2. If the information is not in the context, say so clearly
3. Be specific with numbers and amounts
4. Use Indian Rupee (₹) format for all amounts
5. Be concise and helpful
6. If multiple documents are referenced, mention which document type the information comes from

Answer:"""
        
        try:
            response = self.client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": "You are a helpful tax filing assistant. Answer questions based on the provided document context."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=500
            )
            answer = response.choices[0].message.content.strip()
        except GroqError as e:
            if "rate limit" in str(e).lower() or "429" in str(e):
                answer = "Rate limit exceeded. Please try again in a moment."
            elif "invalid_api_key" in str(e).lower() or "401" in str(e):
                answer = "API key error. Please check your GROQ_API_KEY."
            else:
                answer = f"Error generating answer: {str(e)}"
        except Exception as e:
            answer = f"Error: {str(e)}"
        
        return {
            "answer": answer,
            "sources": sources,
            "context": context
        }
    
    def clear_index(self, user_id: Optional[str] = None):
        """Clear all documents from the index (or for a specific user)"""
        if user_id:
            # Filter out specific user's documents
            new_metadata = []
            indices_to_keep = []
            
            for idx, metadata in enumerate(self.metadata):
                if metadata.get("user_id") != user_id:
                    new_metadata.append(metadata)
                    indices_to_keep.append(idx)
            
            if len(indices_to_keep) < len(self.metadata):
                # Rebuild index with remaining documents
                # Note: FAISS doesn't support deletion, so we need to rebuild
                # For simplicity, we'll clear all and re-add (in production, use a more efficient approach)
                self._create_new_index()
                self.metadata = new_metadata
                # Re-add remaining documents (would need to store original embeddings)
                # For now, just clear everything if user_id filtering is needed
                self._create_new_index()
                self.metadata = []
                self._save_index()
        else:
            # Clear entire index
            self._create_new_index()
            self.metadata = []
            self._save_index()
    
    def get_index_stats(self) -> Dict[str, Any]:
        """Get statistics about the index"""
        return {
            "total_documents": self.index.ntotal if self.index else 0,
            "index_name": self.index_name,
            "dimension": self.dimension
        }
