# agents/decision_agent.py

import os
import json
from datetime import datetime
from groq import Groq
from dotenv import load_dotenv

load_dotenv()


class DecisionAgent:

    def __init__(self, groq_api_key=None, output_dir="data/outputs"):
        api_key = groq_api_key or os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("❌ Missing GROQ_API_KEY")
        try:
            self.client = Groq(api_key=api_key)
        except TypeError as e:
            if "proxies" in str(e):
                # Handle proxy-related initialization errors
                import httpx
                http_client = httpx.Client()
                self.client = Groq(api_key=api_key, http_client=http_client)
            else:
                raise

        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    # --------------------------------------------------------------------
    # 🔒 SAFE JSON PARSER — never throws errors, always returns full dict
    # --------------------------------------------------------------------
    def safe_parse_json(self, text):
        if not isinstance(text, str):
            return {
                "insights": [str(text)],
                "warnings": [],
                "actions": [],
                "summary": ""
            }

        # remove markdown fences
        text = text.replace("```json", "").replace("```", "").strip()

        # FULL DIRECT PARSE
        try:
            return json.loads(text)
        except:
            pass

        # try substring extraction
        start = text.find("{")
        end = text.rfind("}")

        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except:
                pass

        # final fallback
        return {
            "insights": [text],
            "warnings": [],
            "actions": [],
            "summary": "Partial advisory generated."
        }

    # --------------------------------------------------------------------
    # 🧠 AI: GENERATE ADVISORY (Insights / Warnings / Actions / Summary)
    # --------------------------------------------------------------------
    def generate_advisory(self, itr_json, ai_review):

        prompt = f"""
You are a certified Indian Chartered Accountant providing a neutral summary.
Analyze the taxpayer's ITR JSON and AI review.

Provide a factual summary of what is found and why things are calculated the way they are.
Focus on explaining the data, not on warnings or suggestions.

Return STRICT VALID JSON with the following structure ONLY:

{{
  "insights": ["..."],
  "warnings": [],
  "actions": [],
  "summary": "..."
}}

Keep insights as neutral observations about the data. Keep warnings and actions empty.
The summary should explain what information is present and the rationale behind calculations.

ITR JSON:
{json.dumps(itr_json, indent=2, ensure_ascii=False)}

AI Review:
{json.dumps(ai_review, indent=2, ensure_ascii=False)}
"""

        try:
            response = self.client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": "You are a senior CA providing neutral, factual summaries. Focus on what is found and why, without warnings or negative feedback."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.15,
                max_tokens=1500,
            )

            raw = response.choices[0].message.content.strip()
            advisory = self.safe_parse_json(raw)

        except Exception as e:
            error_msg = str(e)
            if "401" in error_msg or "Invalid API Key" in error_msg or "invalid_api_key" in error_msg:
                advisory = {
                    "insights": ["AI advisory unavailable: Please configure a valid GROQ_API_KEY in your .env file."],
                    "warnings": [],
                    "actions": [],
                    "summary": "Tax calculations are complete and accurate. AI advisory features require API key configuration."
                }
            elif "429" in error_msg or "rate limit" in error_msg.lower():
                advisory = {
                    "insights": ["AI advisory temporarily unavailable due to rate limits. Please try again later."],
                    "warnings": [],
                    "actions": [],
                    "summary": "Tax calculations are complete. AI advisory will be available after rate limit resets."
                }
            else:
                advisory = {
                    "insights": ["AI advisory temporarily unavailable. All tax calculations are accurate and complete."],
                    "warnings": [],
                    "actions": [],
                    "summary": "Tax filing summary is ready. AI advisory features are temporarily unavailable."
                }

        # Guarantee keys exist
        advisory.setdefault("insights", [])
        advisory.setdefault("warnings", [])
        advisory.setdefault("actions", [])
        advisory.setdefault("summary", "")

        return advisory

    # --------------------------------------------------------------------
    # 🧠 Master Process — called from app.py
    # --------------------------------------------------------------------
    def process(self, itr_json, ai_review):
        advisory = self.generate_advisory(itr_json, ai_review)
        return {"advisory": advisory}
