"""
LLM client abstraction for Anthropic, OpenAI, and Google Gemini.

Usage:
    from llm import LLM

    # Simple completion
    claude = LLM(provider="anthropic", model="claude-sonnet-4-20250514")
    response = claude.complete("Summarize this text: ...")

    # Chat with messages
    gpt = LLM(provider="openai", model="gpt-4o")
    response = gpt.chat([
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"}
    ])

    # Gemini
    gemini = LLM(provider="gemini", model="gemini-2.0-flash")
    response = gemini.complete("Explain quantum computing")

    # With custom parameters
    response = claude.complete("Write a haiku", temperature=0.9, max_tokens=100)
"""

import os
from dataclasses import dataclass, field
from typing import Optional, Literal

# Lazy imports
anthropic = None
openai = None
genai = None


def get_anthropic():
    global anthropic
    if anthropic is None:
        import anthropic as anthropic_module
        anthropic = anthropic_module
    return anthropic


def get_openai():
    global openai
    if openai is None:
        import openai as openai_module
        openai = openai_module
    return openai


def get_genai():
    global genai
    if genai is None:
        from google import genai as genai_module
        genai = genai_module
    return genai


Provider = Literal["anthropic", "openai", "gemini"]


@dataclass
class LLM:
    """
    LLM client that abstracts provider details.

    Each instance is configured for a specific provider/model combination.
    """
    provider: Provider
    model: str
    api_key: Optional[str] = None
    default_max_tokens: int = 4096
    default_temperature: float = 0.0

    # Clients are created lazily
    _client: object = field(default=None, init=False, repr=False)

    def __post_init__(self):
        if self.provider not in ("anthropic", "openai", "gemini"):
            raise ValueError(f"Unsupported provider: {self.provider}")

    @property
    def client(self):
        """Lazily initialize the client."""
        if self._client is None:
            if self.provider == "anthropic":
                mod = get_anthropic()
                self._client = mod.Anthropic(
                    api_key=self.api_key or os.environ.get("ANTHROPIC_API_KEY")
                )
            elif self.provider == "openai":
                mod = get_openai()
                self._client = mod.OpenAI(
                    api_key=self.api_key or os.environ.get("OPENAI_API_KEY")
                )
            elif self.provider == "gemini":
                mod = get_genai()
                self._client = mod.Client(
                    api_key=self.api_key or os.environ.get("GOOGLE_API_KEY")
                )
        return self._client

    def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """
        Simple text completion.

        Args:
            prompt: The user prompt
            system: Optional system prompt
            max_tokens: Max tokens to generate
            temperature: Sampling temperature

        Returns:
            The model's response text
        """
        messages = [{"role": "user", "content": prompt}]
        return self.chat(
            messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def chat(
        self,
        messages: list[dict],
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """
        Chat completion with messages array.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system: Optional system prompt (overrides any in messages)
            max_tokens: Max tokens to generate
            temperature: Sampling temperature

        Returns:
            The model's response text
        """
        max_tokens = max_tokens or self.default_max_tokens
        temperature = temperature if temperature is not None else self.default_temperature

        if self.provider == "anthropic":
            return self._chat_anthropic(messages, system, max_tokens, temperature)
        elif self.provider == "openai":
            return self._chat_openai(messages, system, max_tokens, temperature)
        elif self.provider == "gemini":
            return self._chat_gemini(messages, system, max_tokens, temperature)

    def _chat_anthropic(
        self,
        messages: list[dict],
        system: Optional[str],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Call Anthropic API."""
        # Extract system from messages if not provided
        if system is None:
            for msg in messages:
                if msg["role"] == "system":
                    system = msg["content"]
                    break

        # Filter out system messages (Anthropic takes system separately)
        filtered_messages = [m for m in messages if m["role"] != "system"]

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": filtered_messages,
        }
        if system:
            kwargs["system"] = system
        if temperature > 0:
            kwargs["temperature"] = temperature

        response = self.client.messages.create(**kwargs)
        return response.content[0].text

    def _chat_openai(
        self,
        messages: list[dict],
        system: Optional[str],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Call OpenAI API."""
        # Prepend system message if provided and not in messages
        has_system = any(m["role"] == "system" for m in messages)
        if system and not has_system:
            messages = [{"role": "system", "content": system}] + messages

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content

    def _chat_gemini(
        self,
        messages: list[dict],
        system: Optional[str],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Call Google Gemini API (using google-genai SDK)."""
        # Extract system instruction
        if system is None:
            for msg in messages:
                if msg["role"] == "system":
                    system = msg["content"]
                    break

        # Convert messages to Gemini format
        # Gemini uses "user" and "model" roles
        contents = []
        for msg in messages:
            if msg["role"] == "system":
                continue
            role = "model" if msg["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})

        # Build config
        config = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            config["system_instruction"] = system

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )

        return response.text
