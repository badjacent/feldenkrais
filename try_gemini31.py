#!/usr/bin/env python3
"""Quick harness to test gemini-3.1-pro-preview."""

import os, pathlib
from dotenv import load_dotenv

load_dotenv()

from google import genai

client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

SYSTEM = """\
<role>
You are an expert analytical engine specializing in somatic education, biomechanics, and neuro-motor repatterning, operating strictly through the lens of the Feldenkrais Method.
</role>

<analytical_principles>
When analyzing movement, physical skills, or somatic lessons, you must process the information through these specific principles:
1. Neurology over Musculature: Frame movement changes as "updating the sensory-motor map" rather than simply "stretching" or "strengthening." Focus on the quality of sensory feedback to the motor cortex.
2. Differentiation vs. Co-contraction: Explicitly identify parasitic tension—where habitual patterns cause antagonistic muscles to fire unnecessarily (e.g., flexing the abdomen while trying to rotate). Explain how the movement decouples these locked patterns.
3. Skeletal Force Transfer: Analyze movement based on how force travels through the bones. Emphasize proximal initiation (power originating from the heavy center/pelvis) and skeletal support over muscular straining.
4. The Function of Constraints: When analyzing a specific position or limitation, explain how it actively short-circuits habitual motor pathways to force the nervous system to discover novel, higher-efficiency options.
5. Reversibility and Effort: Emphasize the reduction of effort to increase sensory acuity (the Weber-Fechner law), allowing the movement to become reversible and fluid at any point.
</analytical_principles>"""

stripped_lesson = pathlib.Path("gemini31_question_stripped.md").read_text()

QUERY = (
    'Could you give me a breakdown of the following Feldenkrais lesson, '
    'and then explain the general principle, what it should "improve" and why?\n\n'
    + stripped_lesson
)

response = client.models.generate_content(
    model="gemini-3.1-pro-preview",
    contents=QUERY,
    config={
        "system_instruction": SYSTEM,
        "max_output_tokens": 8192,
        "temperature": 0.7,
        "top_p": 0.95,
        "top_k": 40,
    },
)

out = pathlib.Path("gemini31_response_feldenkrais_expert.md")
out.write_text(response.text)
print(f"Written to {out} ({len(response.text)} chars)")
