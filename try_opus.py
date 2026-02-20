#!/usr/bin/env python3
"""Send the stripped Feldenkrais lesson to Claude Opus 4.6."""

import os, pathlib
from dotenv import load_dotenv

load_dotenv()

import anthropic

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

stripped_lesson = pathlib.Path("gemini31_question_stripped.md").read_text()

query = (
    'Could you give me a breakdown of the following Feldenkrais lesson, '
    'and then explain the general principle, what it should "improve" and why?\n\n'
    + stripped_lesson
)

response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=8192,
    system=(
        "You are a helpful, capable, and conversational AI assistant. "
        "Provide clear, well-structured, and comprehensive answers. "
        "Use formatting like bullet points and bold text to make complex information easy to scan."
    ),
    messages=[{"role": "user", "content": query}],
)

text = response.content[0].text
out = pathlib.Path("opus46_response_from_stripped.md")
out.write_text(text)
print(f"Written to {out} ({len(text)} chars)")
