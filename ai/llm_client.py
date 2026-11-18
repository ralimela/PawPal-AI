"""LLM clients: a real Gemini client and a MockClient for offline tests.

Both have the same method, generate(prompt, json_mode=False) -> str, so the
rest of the AI layer doesn't care which one it is talking to.
"""

import os

from dotenv import load_dotenv

# Central place to change the model. Must be a name your API key can use.
DEFAULT_MODEL = "gemini-3.5-flash-lite"


class LLMError(Exception):
    """Raised when the model can't be reached or returns nothing usable."""


class GeminiClient:
    """Thin wrapper around the google-genai SDK."""

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        """Create the client; raise LLMError if there is no API key."""
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise LLMError("GEMINI_API_KEY is not set")
        from google import genai  # imported here so offline mode never needs it

        # Keep a reference to the client: if it's garbage collected, its
        # connection closes and later requests fail.
        self._client = genai.Client(api_key=api_key)
        self.model = model
        self.name = f"Gemini ({model})"

    def generate(self, prompt: str, json_mode: bool = False) -> str:
        """Send one prompt and return the text reply. Any failure becomes LLMError."""
        config = {"response_mime_type": "application/json"} if json_mode else None
        try:
            response = self._client.models.generate_content(
                model=self.model, contents=prompt, config=config
            )
        except Exception as err:  # network, quota, bad model name, ...
            raise LLMError(f"{type(err).__name__}: {err}") from err
        text = (response.text or "").strip()
        if not text:
            raise LLMError("the model returned an empty response")
        return text


class MockClient:
    """A fake LLM for tests and offline demos. Makes no network calls.

    script can be:
      - a list of replies, returned in order (one per call), or
      - a dict {substring: reply}: the reply for the first substring found in the prompt.
    A reply that is an Exception instance is raised instead of returned,
    which lets tests simulate API failures.
    """

    def __init__(self, script):
        """Store the scripted replies and start an empty call log."""
        self.script = script
        self.prompts: list[str] = []  # every prompt received, for assertions
        self.name = "MockClient"

    def generate(self, prompt: str, json_mode: bool = False) -> str:
        """Return the next scripted reply for this prompt."""
        self.prompts.append(prompt)
        if isinstance(self.script, dict):
            reply = next((r for key, r in self.script.items() if key in prompt), None)
            if reply is None:
                raise LLMError("MockClient has no scripted reply for this prompt")
        else:
            if len(self.prompts) > len(self.script):
                raise LLMError("MockClient ran out of scripted replies")
            reply = self.script[len(self.prompts) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply


def get_client() -> GeminiClient | None:
    """Return a GeminiClient if a key is configured, otherwise None (offline mode)."""
    load_dotenv()
    try:
        return GeminiClient()
    except LLMError:
        return None
