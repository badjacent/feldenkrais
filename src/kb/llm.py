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
import json
from dataclasses import dataclass, field
from typing import Optional, Literal, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

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
                    api_key=self.api_key or os.environ.get("GOOGLE_API_KEY"),
                    http_options={"timeout": 600_000},  # 10min request timeout
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

        is_reasoning = any(self.model.startswith(p) for p in ("gpt-5", "o1", "o3"))

        kwargs = {
            "model": self.model,
            "messages": messages,
        }
        # Reasoning models don't support temperature and use max_completion_tokens
        if is_reasoning:
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = temperature

        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    def _chat_gemini(
        self,
        messages: list[dict],
        system: Optional[str],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Call Google Gemini API (using google-genai SDK)."""
        import time as _time

        # Extract system instruction
        if system is None:
            for msg in messages:
                if msg["role"] == "system":
                    system = msg["content"]
                    break

        # Convert messages to Gemini format
        # Gemini uses "user" and "model" roles
        contents = []
        total_chars = 0
        for msg in messages:
            if msg["role"] == "system":
                continue
            role = "model" if msg["role"] == "assistant" else "user"
            total_chars += len(msg["content"])
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})

        # Build config
        config = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            config["system_instruction"] = system
            total_chars += len(system)

        t0 = _time.time()
        print(f"[llm] Gemini request: model={self.model}, input_chars={total_chars}, max_tokens={max_tokens}", flush=True)

        # Dump full request to file for debugging
        try:
            import datetime, pathlib
            dump_dir = pathlib.Path("/tmp/gemini_requests")
            dump_dir.mkdir(exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            dump_file = dump_dir / f"{ts}.json"
            dump_file.write_text(json.dumps({
                "model": self.model,
                "system": system,
                "contents": contents,
                "config": {k: str(v) for k, v in config.items()},
            }, indent=2, default=str))
            print(f"[llm] Request dumped to {dump_file}", flush=True)
        except Exception:
            pass

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )

        elapsed = _time.time() - t0
        finish_reason = None
        if response.candidates:
            finish_reason = response.candidates[0].finish_reason
        usage = ""
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            u = response.usage_metadata
            usage = f", tokens_in={getattr(u, 'prompt_token_count', '?')}, tokens_out={getattr(u, 'candidates_token_count', '?')}"
        print(f"[llm] Gemini response: {elapsed:.1f}s, finish={finish_reason}{usage}", flush=True)

        return response.text

    def complete_structured(
        self,
        prompt: str,
        response_model: Type[T],
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> T:
        """
        Get a structured response matching a Pydantic model.

        Args:
            prompt: The user prompt
            response_model: Pydantic model class to parse response into
            system: Optional system prompt
            max_tokens: Max tokens to generate
            temperature: Sampling temperature

        Returns:
            An instance of response_model
        """
        max_tokens = max_tokens or self.default_max_tokens
        temperature = temperature if temperature is not None else self.default_temperature

        if self.provider == "anthropic":
            return self._structured_anthropic(prompt, response_model, system, max_tokens, temperature)
        elif self.provider == "openai":
            return self._structured_openai(prompt, response_model, system, max_tokens, temperature)
        elif self.provider == "gemini":
            return self._structured_gemini(prompt, response_model, system, max_tokens, temperature)

    def _structured_anthropic(
        self,
        prompt: str,
        response_model: Type[T],
        system: Optional[str],
        max_tokens: int,
        temperature: float,
    ) -> T:
        """Get structured output from Anthropic using tool_use."""
        schema = response_model.model_json_schema()

        tool = {
            "name": "output",
            "description": f"Output structured data as {response_model.__name__}",
            "input_schema": schema,
        }

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": "output"},
        }
        if system:
            kwargs["system"] = system
        if temperature > 0:
            kwargs["temperature"] = temperature

        response = self.client.messages.create(**kwargs)

        # Extract tool use input
        for block in response.content:
            if block.type == "tool_use":
                return response_model.model_validate(block.input)

        raise ValueError("No tool use in response")

    def _structured_openai(
        self,
        prompt: str,
        response_model: Type[T],
        system: Optional[str],
        max_tokens: int,
        temperature: float,
    ) -> T:
        """Get structured output from OpenAI using response_format."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = self.client.beta.chat.completions.parse(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_model,
        )

        return response.choices[0].message.parsed

    def _structured_gemini(
        self,
        prompt: str,
        response_model: Type[T],
        system: Optional[str],
        max_tokens: int,
        temperature: float,
    ) -> T:
        """Get structured output from Gemini using response_schema."""
        config = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
            "response_mime_type": "application/json",
            "response_schema": response_model,
        }
        if system:
            config["system_instruction"] = system

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )

        data = json.loads(response.text)
        return response_model.model_validate(data)

    def complete_structured_json(
        self,
        prompt: str,
        schema: dict,
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> dict:
        """
        Get a structured response matching a JSON Schema.

        Args:
            prompt: The user prompt
            schema: JSON Schema dict defining the expected structure
            system: Optional system prompt
            max_tokens: Max tokens to generate
            temperature: Sampling temperature

        Returns:
            A dict matching the schema
        """
        max_tokens = max_tokens or self.default_max_tokens
        temperature = temperature if temperature is not None else self.default_temperature

        if self.provider == "anthropic":
            return self._structured_json_anthropic(prompt, schema, system, max_tokens, temperature)
        elif self.provider == "openai":
            return self._structured_json_openai(prompt, schema, system, max_tokens, temperature)
        elif self.provider == "gemini":
            return self._structured_json_gemini(prompt, schema, system, max_tokens, temperature)

    def _structured_json_anthropic(
        self,
        prompt: str,
        schema: dict,
        system: Optional[str],
        max_tokens: int,
        temperature: float,
    ) -> dict:
        """Get structured output from Anthropic using tool_use with JSON Schema."""
        tool = {
            "name": "output",
            "description": schema.get("description", "Output structured data"),
            "input_schema": schema,
        }

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": "output"},
        }
        if system:
            kwargs["system"] = system
        if temperature > 0:
            kwargs["temperature"] = temperature

        response = self.client.messages.create(**kwargs)

        for block in response.content:
            if block.type == "tool_use":
                return block.input

        raise ValueError("No tool use in response")

    def _structured_json_openai(
        self,
        prompt: str,
        schema: dict,
        system: Optional[str],
        max_tokens: int,
        temperature: float,
    ) -> dict:
        """Get structured output from OpenAI using response_format with JSON Schema."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        is_reasoning = any(self.model.startswith(p) for p in ("gpt-5", "o1", "o3"))

        kwargs = {
            "model": self.model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.get("title", "output"),
                    "schema": schema,
                    "strict": False,
                }
            },
        }
        # Reasoning models don't support temperature and use max_completion_tokens
        if is_reasoning:
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = temperature

        response = self.client.chat.completions.create(**kwargs)
        raw_text = response.choices[0].message.content
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise ValueError(f"{e}\n\n--- Raw LLM output ---\n{raw_text}") from e

    def _structured_json_gemini(
        self,
        prompt: str,
        schema: dict,
        system: Optional[str],
        max_tokens: int,
        temperature: float,
    ) -> dict:
        """Get structured output from Gemini using response_json_schema."""
        # Strip fields Gemini doesn't accept in JSON Schema mode
        clean_schema = {k: v for k, v in schema.items() if k not in ("$schema", "$id")}

        config = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
            "response_mime_type": "application/json",
            "response_json_schema": clean_schema,
        }
        if system:
            config["system_instruction"] = system

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )

        # Check finish reason for truncation
        finish_reason = None
        if response.candidates:
            finish_reason = response.candidates[0].finish_reason

        raw_text = response.text
        try:
            result = json.loads(raw_text)
        except json.JSONDecodeError as e:
            extra = ""
            if finish_reason:
                extra = f"\nFinish reason: {finish_reason}"
            if response.candidates:
                c = response.candidates[0]
                extra += f"\nToken count: {getattr(c, 'token_count', 'unknown')}"
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                u = response.usage_metadata
                extra += f"\nUsage: prompt={getattr(u, 'prompt_token_count', '?')}, output={getattr(u, 'candidates_token_count', '?')}, total={getattr(u, 'total_token_count', '?')}"
            raise ValueError(f"{e}{extra}\n\n--- Raw LLM output ---\n{raw_text}") from e

        return result
