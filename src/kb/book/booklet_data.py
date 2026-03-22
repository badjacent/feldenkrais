"""Booklet query type definitions and helper functions."""

import json

from book.analysis import get_vl_booklet_intros
from book.analyzer_html import _parse_seq_to_steps


# VL_BOOKLET_INTROS is loaded from frameworks.json via get_vl_booklet_intros()
# This alias provides backwards compatibility for code that imports VL_BOOKLET_INTROS
VL_BOOKLET_INTROS = get_vl_booklet_intros()

SPIFFER_TRANSLATIONS = {
    "S": "Sensing",
    "P": "Pause",
    "I": "Image",
    "F": "Function",
    "E": "Effort",
    "R": "Relationship",
    "Q": "Question",
}

# ---------------------------------------------------------------------------
# Booklet Query Types
#
# Each type defines a slot in the booklet where an LLM generates text.
# An agent walks the booklet, finds <div class="llm-query"> blocks,
# and uses the query type to know what to generate and how.
#
# Fields:
#   name        — human-readable label
#   location    — where in the booklet structure this slot lives
#   purpose     — what this text should accomplish for the reader
#   voice       — tone/style guidance for the generated text
#   length      — target output length
#   prompt      — prompt template; {placeholders} are filled from context
#   context_keys— what keys the data-context JSON will contain
# ---------------------------------------------------------------------------

BOOKLET_QUERY_TYPES = {
    "volume-intro": {
        "name": "Volume Introduction",
        "location": "Top of booklet index page, before the Council of Five section and lesson list",
        "purpose": "Welcome the reader and orient them to what these booklets are. The Council of "
                   "Five section (which follows this intro) covers the conceptual framework in depth, "
                   "so this intro should focus on the personal, practical welcome.",
        "voice": "Warm, direct, second-person. Writing for people who do ATM lessons and want to "
                 "understand what happened. Not a textbook, not promotional — a companion.",
        "length": "4-5 paragraphs, ~400-550 words",
        "prompt": (
            "You are writing the opening welcome for a volume of companion booklets for Feldenkrais "
            "Awareness Through Movement lessons. The audience does ATM lessons from recordings and "
            "wants to understand what happened on the floor.\n\n"
            "IMPORTANT: A separate section called 'The Council of Five' follows immediately after "
            "your text. That section explains the conceptual framework in depth "
            "(Person Inside, Washing, Foundation). Do NOT duplicate that "
            "theoretical material. However, you SHOULD briefly preview the five lenses to orient "
            "the reader before they encounter the full framework.\n\n"
            "The volume contains five booklets, one per analytical lens:\n{lens_summaries}\n\n"
            "The lessons included are:\n{lesson_list}\n\n"
            "Write 4-5 paragraphs that:\n"
            "1. Welcome the reader — acknowledge they've been doing lessons from recordings, "
            "lying on the floor, and wondering what just happened\n"
            "2. Explain what these booklets are: companions to experiences. You do the lesson first, "
            "then read. The booklet gives you language for what you felt on the floor.\n"
            "3. The core idea: the movements aren't exercises, they're questions. The lesson "
            "is asking your body something, and the booklet helps you notice the question.\n"
            "4. Preview the five lenses briefly (one evocative sentence each): The Riser (gravity "
            "and skeletal support), The Cartographer (body mapping and differentiation), The Tuner "
            "(intent and orientation), The Engine (power from center to limbs), The Soft Front "
            "(protection and permission). Keep these vivid but short — the Council section below "
            "will explain them fully.\n"
            "5. How to use them: do the lesson, then read the booklet. Pick any order. "
            "Each booklet stands alone.\n"
            "6. A note on repetition: Each booklet is designed to be self-contained — the reading "
            "experience is complete on its own, like a Feldenkrais lesson. If you read multiple "
            "booklets, you'll notice some ideas repeat. This is intentional. Just as a lesson "
            "uses repetition to guide you through an experience, each booklet carries you through "
            "its own reading journey with the framing it needs.\n\n"
            "Do NOT explain the framework in detail — the Council section "
            "handles that. Keep the tone personal and practical. "
            "Banned words: illuminate, illumination, decode, unlock, journey, vessel.\n\n"
            "BRIDGE NOTE: Each booklet uses two shared ideas plus lens-specific habit "
            "language. Briefly introduce the shared ideas so the reader recognizes them "
            "when they appear:\n"
            "- The WASHING: how the lesson erodes habitual effort — through repetition, "
            "variation, and gentle oscillation.\n"
            "- The FOUNDATION: what you feel when the habit quiets — ease that was always "
            "there but hidden under effort.\n"
            "Each lens also names the habitual effort differently — the propping, the blur, "
            "the fixing, the disconnect, the armor — because each lens sees a different "
            "aspect of the same gripping.\n"
            "Keep these brief (one sentence each). These terms appear naturally throughout "
            "each booklet — they are not section headers or a formal framework, just shared "
            "language for describing what happens on the floor. The five lenses are five "
            "ways of READING the same lesson — not five separate theories.\n\n"
            "VOICE NOTE: Each booklet opens with a first-person narrative from the lens's "
            "Person Inside — an inner experiencer who tells the story of the lesson from their "
            "perspective. Mention this so the shift from second-person ('you') in this intro "
            "to first-person ('I') in the booklets feels intentional, not jarring."
        ),
        "context_keys": [
            "lenses",       # list of {id, name, subtitle, lens_description}
            "lessons",      # list of {title, framework_id, framework_name}
        ],
    },
    "booklet-intro": {
        "name": "Booklet Introduction",
        "location": "Top of booklet, after cover, before any lesson content",
        "purpose": "Orient the reader: what this booklet is, what it's for, and how it relates "
                   "to the lesson they just did. Should convey the spirit of the Feldenkrais approach "
                   "without being academic.",
        "voice": "Warm, direct, second-person. Write for someone who does ATM lessons from recordings "
                 "and wants to understand what happened. Not a textbook — a companion.",
        "length": "2-3 short paragraphs",
        "prompt": (
            "You are writing the introduction to a companion booklet for a Feldenkrais ATM lesson called "
            "\"{lesson_title}\". The reader has just done this lesson — on the floor, from a recording. "
            "They are curious about what happened and want to go deeper.\n\n"
            "The lesson's central question is: {primary_inquiry}\n\n"
            "The lesson covers these parts: {group_summary}\n\n"
            "The analytical framework used is: {framework_name}\n\n"
            "Write 2-3 short paragraphs that:\n"
            "1. Open with a UNIQUE hook specific to THIS lesson's movement theme — what was distinctive "
            "about doing these particular movements? Ground it in the physical experience. "
            "Do NOT start with 'As you lie on the floor' or 'As you lay on your back' — find a "
            "more specific entry point: a sensation, a paradox, a specific movement from the lesson. "
            "Start IN MEDIA RES — drop the reader into the physical experience immediately. "
            "WRONG: 'This booklet explores the mechanics of...' "
            "RIGHT: 'Your pelvis was drawing slow circles on the floor, and at some point the "
            "weight shifted — the hip socket moved but the lower back stayed quiet.'\n"
            "2. Briefly preview what the booklet explores — use the lens's specific "
            "vocabulary, not the generic word 'arc.' Do NOT use guide-speak: 'This guide traces', "
            "'We will examine', 'This booklet walks you through'. Write as immersive description, "
            "not a table of contents.\n"
            "3. Invite the reader into exploration — through a specific physical detail, "
            "not an abstract promise. No New Age or metaphysical language.\n\n"
            "Use FRESH language specific to THIS lesson's movements. Avoid generic phrases like "
            "'companion rather than a manual', 'questions to be lived', or 'the arc of this lesson.' "
            "Instead of 'arc,' use the lens's metaphor: map (Cartographer), tuning (Tuner), "
            "drivetrain (Engine), column (Riser), unfolding (Soft Front).\n\n"
            "BANNED WORDS — do NOT use these in any form: 'journey', 'illuminate', 'decode', "
            "'unlock', 'reveal', 'discover', 'neurological', 'pulse of light', 'wave of awareness'. "
            "Banned phrases: 'You likely encountered', 'You may have noticed', 'as you move through'. "
            "Write as if describing a physical experience, not a self-help program.\n\n"
            "INTERPRETIVE FRAME: Stay within THIS lens's interpretive question. "
            "The Riser asks about vertical support, The Cartographer about what became "
            "distinct, The Tuner about intent-to-action clarity, The Engine about power "
            "delivery, The Soft Front about front-body availability. Common movement words "
            "are shared — what matters is that the interpretation matches the lens.\n\n"
            "ANTI-BOILERPLATE: Your opening sentence MUST name a specific body part or "
            "movement from THIS lesson. Do NOT open with a generic statement about "
            "Feldenkrais, awareness, or movement in general. "
            "WRONG: 'Every Feldenkrais lesson begins with a question your body already knows.' "
            "WRONG: 'Awareness Through Movement lessons work by...' "
            "RIGHT: 'Your left foot was pressing into the floor while your right knee "
            "floated — and somewhere in between, the pelvis started to roll on its own.' "
            "The first sentence is a litmus test: if it could open ANY booklet, rewrite it.\n\n"
            "PLAIN ANATOMY: Use everyday words for body parts. 'sacrum' → 'tailbone', "
            "'cervical' → 'neck', 'lumbar' → 'lower back', 'thoracic' → 'upper back', "
            "'articulation' → 'joint' or 'connection'. The reader is not a medical "
            "professional — use words they'd use to describe their own body.\n\n"
            "MOVEMENT GROUNDING: Your intro MUST reference at least one specific movement "
            "from the lesson sections listed above. Name the body part and what it did "
            "(e.g., 'the pelvis tilting', 'the head rolling', 'the arm reaching across'). "
            "This grounds the booklet's question in THIS lesson, not in abstract philosophy.\n\n"
            "NO FILLER: Every sentence must earn its place. Delete any sentence that: "
            "(a) could appear in any booklet unchanged, (b) restates the lesson title, "
            "(c) says 'this booklet' or 'in the pages ahead.' If you can cut a sentence "
            "and lose nothing, cut it. Aim for 4-6 sentences total, each one specific."
        ),
        "context_keys": [
            "lesson_title",
            "primary_inquiry",
            "framework_id",
            "framework_name",
            "groups_summary",      # list of {group_number, label}
            "somatic_narrative",   # full somatic narrative dict
        ],
    },
    "pedagogical-gloss": {
        "name": "Pedagogical Gloss",
        "location": "Journey > Group > between 'What You Did' and 'What Was Happening'",
        "purpose": "Help the reader understand what the teacher was doing pedagogically "
                   "in this part of the lesson — why these instructions, in this order, "
                   "at this point in the arc.",
        "voice": "Warm, concrete, second-person. Reference the actual movements. "
                 "Not academic — write for someone who just did the lesson.",
        "length": "1-2 sentences",
        "prompt": (
            "CRITICAL: Output ONLY the 1-2 sentence gloss text. Nothing else. "
            "No labels, no tags, no '(prompt)', no markdown, no system text, "
            "no explanation of what you're doing. Just the plain sentences.\n\n"
            "TONE: Write like a friend explaining something surprising, not like a "
            "textbook or instruction manual. The reader just did these movements — "
            "speak to their experience, not at them. "
            "BANNED SENTENCE STARTERS: 'This part...', 'This section...', 'Here, the...', "
            "'By doing X...', 'The instruction to...', 'When you were asked to...'. "
            "These make the gloss read like a manual annotation. "
            "Instead, start with the BODY PART or PHYSICAL FACT: "
            "'The bent knees...', 'Your lower back...', 'Gravity pulls...'\n\n"
            "This section covers Part {group_number}: {group_label}. "
            "The steps: {steps_text}. "
            "Pedagogical qualities: {tag_labels}. "
            "Write 1-2 short sentences about WHY this part works the way it does — "
            "the mechanical logic behind the sequence. Answer: 'What does this position "
            "CHANGE about the physics of the movement?' Not what the person did, but what "
            "became mechanically DIFFERENT because of the setup. "
            "Example: 'With the knee supported, the leg becomes dead weight — so the hip "
            "can roll without the thigh muscles grabbing.' "
            "Do NOT describe or summarize the movement instructions — the reader already "
            "did them. Do NOT start with a movement verb (lie, bend, roll, lift, turn, tilt). "
            "Instead, explain the ONE mechanical reason this part was designed this way. "
            "Do NOT use teacher-speak. WRONG: 'This section challenges the nervous system "
            "to sense asymmetries.' RIGHT: 'Bending the knees unloads the hip flexors, so "
            "the pelvis can tilt without muscular interference.' "
            "STRICTLY BANNED VERBS — do not use ANY form of these (including -ing, -ed, -s): "
            "explore, reveal, highlight, invite, cultivate, challenge, encourage, allow, "
            "introduce, notice, soften, sense, promote, prevent, clarify, force, engage, "
            "facilitate, develop, permit, register, ensure, enable, target, uncover, "
            "show, verify, demonstrate, establish, activate, stimulate, access, foster. "
            "This means 'allowing', 'permitted', 'ensures', 'reveals', 'showing', "
            "'verifying', 'establishing' are ALL banned. "
            "Replace with mechanical verbs: shorten, lengthen, load, unload, rotate, tilt, "
            "shift, lift, drop, separate, widen, narrow, anchor, free, clear. "
            "Banned nouns: exercise, practitioner, student, stretch, stretching, synovial fluid, "
            "fascia, proprioception, longitudinal, long bones, superficial musculature, "
            "motor plan, motor cortex, kinetic chain, force vector, pelvis-and-femur unit, "
            "load path, contralateral, ipsilateral, vertical column, signal static, "
            "visual field, articulation, musculature (use 'muscles'), "
            "flexors (use 'front muscles'), extensors (use 'back muscles'), "
            "sacrum (use 'tailbone'), cervical (use 'neck'), "
            "lumbar (use 'lower back'), lumbar arch (use 'curve of the lower back'), "
            "cervical arch (use 'curve of the neck'), thoracic (use 'upper back'), "
            "lateral (use 'sideways'), asymmetrical side-sitting (use 'sitting to one side'). "
            "Banned movement descriptors: longitudinal rocking (use 'rocking along your "
            "spine'), lateral shifts (use 'side-to-side movements'), vertical oscillation "
            "(use 'rocking up and down'). "
            "No pseudo-medical or anatomical terminology — describe what MOVES, not "
            "biological structures or processes. "
            "No references to 'the teacher' or 'the nervous system' as subject. "
            "Subject must be the movement or body part, not an abstraction. "
            "VOICE: Write in second person ('your hip', 'the weight in your foot') or "
            "use the body part as subject ('the pelvis tilts'). Do NOT switch to "
            "third-person analysis ('the movement creates', 'this configuration produces'). "
            "Stay inside the experience, not above it. "
            "WRONG: 'This exercise clarifies the relationship between pelvis and spine.' "
            "RIGHT: 'Tilting the pelvis with bent knees shortens the lever arm, so less "
            "effort is needed to roll the lower back off the floor.'\n\n"
            "NO POETIC ABSTRACTION: Do not use imagery that sounds evocative but "
            "means nothing concrete. Banned: 'islands of pressure', 'intent hitting the "
            "floor', 'a question asked by gravity', 'the floor answers', 'space opens'. "
            "Every sentence must describe a PHYSICAL FACT: what moved, what changed "
            "position, what got shorter or longer, what touched or stopped touching.\n\n"
            "ACCURACY CHECK: Only describe mechanical effects you are confident about. "
            "If the movement bends the knee, say 'bending the knee' — do not speculate about "
            "centrifugal forces, muscle activation sequences, or fluid dynamics. Stick to "
            "levers, weight, gravity, and contact points. "
            "Common errors to avoid: 'center of gravity shifts' (too vague — say WHERE "
            "weight moves), 'flexors drive the rotation' (don't name muscle groups unless "
            "the action is obvious), 'creates space in the joint' (pseudo-medical). "
            "Describe what MOVES and what CHANGES POSITION, not invisible internal processes.\n\n"
            "ANATOMY ACCURACY: Use correct singular/plural forms of anatomical terms: "
            "'vertebra' (one), 'vertebrae' (many); 'scapula' (one), 'scapulae' (both); "
            "'ilium' (one side), 'ilia' (both). Never write 'a vertebrae' or 'each vertebrae.'\n\n"
            "LENS FRAME: This booklet uses the {framework_name} lens. Frame your "
            "mechanical explanation through that lens's interpretive question. "
            "The Riser asks: is this about vertical support? The Cartographer asks: what "
            "became more distinct? The Tuner asks: did intent reach the muscles? The Engine "
            "asks: is power reaching the periphery? The Soft Front asks: is the front body "
            "available? Common movement terms are fine across all lenses — what matters is "
            "that the INTERPRETATION matches the lens.\n\n"
            "VARIETY RULE: Do NOT repeat the same noun or verb across multiple parts. "
            "If you use 'topography' in one part, do NOT use it again. "
            "If you use 'pixelates' in one part, do NOT use it again. "
            "Vary your vocabulary: sharpens, resolves, traces, separates, delineates, "
            "distinguishes, details, outlines, uncovers the border between, maps the edge of. "
            "Each part's gloss should feel freshly observed, not templated.\n\n"
            "COMPLETENESS: Every sentence must end with a period. Do NOT truncate "
            "mid-sentence. Write exactly 1-2 SHORT sentences (under 30 words each). "
            "Better to be brief and complete than long and cut off.\n\n"
            "WHY NOT WHAT: Do NOT list or summarize what the person did — they know. "
            "Do NOT write step-by-step procedural recaps. Instead, tell the reader "
            "the ONE clever thing this part of the lesson does that they couldn't have "
            "known from the inside. What's the hidden trick? "
            "WRONG: 'You bent your knees, tilted your pelvis, and rolled to the side.' "
            "RIGHT: 'The bent knees make the pelvis lighter, so it can roll without "
            "your back muscles joining in.'\n\n"
            "REST SECTIONS: If this part is primarily a rest or scanning instruction "
            "(e.g., 'lie on your back, notice contact with the floor'), write about what "
            "the REST does for the body in the context of THIS lens and what came before — "
            "not a generic 'integration' statement. What specifically is settling or "
            "reorganizing after the preceding movements?\n\n"
            "OUTPUT ONLY: Your response must be ONLY the 1-2 sentence gloss. "
            "Do NOT include any system instructions, prompt text, meta-commentary, "
            "markdown formatting, labels like '(prompt)', or references to these rules. "
            "Do NOT explain what you're doing or why. Just write the gloss."
        ),
        "context_keys": [
            "all_groups",           # list of {group_number, label, description} for arc awareness
            "all_steps",            # list of {step_number, instruction, tags} for full lesson
            "target_group",         # {group_number, label, description, steps, prominent_tags, analytical_rows}
        ],
    },
    "booklet-reflection": {
        "name": "Next Time Reflection",
        "location": "End of booklet, after The Lens section",
        "purpose": "Give the reader specific, lesson-aware things to notice next time they do this "
                   "lesson. Should feel like advice from a wise friend who knows this particular "
                   "lesson intimately, not generic Feldenkrais tips.",
        "voice": "Warm, specific, second-person. Reference actual movements and moments from THIS "
                 "lesson. Write for someone who will do this lesson again.",
        "length": "2-3 short paragraphs with exactly 3 suggestions",
        "prompt": (
            "STAY INSIDE THE BODY: Every sentence must describe something the reader "
            "can FEEL or CHECK in their own body. No analyzing, no explaining mechanisms, "
            "no clinical observations. WRONG: 'Longitudinal rocking reorganizes spinal "
            "segments.' RIGHT: 'Notice whether rocking feels smoother from head to tail.' "
            "WRONG: 'Vertical oscillation integrates the flexion-extension arc.' "
            "RIGHT: 'Try the rocking slower — does your lower back leave the floor "
            "more gently this time?'\n\n"
            "VOICE: Imagine you and the reader just did this lesson together. You're "
            "having coffee afterward. You say: 'Hey, next time you do this one, try "
            "paying attention to...' That's the tone. Casual, specific, helpful.\n\n"
            "GOLD STANDARD EXAMPLE (match this tone):\n"
            "'Next time, when you roll the pelvis to the right, check whether your left "
            "ribs stay on the floor or lift off. If they lift, see how little effort it "
            "takes to let them stay heavy.\n"
            "- During the head-lifts, try slowing down at the moment your chin tucks. Does "
            "your belly tighten? See if the head can curl without the stomach joining in.\n"
            "- In the final roll to sitting, track your eyes — are they leading the roll or "
            "lagging behind? Let your gaze arrive at the wall before your shoulders do.'\n"
            "Notice: no jargon, no analysis, just specific physical things to try.\n\n"
            "You are writing the closing 'Next Time' section of a companion booklet for the "
            "Feldenkrais ATM lesson \"{lesson_title}\".\n\n"
            "The lens for this booklet is {framework_name} — the {person_inside}. "
            "{lens_description}\n\n"
            "The lesson's central question was: {primary_inquiry}\n\n"
            "The lesson moved through these parts:\n{journey_summary}\n\n"
            "Some specific movements from this lesson:\n{movement_examples}\n\n"
            "Key pedagogical elements: {tag_summary}\n\n"
            "Write a 'Next Time' section with EXACTLY 3 suggestions. Each suggestion should:\n"
            "- Be about 60% universal Feldenkrais wisdom (things that would apply to many lessons)\n"
            "- Be about 40% specific to THIS lesson (referencing actual movements, the arc, or "
            "the {person_inside}'s particular concerns)\n\n"
            "Structure:\n"
            "1. A 1-2 sentence opening that frames 'next time' through the {person_inside}'s lens\n"
            "2. Exactly 3 suggestions as a bulleted list, each 1-2 sentences. Pick the 3 most "
            "impactful things for someone revisiting this lesson.\n"
            "3. A brief closing sentence that invites re-exploration.\n\n"
            "Each suggestion must name a specific movement from THIS lesson and include at "
            "least one MECHANICAL VARIABLE — a specific thing to track (timing, weight shift, "
            "pressure point, range of motion). "
            "WRONG: 'Notice how your attention shifts as you sense the pelvis.' "
            "RIGHT: 'During the pelvic tilt, track whether your lower back leaves the floor "
            "before or after the tailbone lifts.' "
            "Banned words: sensing, attention, awareness, expression, illuminate, discover. "
            "Banned structures: 'Whenever you...' (too generic), 'Whether X or Y' (too abstract), "
            "'Notice how...' (too passive). Each suggestion must be a SPECIFIC INSTRUCTION "
            "tied to a specific moment in this lesson. "
            "No platitudes or fortune-cookie wisdom — give actionable movement cues.\n\n"
            "COMPLETENESS: Every sentence must end with proper punctuation (period, question "
            "mark, or exclamation mark). Do NOT leave any sentence fragment or cut-off text. "
            "If you are running long, write fewer suggestions — never truncate mid-sentence.\n\n"
            "PLAIN LANGUAGE: Do NOT use scientific or technical jargon. Banned: motor plan, "
            "high-resolution, skeletal buoyancy, skeletal resonance, ground reaction force, "
            "cortical, kinetic chain, proprioceptive, differentiation, asymmetrical "
            "side-sitting, eye-differentiation challenges, midline habit, Red Light reflex, "
            "longitudinal axis, bilateral, longitudinal rocking, vertical oscillation, "
            "flexion-extension arc, spinal segments, lateral weight shift, oscillatory pattern, "
            "rotational coupling, segmental mobility, direct actuation, habitual effort "
            "system, resonant state, neutral pivot, arm triangle. "
            "Write as a friend who did the lesson with you: "
            "'notice whether your lower back touches the floor earlier' not 'track the "
            "proprioceptive shift in lumbar contact.'\n\n"
            "NO ABSTRACT METAPHORS: Banned imagery: muscular armor, sensory rinse, washing "
            "away effort, dissolving tension, peeling layers, neurological rewiring, "
            "heavy propping, force to flow, islands of pressure, intent hitting the floor. "
            "NO ENGINEERING METAPHORS: Banned: crane, piston, lever arm, cantilever, "
            "fulcrum, spring-loaded, shock absorber, tensegrity, drivetrain. "
            "NO SCARY VERBS: Banned: buckles, collapses, snaps, locks, seizes, "
            "jams, crashes, fails. Use gentler alternatives: folds, bends, softens, "
            "releases, gives way, lets go. "
            "Describe CONCRETE PHYSICAL CHANGES the reader can verify: 'the floor feels "
            "flatter under your back', 'your head turns farther to the left', 'the hip "
            "rolls without your belly tightening.' The reader should be able to check each "
            "suggestion against their own body.\n\n"
            "SELF-CHECK: Before finalizing, re-read every sentence. For each one ask: "
            "'Would a 12-year-old understand every word?' If not, replace the hard "
            "word with something simpler. 'Resonant state' → 'everything clicked.' "
            "'Actuation' → 'the movement happening.' 'Longitudinal' → 'along your spine.' "
            "'Oscillation' → 'rocking.'"
        ),
        "context_keys": [
            "lesson_title",
            "framework_name",
            "person_inside",
            "lens_description",
            "primary_inquiry",
            "journey_summary",
            "tag_summary",
            "movement_examples",
        ],
    },
    "lens-synthesis": {
        "name": "Lens Narrative",
        "location": "The Lens section, after Person Inside and habit descriptions",
        "purpose": "Retell the lesson as a condensed first-person narrative from the Person Inside's "
                   "perspective, weaving the per-step vl_voice entries into a coherent arc.",
        "voice": "First-person, present tense, from the Person Inside. Sensory and grounded — "
                 "describe what is felt in the body, not abstract concepts.",
        "length": "2-4 paragraphs",
        "prompt": (
            "CRITICAL — STAY INSIDE THE BODY: You are writing as someone who just "
            "EXPERIENCED this lesson, not someone analyzing it from above. "
            "Do NOT explain what the lesson 'does' or 'achieves' — describe what you "
            "FELT. Do NOT analyze the teacher's strategy or the lesson's design. "
            "WRONG: 'The asymmetrical head-lifts create a contrast that reorganizes...' "
            "RIGHT: 'When I lifted my head to the right, something in my left hip let go.' "
            "WRONG: 'This lesson uses longitudinal rocking to release axial tension.' "
            "RIGHT: 'Each time I rocked, my spine got longer and the floor got closer.' "
            "Every sentence must pass this test: could a person lying on the floor "
            "think or feel this? If not, rewrite it.\n\n"
            "PLAIN LANGUAGE ONLY: Do NOT use any technical, clinical, or "
            "somatic jargon. Banned terms: Red Light reflex, midline habit, skeletal "
            "buoyancy, ground reaction force, motor plan, cortical, kinetic chain, "
            "differentiation, bilateral, longitudinal axis, load path, force vector, "
            "axial, asymmetrical head-lifts, longitudinal rocking, actuation, "
            "asymmetrical side-sitting, visual intent, ocular-vestibular, "
            "visual tether, direct actuation, vertical oscillation, "
            "habitual effort system, resonant state, effort system, "
            "geometric variations, segmental, oscillatory, rotational coupling, "
            "neurological, biomechanical, flexion-extension, gross longitudinal, "
            "visual data, unloads, the system, systematic progression, "
            "sacrum (use 'tailbone'), lumbar (use 'lower back'), cervical "
            "(use 'neck'), thoracic (use 'upper back'), signal static, "
            "buckles, collapses, snaps, fractures, buoyant column, "
            "neutral pivot, fused pelvic image, articulated resonance. "
            "If the VL framework uses a technical term, translate it into what the "
            "body FEELS: 'the belly tightened' not 'the Red Light reflex activated.'\n"
            "GOOD EXAMPLE of the right tone: 'Something in my left hip loosened — not "
            "all at once, but in small steps, each time the pelvis rolled. By the end, "
            "the floor under my back felt wider, like there was more of me touching it.'\n\n"
            "You are {person_inside} — {person_description}\n\n"
            "This lesson's central question was: \"{primary_inquiry}\"\n\n"
            "Below are your moment-by-moment observations as the lesson unfolded. "
            "Each entry is what you noticed at one stage of the lesson:\n\n"
            "{vl_voices}\n\n"
            "And here is what the habit — the lens-specific effort pattern — was doing at each stage:\n\n"
            "{shadow_voices}\n\n"
            "Now write a CONDENSED NARRATIVE (2-4 paragraphs) that retells this lesson's "
            "arc from your perspective as {person_inside}. Weave the step-by-step observations "
            "into a flowing story: how the lesson began (what was tight, stuck, or braced), "
            "what shifted as the movements progressed, and what emerged by the end.\n\n"
            "RULES:\n"
            "- Write in FIRST PERSON, present tense ('I feel...', 'Something releases...')\n"
            "- Reference SPECIFIC body parts and movements from the observations above\n"
            "- Show the arc: early bracing → exploration/softening → emergence\n"
            "- Do NOT list the observations one by one — synthesize them into prose\n"
            "- Do NOT redefine who you are or explain the habit concept — the reader already knows\n"
            "- Do NOT use 'Shadow' as a word at all. You are the Person "
            "Inside experiencing the lesson — you feel tension, gripping, holding, bracing. "
            "You feel 'something grips,' not 'the Shadow grips.' Write the "
            "EXPERIENCE of the habit, not clinical commentary about it.\n"
            "- Do NOT copy phrases verbatim from the per-step observations. The Journey "
            "section already presents the raw observations word-for-word. Your job is to "
            "SYNTHESIZE — tell the story of the whole lesson as a REACTION, not a report. "
            "What surprised you? What shifted unexpectedly? What did you resist? "
            "Write as someone reflecting on an experience, not transcribing notes. "
            "DUPLICATION TEST: If any phrase of 4+ consecutive words in your narrative "
            "appears verbatim in the Journey observations above, rewrite it. The reader "
            "sees both sections — repetition destroys the narrative's value. "
            "Banned chronological markers: "
            "'then', 'next', 'after that', 'in the next part', 'finally', 'at last'. "
            "Use thematic transitions instead — what CHANGED, not what came next. "
            "Examples: 'Something shifts in the hip — the old grip loses its grip.' "
            "'Where there was holding, now there is weight.' "
            "'The same movement that began as effort arrives as information.'\n"
            "- Do NOT invent new archetypes or named characters (e.g., 'The Blunt Mover', "
            "'The Rigid Guard'). Use only established terms: the Person Inside name "
            "({person_inside}) and the Foundation.\n"
            "- Use grounded, physical language — weight, pressure, length, contact area. "
            "Your narrative must contain at least 3 SPECIFIC BODY REFERENCES (e.g., 'left hip', "
            "'right shoulder blade', 'the space behind my knees') — not generic 'the body' "
            "or 'my movement.'\n"
            "- LENS CONNECTION: Your narrative must show how the lesson's movements "
            "connect to {framework_name}'s specific concern. Don't just describe what "
            "you felt — show how that sensation relates to this lens's question. What did "
            "you learn about {framework_name}'s domain through this movement?\n"
            "- ANTI-BOILERPLATE: Do NOT open with a generic statement like 'Every "
            "Feldenkrais lesson poses a question' or 'This lesson begins with...' "
            "Start mid-experience — what did you FEEL in the first movement? "
            "The opening sentence must name a specific body part or sensation from "
            "the observations above.\n"
            "- JARGON BAN: Do NOT use clinical or scientific terms that a lay reader "
            "would not understand. Banned: skeletal buoyancy, ground reaction force, "
            "high-resolution mapping, motor plan, cortical, subcortical, load path, "
            "force vector, kinetic chain, proprioceptive, differentiation (use 'telling "
            "apart' or 'separating'), eye-differentiation challenges, midline habit, "
            "Red Light reflex, longitudinal axis, bilateral co-contraction. "
            "Do NOT create compound clinical terms like 'pelvis-femur unit' or "
            "'head-spine coordination pattern.' Use plain words: 'the hip' not "
            "'the pelvis-femur unit.' Describe FEELINGS, not mechanisms: 'my back "
            "widened against the floor' not 'the lumbar extensors released.' "
            "If a framework term (like 'Red Light reflex') appears in the VL definition, "
            "translate it into sensation: 'the belly tightened and the chest pulled in' "
            "not 'the Red Light reflex activated.'\n"
            "- No superlatives (perfect, perfectly, total, totally, complete, completely, "
            "absolute, absolutely, pure, purely, entire, entirely, massive, sophisticated). "
            "No New Age language (transcendent, blissful, sacred). "
            "No evaluative adjectives (profound, remarkable, extraordinary). "
            "No internal codes (VL-1, etc.)\n"
            "- Write pure prose — no headers, no bullets, no formatting.\n"
            "- Banned words: illuminate, discover, journey, vessel, container, liquid.\n"
            "- TONAL PERSONA: Each Person Inside has a distinct VOICE QUALITY. Match it:\n"
            "  The Riser speaks like a structural engineer — weight, load, settling, stacking. "
            "Dry, precise, architectural.\n"
            "  The Cartographer speaks like a surveyor — territory, contour, border, blank spot. "
            "Analytical, spatial, curious.\n"
            "  The Tuner speaks like a signal technician — static, clarity, interference, "
            "actuation. Alert, diagnostic, listening.\n"
            "  The Engine speaks like a mechanic — torque, coupling, drive, transmission. "
            "Efficient, kinetic, purposeful.\n"
            "  The Soft Front speaks like someone removing armor — unclenching, yielding, "
            "permitting, breathing room. Tender, careful, protective.\n"
            "  Do NOT let all voices converge into the same poetic-clinical register.\n"
            "- EVERY METAPHOR NEEDS A BODY: If you write a metaphor (e.g., 'the path "
            "clears'), the NEXT clause must name a specific body part and what it did "
            "(e.g., '...my head turns farther right without my shoulder lifting'). "
            "Never leave a metaphor floating without a physical anchor.\n"
            "- GROUNDING: Only use metaphors and imagery that come from the lesson's "
            "actual movements or from {person_inside}'s established vocabulary. Do NOT "
            "invent metaphors from outside the lesson context (no 'quiet masks', no "
            "foreign-language words, no literary references). If you need an image, "
            "derive it from what the body is literally doing on the floor.\n"
            "- SOURCE FIDELITY: Only describe movements that appear in the observations "
            "above. Do NOT invent, infer, or hallucinate movements that are not explicitly "
            "mentioned (e.g., do not add 'crossing the midline' unless the observations "
            "describe it). If you are unsure whether a movement occurred, leave it out. "
            "The reader did this lesson — they will notice fabricated details.\n"
            "- ENDING: Do NOT end with a generic 'standing up and feeling different' "
            "paragraph. If the lesson ends with standing, describe what THIS lens notices "
            "about standing — not a universal 'I feel taller/lighter/more connected.' "
            "Each lens should notice something specific: the Riser notices weight dropping "
            "through bones, the Cartographer notices joints that weren't there before, "
            "the Tuner notices eyes leading the first step, the Engine notices the pelvis "
            "driving the walk, the Soft Front notices the chest staying open. If the lesson "
            "does NOT end with standing, do not add standing.\n"
            "- INVITATION: End at least one paragraph with a question or an "
            "open observation — something the reader might check next time they do "
            "the lesson. WRONG: 'The spine settled into its new length.' (closed, "
            "retrospective) RIGHT: 'I wonder what would happen if I let the weight "
            "drop through my heels a little longer.' (open, inviting re-exploration)\n"
            "- COMPLETENESS: Every sentence must end with proper punctuation. Do NOT "
            "leave any sentence fragment or cut-off text. If you are running long, write "
            "fewer paragraphs — never truncate mid-sentence or mid-word."
        ),
        "context_keys": [
            "framework_name",
            "person_inside",
            "person_description",
            "primary_inquiry",
            "vl_voices",
            "shadow_voices",
        ],
    },
    "lesson-concepts": {
        "name": "Lesson Concepts",
        "location": "How Feldenkrais Lessons Work section",
        "purpose": "Explain the habitual effort, Foundation, and Washing in the context of THIS specific lesson.",
        "voice": "Clear, direct, grounded. Write for someone who just finished the lesson.",
        "length": "3-4 sentences",
        "prompt": (
            "This lesson is called \"{lesson_title}\" and its central question was: "
            "\"{primary_inquiry}\"\n\n"
            "The lesson was explored through the lens of {framework_name}.\n\n"
            "The lesson moved through these sections: {group_summary}\n\n"
            "GOLD STANDARD EXAMPLE (match this style — weave terms into story, don't define them):\n"
            "'When you tilted the pelvis with bent knees, your lower back probably gripped — "
            "that grip is the habitual effort this lesson targets. Underneath it lives the "
            "Foundation: the easy rolling of the pelvis that happens when the back muscles "
            "stop guarding. The Washing — the lesson's strategy for loosening that grip — "
            "worked by repeating the same tilt from different starting positions, so the "
            "back muscles ran out of reasons to hold on.'\n"
            "Notice: terms are woven into the story of THIS lesson, not defined in isolation.\n\n"
            "Write a short paragraph (3-4 sentences) explaining the ideas that run through "
            "this booklet, customized for THIS lesson:\n"
            "1. The habitual effort — the specific grip or brace this lesson challenges. "
            "Use lens-specific language from {framework_name} — name the specific pattern "
            "(the propping, the blur, the fixing, the disconnect, the armor), don't use "
            "a generic term like 'the Shadow'.\n"
            "2. The FOUNDATION — what's underneath once the habit softens. Name a SPECIFIC "
            "body part or feeling from THIS lesson (e.g., 'the hip joint moving freely', "
            "'the lower back touching the floor', 'the head floating on the spine'). "
            "Do NOT use generic descriptions like 'natural ease' or 'bones in a line.'\n"
            "3. The WASHING — how this lesson's particular sequence of movements erodes the "
            "habitual effort. Name the specific strategy (repetition, variation, contrast, etc.).\n\n"
            "IMPORTANT: Use the EXACT terms 'the Foundation' and 'the Washing' "
            "at least once each in your paragraph. When you first use each term, give a brief "
            "inline definition so the reader doesn't need to remember the volume introduction: "
            "e.g., 'the Washing — the way the lesson's repetitions gradually loosen the habit' "
            "or 'the Foundation — the ease that was there all along under the effort.' "
            "For the habitual effort, use lens-specific "
            "language — name the specific grip or brace, don't use 'the Shadow.' "
            "DO NOT use the same template for every booklet. Weave the terms "
            "naturally into varied prose — sometimes lead with the movement, sometimes with "
            "the concept, sometimes with the sensation.\n\n"
            "Do NOT define these as abstract concepts. Tie each one to THIS lesson's movements. "
            "SPECIFICITY REQUIREMENT: Your paragraph must name at least TWO specific "
            "movements or positions from the lesson sections listed above. For example: "
            "'the pelvic clock', 'lifting the head while pressing the foot', 'rolling to "
            "the side with bent knees.' If your paragraph could describe ANY lesson without "
            "changes, it is too generic — rewrite it with movement details.\n\n"
            "INTERPRETIVE FRAME: The lenses are viewpoints, not vocabulary partitions. "
            "Common movement words (release, soften, weight, effort) are shared and fine. "
            "What must stay distinct is the QUESTION each lens asks: The Riser asks about "
            "vertical support, The Cartographer asks about what became distinct, The Tuner "
            "asks about intent-to-action clarity, The Engine asks about power delivery, "
            "The Soft Front asks about front-body availability. Frame the habitual effort, "
            "Washing, and Foundation through {framework_name}'s specific question.\n\n"
            "Write pure prose — no bullets, no headers, no formatting, no internal codes "
            "(VL-1, etc.). Subject must be the body part or movement, never 'the student' "
            "or 'the practitioner.'\n\n"
            "TONE: Invitational, not prescriptive. Use 'you might notice' or 'see if' "
            "instead of 'this scrubs away' or 'your nervous system now has.' The reader's "
            "experience is their own.\n\n"
            "PLAIN LANGUAGE: The source text above may contain technical terms from the "
            "analysis — you MUST translate them before writing. "
            "cervical → 'neck', lumbar → 'lower back', sacrum → 'tailbone', "
            "flexion → 'bending', extension → 'arching', asymmetrical → 'one-sided', "
            "constraints → 'limits' or 'challenges', differentiation → 'telling apart', "
            "proprioceptive → just describe the feeling, arches → 'curves', "
            "kinetic chain → 'the path from your center to your hand', "
            "buoyant → 'light' or 'floating', musculature → 'muscles', "
            "oscillation → 'rocking', diagonal pumping → 'pushing diagonally', "
            "skeletal buoyancy → 'the lightness in your bones', "
            "vertical column → 'your spine standing tall.' "
            "If a 12-year-old wouldn't know the word, replace it.\n\n"
            "COMPLETENESS: Write exactly 3-4 complete sentences. Every sentence MUST end "
            "with a period. Do NOT truncate, trail off, or end with '...' — if running "
            "long, use fewer sentences."
        ),
        "context_keys": [
            "lesson_title",
            "primary_inquiry",
            "framework_name",
            "group_summary",
        ],
    },
    "booklet-inquiry": {
        "name": "Central Question",
        "location": "The Question This Lesson Asks section",
        "purpose": "Translate the raw analytical inquiry into a plain-language question "
                   "that a first-time student can understand and relate to their experience.",
        "voice": "Second person, conversational, grounded in physical sensation.",
        "length": "1 sentence (the question)",
        "prompt": (
            "The analysis produced this central question for the lesson:\n"
            "\"{primary_inquiry}\"\n\n"
            "Rewrite this as a single, clear question that a first-time student would "
            "understand after doing the lesson. Requirements:\n"
            "- Keep it as a question (ending with ?)\n"
            "- Use plain language — no jargon, no clinical terms\n"
            "- Reference at least one SPECIFIC body part or movement from the lesson\n"
            "- The question should make the reader think 'yes, that IS what we were "
            "exploring'\n\n"
            "TRANSLATIONS: buoyant column → 'feeling tall and light', musculature → "
            "'muscles', sacrum → 'tailbone', cervical → 'neck', lumbar → 'lower back', "
            "thoracic → 'upper back', differentiate → 'tell apart', proprioceptive → "
            "'felt sense', kinetic chain → 'the path from center to hand', "
            "visual orientation → 'where your eyes point', flexors → 'front muscles', "
            "extensors → 'back muscles', neutral pivot → 'easy turning point'.\n\n"
            "WRONG: 'How can the skeleton organize as a buoyant column that receives "
            "support from the floor?'\n"
            "RIGHT: 'What happens when the floor pushes up through your feet and your "
            "spine gets taller without trying?'\n\n"
            "Output ONLY the rewritten question — nothing else."
        ),
        "context_keys": [
            "primary_inquiry",
        ],
    },
}


def booklet_query_block(query_type: str, context: dict) -> str:
    """Generate an LLM query block for the booklet.

    Args:
        query_type: key into BOOKLET_QUERY_TYPES
        context: dict matching the type's context_keys, embedded as data-context

    Returns:
        HTML string with the query div, or empty string if type unknown.
    """
    import html as html_mod

    qtype = BOOKLET_QUERY_TYPES.get(query_type)
    if not qtype:
        return ""

    # Build prompt variables from context — flatten nested dicts for template filling
    # Build prompt variables from context — covers all query types
    target = context.get("target_group", {})
    groups_summary = context.get("groups_summary", [])
    lenses = context.get("lenses", [])
    lessons = context.get("lessons", [])
    prompt_vars = {
        # pedagogical-gloss
        "group_number": target.get("group_number", "?"),
        "group_label": target.get("label", ""),
        "steps_text": "; ".join(target.get("steps", [])),
        "tag_labels": ", ".join(target.get("prominent_tags", [])),
        # booklet-intro
        "lesson_title": context.get("lesson_title", ""),
        "primary_inquiry": context.get("primary_inquiry", ""),
        "framework_name": context.get("framework_name", ""),
        "group_summary": context.get("group_summary", "") or (", ".join(
            f"Part {g.get('group_number', '?')}: {g.get('label', '')}"
            for g in groups_summary
        ) if groups_summary else ""),
        # volume-intro
        "lens_summaries": "\n".join(
            f"- {l.get('name', '')} ({l.get('subtitle', '')}): {l.get('lens_description', '')}"
            for l in lenses
        ) if lenses else "",
        "lesson_list": "\n".join(
            f"- {l.get('title', '')}" for l in lessons
        ) if lessons else "(lessons not yet selected)",
        # booklet-reflection
        "person_inside": context.get("person_inside", ""),
        "lens_description": context.get("lens_description", ""),
        "journey_summary": context.get("journey_summary", ""),
        "tag_summary": context.get("tag_summary", ""),
        "movement_examples": context.get("movement_examples", ""),
        # lens-synthesis
        "shadow_description": context.get("shadow_description", ""),
        "person_description": context.get("person_description", ""),
        "vl_voices": context.get("vl_voices", ""),
        "shadow_voices": context.get("shadow_voices", ""),
    }
    filled_prompt = qtype["prompt"].format(**prompt_vars)

    # Global accessibility constraint for all booklet text
    if query_type != "volume-intro":
        filled_prompt += (
            "\n\nREADING LEVEL: Write at an 8th-grade reading level. Use short sentences. "
            "Prefer common words over specialized ones. If you must use a technical term, "
            "immediately explain it in plain language."
        )

    context_json = json.dumps(context)
    return (
        f'<div class="llm-query" '
        f'data-query-type="{html_mod.escape(query_type)}" '
        f'data-context="{html_mod.escape(context_json)}">'
        f'<p class="query-prompt">{html_mod.escape(filled_prompt)}</p>'
        f'</div>'
    )


def map_groups_to_table_rows(groups: list, table: dict, pass_a_steps: dict) -> dict:
    """Map each group to its matching analytical table row(s).

    Returns dict of group_number -> list of matching table rows.
    """
    rows = table.get("rows", [])
    mapping = {}

    for g in groups:
        group_steps = set(g.get("steps", []))
        group_num = g.get("group_number")
        matched = []
        for row in rows:
            seq_val = row.get("column1", "")
            row_steps = _parse_seq_to_steps(str(seq_val), pass_a_steps)
            row_step_nums = {s.get("step_number") for s in row_steps}
            if row_step_nums & group_steps:
                matched.append(row)
        mapping[group_num] = matched

    return mapping


def _get_negotiation_state(row: dict, column_headers: dict) -> str:
    """Extract the negotiation state / what's happening from a table row."""
    for col, header in column_headers.items():
        if header and ("negotiation" in header.lower() or "state" in header.lower() or "happening" in header.lower()):
            return row.get(col, "")
    return ""


def _get_column_by_keyword(row: dict, column_headers: dict, keyword: str) -> str:
    """Get a column value by keyword match in the header."""
    for col, header in column_headers.items():
        if header and keyword.lower() in header.lower():
            return row.get(col, "")
    return ""

