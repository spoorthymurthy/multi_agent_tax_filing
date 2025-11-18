# agents/ai_utils.py
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
    Robust wrapper around Groq chat.completions.create that:
      - retries on rate limits / transient errors
      - ensures a string result (never returns None or an object with empty .choices)
      - removes triple-backtick fences from the returned content
    Returns: string (possibly '{}') on failure.
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
            # defensive checks
            if not resp:
                raise Exception("Empty response from Groq API")
            choices = getattr(resp, "choices", None) or resp.get("choices") if isinstance(resp, dict) else getattr(resp, "choices", None)
            # normalize choices if resp is dict-like
            if choices is None:
                # try dict access
                try:
                    choices = resp["choices"]
                except Exception:
                    choices = None

            if not choices or len(choices) == 0:
                raise Exception("Groq response has no choices")

            # try to get message content robustly
            choice0 = choices[0]
            # choice0 might be dict-like or object
            content = None
            if isinstance(choice0, dict):
                content = (choice0.get("message") or {}).get("content") or choice0.get("text") or ""
            else:
                # object-like
                content = getattr(choice0, "message", None)
                if content:
                    content = getattr(content, "content", "") or content.get("content") if isinstance(content, dict) else content
                else:
                    content = getattr(choice0, "text", "")

            if content is None:
                content = ""

            # strip fences and return
            txt = str(content).strip()
            if txt.startswith("```"):
                txt = txt.replace("```json", "").replace("```", "").strip()
            return txt

        except GroqError as e:
            s = str(e)
            if "rate_limit" in s or "rate_limit_exceeded" in s or "tokens per day" in s:
                wait = backoff * (attempt + 1)
                time.sleep(wait)
                continue
            # other GroqError: try again shorter
            time.sleep(backoff)
        except Exception as e:
            # transient maybe; retry with backoff
            time.sleep(backoff)
    # If all retries fail, return safe fallback string
    return "{}"
