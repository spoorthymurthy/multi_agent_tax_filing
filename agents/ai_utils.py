# agents/ai_utils.py
"""Utility functions for safe Groq API calls with retry logic"""
import time
import json
from typing import Optional
from groq import Groq, GroqError
import os
from dotenv import load_dotenv

load_dotenv()


def safe_groq_chat(api_key: Optional[str], messages, model: str = "llama-3.1-8b-instant",
                   temperature: float = 0.2, max_tokens: int = 1200, attempts: int = 3) -> str:
    """
    Robust wrapper around Groq chat.completions.create with retry logic.
    
    Features:
    - Retries on rate limits / transient errors
    - Ensures string result (never returns None)
    - Removes triple-backtick fences from JSON responses
    
    Returns: string (possibly '{}' on failure).
    """
    if not api_key:
        raise ValueError("Missing GROQ_API_KEY for safe_groq_chat")

    client = Groq(api_key=api_key)
    backoff = 5

    for attempt in range(attempts):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            
            # Defensive checks for response structure
            if not resp:
                raise Exception("Empty response from Groq API")
            
            # Extract choices (handle both dict and object responses)
            choices = getattr(resp, "choices", None)
            if choices is None and isinstance(resp, dict):
                choices = resp.get("choices")
            
            if not choices or len(choices) == 0:
                raise Exception("Groq response has no choices")

            # Extract message content (handle both dict and object)
            choice0 = choices[0]
            content = None
            
            if isinstance(choice0, dict):
                content = (choice0.get("message") or {}).get("content") or choice0.get("text") or ""
            else:
                # Object-like access
                content = getattr(choice0, "message", None)
                if content:
                    content = getattr(content, "content", "") or (content.get("content") if isinstance(content, dict) else content)
                else:
                    content = getattr(choice0, "text", "")

            if content is None:
                content = ""

            # Strip JSON code fences and return
            txt = str(content).strip()
            if txt.startswith("```"):
                txt = txt.replace("```json", "").replace("```", "").strip()
            return txt

        except GroqError as e:
            # Handle rate limiting with exponential backoff
            s = str(e)
            if "rate_limit" in s or "rate_limit_exceeded" in s or "tokens per day" in s:
                wait = backoff * (attempt + 1)
                time.sleep(wait)
                continue
            # Other GroqError: retry with shorter backoff
            time.sleep(backoff)
        except Exception as e:
            # Transient errors: retry with backoff
            time.sleep(backoff)
    
    # If all retries fail, return safe fallback
    return "{}"
