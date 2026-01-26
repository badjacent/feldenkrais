"""
Agent module: LLM-powered text processing pipeline.

Usage:
    from kb.llm import LLM
    from kb.agent import Agent

    # Simple agent with single LLM
    agent = Agent(llm=LLM(provider='anthropic', model='claude-sonnet-4-20250514'))
    response = agent.run("What is awareness through movement?")

    # Agent with separate transform LLM (cheap model for preprocessing)
    agent = Agent(
        llm=LLM(provider='anthropic', model='claude-sonnet-4-20250514'),
        transform_llm=LLM(provider='gemini', model='gemini-2.0-flash')
    )

    # Custom agent with override
    class MyAgent(Agent):
        def transform(self, user_input: str) -> str:
            # Custom transformation logic
            return self.transform_llm.complete(
                user_input,
                system="Rewrite as a detailed question."
            )
"""

from dataclasses import dataclass, field
from typing import Optional

try:
    from llm import LLM
except ImportError:
    from .llm import LLM


@dataclass
class Agent:
    """
    Agent that processes input through an LLM transform layer before generation.

    The transform step can rewrite queries, extract intent, add context, etc.
    Subclass and override `transform()` for custom behavior.
    """
    llm: LLM
    transform_llm: Optional[LLM] = None
    system_prompt: Optional[str] = None
    transform_prompt: Optional[str] = None

    def __post_init__(self):
        if self.transform_llm is None:
            self.transform_llm = self.llm

    def transform(self, user_input: str) -> str:
        """
        Transform user input before sending to the main LLM.

        Override this method for custom transformation logic.
        By default, uses transform_llm with transform_prompt if set,
        otherwise returns input unchanged.
        """
        if self.transform_prompt:
            return self.transform_llm.complete(
                user_input,
                system=self.transform_prompt
            )
        return user_input

    def generate(self, prompt: str) -> str:
        """
        Generate response from the main LLM.

        Override for custom generation logic.
        """
        return self.llm.complete(prompt, system=self.system_prompt)

    def run(self, user_input: str) -> str:
        """
        Run the full pipeline: transform → generate.
        """
        transformed = self.transform(user_input)
        return self.generate(transformed)


@dataclass
class ConversationAgent(Agent):
    """
    Agent that maintains conversation history.
    """
    history: list = field(default_factory=list)
    max_history: int = 10

    def run(self, user_input: str) -> str:
        """Run with conversation history."""
        transformed = self.transform(user_input)

        # Build messages with history
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend(self.history)
        messages.append({"role": "user", "content": transformed})

        # Generate response
        response = self.llm.chat(messages)

        # Update history
        self.history.append({"role": "user", "content": transformed})
        self.history.append({"role": "assistant", "content": response})

        # Trim history if needed
        if len(self.history) > self.max_history * 2:
            self.history = self.history[-self.max_history * 2:]

        return response

    def clear_history(self):
        """Clear conversation history."""
        self.history = []
