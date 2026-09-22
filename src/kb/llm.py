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

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
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


Provider = Literal["anthropic", "openai", "gemini", "zai", "codex"]

# z.ai (Zhipu GLM) exposes an OpenAI-compatible endpoint.
ZAI_DEFAULT_BASE_URL = "https://api.z.ai/api/paas/v4"


def get_embeddings(texts: list[str], model: str = "gemini-embedding-001",
                    task_type: str = "RETRIEVAL_DOCUMENT") -> tuple[list[list[float]], dict]:
    """Get embeddings from Gemini. Always uses Gemini regardless of app LLM provider.

    Args:
        texts: List of strings to embed.
        model: Gemini embedding model name.
        task_type: One of RETRIEVAL_DOCUMENT, RETRIEVAL_QUERY, SEMANTIC_SIMILARITY, etc.

    Returns (embeddings, usage) where usage has token counts when available.
    """
    mod = get_genai()
    client = mod.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    result = client.models.embed_content(
        model=model,
        contents=texts,
        config={"task_type": task_type, "output_dimensionality": 1536},
    )
    usage = {}
    if hasattr(result, "metadata") and result.metadata:
        usage = {"billable_characters": getattr(result.metadata, "billable_character_count", 0)}
    return [e.values for e in result.embeddings], usage


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
    thinking_level: Optional[str] = None  # Gemini 3 only: MINIMAL, LOW, MEDIUM, HIGH
    reasoning_effort: Optional[str] = None  # Codex: low, medium, high, ...

    # Clients are created lazily
    _client: object = field(default=None, init=False, repr=False)
    last_usage: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self):
        if self.provider not in ("anthropic", "openai", "gemini", "zai", "codex"):
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
            elif self.provider == "zai":
                mod = get_openai()
                self._client = mod.OpenAI(
                    api_key=self.api_key or os.environ.get("ZAI_API_KEY"),
                    base_url=os.environ.get("ZAI_BASE_URL", ZAI_DEFAULT_BASE_URL),
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
        elif self.provider in ("openai", "zai"):
            return self._chat_openai(messages, system, max_tokens, temperature)
        elif self.provider == "gemini":
            return self._chat_gemini(messages, system, max_tokens, temperature)
        elif self.provider == "codex":
            raise NotImplementedError("Codex provider currently supports structured JSON analysis only")

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

        with self.client.messages.stream(**kwargs) as stream:
            response = stream.get_final_message()
        # Models with thinking on (Fable 5, adaptive-thinking Opus/Sonnet) return
        # a ThinkingBlock before the text — pick the first text block, not [0].
        text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
        if text is None:
            text = next((b.text for b in response.content if hasattr(b, "text")), "")
        return text

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
        is_gemini3 = "gemini-3" in self.model
        config = {
            "max_output_tokens": max_tokens,
            "temperature": 1.0 if is_gemini3 else temperature,
        }
        if is_gemini3:
            from google.genai import types as _gtypes
            level = self.thinking_level or "MINIMAL"
            config["thinking_config"] = _gtypes.ThinkingConfig(thinking_level=level)
            config["top_p"] = 0.95
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
        elif self.provider in ("openai", "zai"):
            return self._structured_openai(prompt, response_model, system, max_tokens, temperature)
        elif self.provider == "gemini":
            return self._structured_gemini(prompt, response_model, system, max_tokens, temperature)
        elif self.provider == "codex":
            data = self._structured_json_codex(
                prompt, response_model.model_json_schema(), system, max_tokens, temperature
            )
            return response_model.model_validate(data)

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

        with self.client.messages.stream(**kwargs) as stream:
            response = stream.get_final_message()

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
        is_gemini3 = "gemini-3" in self.model
        config = {
            "max_output_tokens": max_tokens,
            "temperature": 1.0 if is_gemini3 else temperature,
            "response_mime_type": "application/json",
            "response_schema": response_model,
        }
        if is_gemini3:
            from google.genai import types as _gtypes
            config["thinking_config"] = _gtypes.ThinkingConfig(thinking_level="MINIMAL")
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
        elif self.provider in ("openai", "zai"):
            return self._structured_json_openai(prompt, schema, system, max_tokens, temperature)
        elif self.provider == "gemini":
            return self._structured_json_gemini(prompt, schema, system, max_tokens, temperature)
        elif self.provider == "codex":
            return self._structured_json_codex(prompt, schema, system, max_tokens, temperature)

    def _structured_json_codex(
        self,
        prompt: str,
        schema: dict,
        system: Optional[str],
        max_tokens: int,
        temperature: float,
    ) -> dict:
        """Run a schema-constrained Codex turn using the signed-in subscription."""
        del max_tokens, temperature  # Codex CLI owns these controls.
        codex_bin = self._codex_bin()
        schema = self._codex_output_schema(schema)
        full_prompt = prompt
        if system:
            full_prompt = f"{system}\n\n---\n\n{prompt}"
        full_prompt += "\n\nReturn only JSON matching the supplied output schema. Do not use tools."

        schema_path = output_path = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as schema_file:
                json.dump(schema, schema_file)
                schema_path = schema_file.name
            with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as output_file:
                output_path = output_file.name

            cmd = [
                codex_bin, "exec", "--skip-git-repo-check", "--ephemeral",
                "-s", "read-only", "--output-schema", schema_path,
                "-o", output_path, "-m", self.model,
            ]
            if self.reasoning_effort:
                cmd += ["-c", f"model_reasoning_effort={self.reasoning_effort}"]
            cmd += ["--", full_prompt]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=900, cwd="/tmp", check=False
            )
            if result.returncode:
                detail = (result.stderr or result.stdout or "unknown error")[-1000:]
                raise RuntimeError(f"Codex exited with status {result.returncode}: {detail}")
            raw = Path(output_path).read_text(encoding="utf-8").strip()
            data = json.loads(raw)
            self.last_usage = {}
            return data
        finally:
            if schema_path:
                Path(schema_path).unlink(missing_ok=True)
            if output_path:
                Path(output_path).unlink(missing_ok=True)

    @staticmethod
    def _codex_output_schema(schema: dict) -> dict:
        """Close every JSON object for Codex strict structured output.

        The pipeline's schemas were authored for Gemini, which permits object
        schemas without ``additionalProperties``. Codex requires that keyword
        to be explicitly false at every object level.
        """
        def close_objects(value):
            if isinstance(value, dict):
                result = {key: close_objects(item) for key, item in value.items()}
                if result.get("type") == "object" or "properties" in result:
                    result["additionalProperties"] = False
                    # Codex strict schemas require every declared property to
                    # appear in ``required``. Existing pipeline prompts already
                    # ask for complete records; make that contract explicit.
                    if "properties" in result:
                        result["required"] = list(result["properties"])
                return result
            if isinstance(value, list):
                return [close_objects(item) for item in value]
            return value

        return close_objects(schema)

    @staticmethod
    def _codex_bin() -> str:
        """Resolve Codex with zero extra config for a standard local install."""
        override = os.environ.get("CODEX_BIN")
        if override:
            return override
        user_install = Path.home() / ".local" / "bin" / "codex"
        if user_install.is_file():
            return str(user_install)
        discovered = shutil.which("codex")
        if discovered:
            return discovered
        raise RuntimeError(
            "Codex CLI not found. Install it as this worker user, then run "
            "`codex login` and retry."
        )

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

        with self.client.messages.stream(**kwargs) as stream:
            response = stream.get_final_message()

        self.last_usage = {
            "tokens_in": getattr(response.usage, "input_tokens", None),
            "tokens_out": getattr(response.usage, "output_tokens", None),
        }

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

        if hasattr(response, "usage") and response.usage:
            self.last_usage = {
                "tokens_in": response.usage.prompt_tokens,
                "tokens_out": response.usage.completion_tokens,
            }

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

        is_gemini3 = "gemini-3" in self.model
        config = {
            "max_output_tokens": max_tokens,
            "temperature": 1.0 if is_gemini3 else temperature,
            "response_mime_type": "application/json",
            "response_json_schema": clean_schema,
        }
        if is_gemini3:
            from google.genai import types as _gtypes
            config["thinking_config"] = _gtypes.ThinkingConfig(thinking_level="MINIMAL")
        if system:
            config["system_instruction"] = system

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )

        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            u = response.usage_metadata
            self.last_usage = {
                "tokens_in": getattr(u, 'prompt_token_count', None),
                "tokens_out": getattr(u, 'candidates_token_count', None),
            }

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
