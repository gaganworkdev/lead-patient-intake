import json
import logging
import re

import requests

from config import env

log = logging.getLogger("llm")


class LLMError(RuntimeError):
    pass


def extract_json(text):
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
        raise


class LLMClient:
    def __init__(self):
        self.provider = (env("LLM_PROVIDER") or "").lower()
        self.groq_key = env("GROQ_API_KEY")
        self.gemini_key = env("GEMINI_API_KEY") or env("GOOGLE_API_KEY")
        self.groq_model = env("GROQ_MODEL", "llama-3.3-70b-versatile")
        self.gemini_model = env("GEMINI_MODEL", "gemini-2.5-flash")
        self.ollama_host = env("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
        self.ollama_model = env("OLLAMA_MODEL", "llama3.2")
        if not self.provider:
            self.provider = self._detect()
        self._check()

    def _detect(self):
        if self.groq_key:
            return "groq"
        if self.gemini_key:
            return "gemini"
        if self._ollama_up():
            return "ollama"
        return ""

    def _ollama_up(self):
        try:
            resp = requests.get(f"{self.ollama_host}/api/tags", timeout=1.5)
            return resp.ok
        except requests.RequestException:
            return False

    def _check(self):
        if self.provider == "groq" and self.groq_key:
            return
        if self.provider == "gemini" and self.gemini_key:
            return
        if self.provider == "ollama" and self._ollama_up():
            return
        raise LLMError(
            "No LLM is configured. Set GROQ_API_KEY or GEMINI_API_KEY in .env, "
            "or start Ollama and set OLLAMA_MODEL. Free tiers are fine."
        )

    def describe(self):
        if self.provider == "groq":
            return f"groq/{self.groq_model}"
        if self.provider == "gemini":
            return f"gemini/{self.gemini_model}"
        if self.provider == "ollama":
            return f"ollama/{self.ollama_model}"
        return "unconfigured"

    def complete(self, system, user, temperature=0.3):
        last_error = None
        for attempt in range(1, 4):
            try:
                return self._once(system, user, temperature)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                log.warning("llm timeout attempt %s/3 (%s)", attempt, exc)
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                last_error = exc
                if status not in (429, 500, 502, 503, 504):
                    body = ""
                    if exc.response is not None:
                        body = exc.response.text[:300]
                    raise LLMError(f"llm http {status}: {body}") from exc
                log.warning("llm http %s attempt %s/3", status, attempt)
            if attempt < 3:
                import time

                time.sleep(1.2 * attempt)
        raise LLMError(f"llm gave up after retries: {last_error}")

    def complete_json(self, system, user, temperature=0.3):
        text = self.complete(system, user, temperature)
        try:
            data = extract_json(text)
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("llm returned non-json, asking once more")
            repair = self.complete(
                system,
                user + "\n\nYour last reply was not valid JSON. Return one JSON object only.",
                0.1,
            )
            try:
                data = extract_json(repair)
            except (json.JSONDecodeError, ValueError) as second:
                raise LLMError(f"could not parse json from the model: {second}") from exc
        if not isinstance(data, dict):
            raise LLMError("model json was not an object")
        return data

    def _once(self, system, user, temperature):
        if self.provider == "groq":
            return self._groq(system, user, temperature)
        if self.provider == "gemini":
            return self._gemini(system, user, temperature)
        if self.provider == "ollama":
            return self._ollama(system, user, temperature)
        raise LLMError("llm provider is not set")

    def _groq(self, system, user, temperature):
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.groq_key}"},
            json={
                "model": self.groq_model,
                "temperature": temperature,
                "max_tokens": 700,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=45,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def _gemini(self, system, user, temperature):
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.gemini_model}:generateContent"
        )
        resp = requests.post(
            url,
            params={"key": self.gemini_key},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": 700,
                    "responseMimeType": "application/json",
                },
            },
            timeout=45,
        )
        resp.raise_for_status()
        data = resp.json()
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(part.get("text", "") for part in parts)

    def _ollama(self, system, user, temperature):
        resp = requests.post(
            f"{self.ollama_host}/api/chat",
            json={
                "model": self.ollama_model,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "options": {"temperature": temperature, "num_predict": 420},
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]
