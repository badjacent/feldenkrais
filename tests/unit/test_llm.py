"""
Tests for LLM abstraction layer.

Covers Anthropic, OpenAI, and Gemini providers.
"""

import sys
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


@pytest.fixture
def llm_module():
    """Import the LLM module."""
    kb_dir = str(Path(__file__).resolve().parent.parent.parent / "src" / "kb")
    if kb_dir not in sys.path:
        sys.path.insert(0, kb_dir)
    from llm import LLM
    return LLM


class TestGetLLM:
    def test_returns_anthropic_provider(self, llm_module):
        """Should create an LLM instance with anthropic provider."""
        llm = llm_module(provider="anthropic", model="claude-sonnet-4-20250514")
        assert llm.provider == "anthropic"
        assert llm.model == "claude-sonnet-4-20250514"

    def test_returns_openai_provider(self, llm_module):
        llm = llm_module(provider="openai", model="gpt-4o")
        assert llm.provider == "openai"
        assert llm.model == "gpt-4o"

    def test_returns_gemini_provider(self, llm_module):
        llm = llm_module(provider="gemini", model="gemini-2.0-flash")
        assert llm.provider == "gemini"
        assert llm.model == "gemini-2.0-flash"

    def test_invalid_provider_raises(self, llm_module):
        """Should raise ValueError for unsupported provider."""
        with pytest.raises(ValueError, match="Unsupported provider"):
            llm_module(provider="invalid", model="test")


class TestCompleteStructuredJSON:
    def test_anthropic_structured_output(self, llm_module):
        """Should call Anthropic API and parse JSON response."""
        llm = llm_module(provider="anthropic", model="claude-sonnet-4-20250514", api_key="test-key")

        # Mock the anthropic client
        mock_block = MagicMock()
        mock_block.type = "tool_use"
        mock_block.input = {"result": "test_value"}

        mock_response = MagicMock()
        mock_response.content = [mock_block]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)

        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.get_final_message.return_value = mock_response

        mock_client = MagicMock()
        mock_client.messages.stream.return_value = mock_stream
        llm._client = mock_client

        schema = {"type": "object", "properties": {"result": {"type": "string"}}}
        result = llm.complete_structured_json("test prompt", schema)

        assert result == {"result": "test_value"}
        assert llm.last_usage["tokens_in"] == 100
        assert llm.last_usage["tokens_out"] == 50

    def test_openai_structured_output(self, llm_module):
        """Should call OpenAI API and parse JSON response."""
        llm = llm_module(provider="openai", model="gpt-4o", api_key="test-key")

        mock_message = MagicMock()
        mock_message.content = '{"result": "test_value"}'

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        llm._client = mock_client

        schema = {"type": "object", "properties": {"result": {"type": "string"}}}
        result = llm.complete_structured_json("test prompt", schema)

        assert result == {"result": "test_value"}

    def test_handles_truncated_output(self, llm_module):
        """Should handle models truncating long structured output.

        When the output JSON is truncated (incomplete), the provider-specific
        methods may raise a ValueError or return partial results. The exact
        behavior depends on the provider's response parsing."""
        llm = llm_module(provider="anthropic", model="claude-sonnet-4-20250514", api_key="test-key")

        # Simulate no tool_use block (Anthropic returns text instead of tool)
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = '{"partial": "data...'

        mock_response = MagicMock()
        mock_response.content = [mock_block]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=4096)

        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.get_final_message.return_value = mock_response

        mock_client = MagicMock()
        mock_client.messages.stream.return_value = mock_stream
        llm._client = mock_client

        schema = {"type": "object", "properties": {"result": {"type": "string"}}}
        with pytest.raises(ValueError, match="No tool use"):
            llm.complete_structured_json("test prompt", schema)


class TestGetEmbeddings:
    def test_returns_embedding_vector(self):
        """Should return list of floats."""
        kb_dir = str(Path(__file__).resolve().parent.parent.parent / "src" / "kb")
        if kb_dir not in sys.path:
            sys.path.insert(0, kb_dir)
        from llm import get_embeddings

        # Mock the genai client
        mock_embedding = MagicMock()
        mock_embedding.values = [0.1, 0.2, 0.3]

        mock_result = MagicMock()
        mock_result.embeddings = [mock_embedding]
        mock_result.metadata = None

        mock_client = MagicMock()
        mock_client.models.embed_content.return_value = mock_result

        with patch("llm.get_genai") as mock_get_genai:
            mock_mod = MagicMock()
            mock_mod.Client.return_value = mock_client
            mock_get_genai.return_value = mock_mod

            embeddings, usage = get_embeddings(["test text"])

        assert len(embeddings) == 1
        assert embeddings[0] == [0.1, 0.2, 0.3]

    def test_embedding_dimension(self):
        """Vector should have expected dimensionality (config requests 1536)."""
        kb_dir = str(Path(__file__).resolve().parent.parent.parent / "src" / "kb")
        if kb_dir not in sys.path:
            sys.path.insert(0, kb_dir)
        from llm import get_embeddings

        # Create a mock embedding with 1536 dimensions
        mock_embedding = MagicMock()
        mock_embedding.values = [0.0] * 1536

        mock_result = MagicMock()
        mock_result.embeddings = [mock_embedding]
        mock_result.metadata = MagicMock(billable_character_count=10)

        mock_client = MagicMock()
        mock_client.models.embed_content.return_value = mock_result

        with patch("llm.get_genai") as mock_get_genai:
            mock_mod = MagicMock()
            mock_mod.Client.return_value = mock_client
            mock_get_genai.return_value = mock_mod

            embeddings, usage = get_embeddings(["test text"])

        assert len(embeddings[0]) == 1536
        assert usage.get("billable_characters") == 10
