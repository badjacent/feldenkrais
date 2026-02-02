#!/usr/bin/env python3
"""Integration test: run analysis pipeline against a known lesson text.

Usage:
    python scripts/integration_test.py
    python scripts/integration_test.py --split
    python scripts/integration_test.py --pass-a-provider openai --pass-b-provider openai
    python scripts/integration_test.py --split --pass-b-provider openai

Options:
    --split                  Split Pass B into two queries (narrative + table)
    --pass-a-provider NAME   LLM provider for Pass A (gemini, openai, anthropic)
    --pass-b-provider NAME   LLM provider for Pass B (gemini, openai, anthropic)
"""

import argparse
import json
import sys
import time
import os

# Add src/kb to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "kb"))

# Load env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

LESSON_TEXT = r"""Please go ahead and lie down. Make yourself comfortable. let your legs be long and let them be a little bit apart from each other just comfortably. And feel your contact with the floor. Feel the islands of contact. Notice that some areas of you are more fully supported and enjoy those areas, enjoy that support. Let yourself settle further. And some areas of you are not as supported. And make a note, where are the places where in your back, perhaps, or around the shoulders or your neck, there's a sense of shortness or liftedness behind you. There's no contact and there's almost an unwillingness to let yourself settle to the ground. Which areas are like that? This map of pressures of support and not any support, it's different left and right. you run your attention back and forth and notice. And would you turn your palms down to face the floor and bring your arms comfortably close to yourself and just rest a moment like this. might not be where you chose to position your palms or your hands. You don't have to press them into the floor, but just that they're oriented down towards the floor. And can you feel, if that wasn't where you put them, can you feel why? Can you feel something about the shoulders or your comfort, your chest or your breathing that says, oh, this isn't quite as nice as the place I spontaneously put them. And if there's any substantial discomfort, then of course do whatever you like. If you're able to maintain the arms in this shape, more or less, for this lesson, that might be helpful. Would you bend your knees and bring the soles of your feet to stand on the ground? And take a moment to sense the width of your feet, the width of your knees, is something like the width of your hips or shoulders. And there's a security and stability. There's a really nice, easy placement of the feet. And would you use your feet to press into the ground to direct some pressure vertically up your body towards your head, as if you would drive yourself lightly with your feet in the direction of the crown in the direction of the crown of your head, and then let that pressure go and float back to where you came from, and do that again slowly. This is an important lesson to have a good relationship in terms of friction below your feet. So if you don't have any friction and you'd like a little piece of the tacky stuff for me, I'll bring one along. Or if you want to take off your socks, it's even an option to slide to the bottom of your mat, put your feet on the hardwood floor, but it'll be very helpful today to not be negotiating with slipping. So you press, you move your body up its vertical length toward the crown of your head, and then you just reduce that pressure. And we've done many movements similar to this, often involving an intentional rolling of the pelvis. And tonight's lesson is a little different than that, while your pelvis might roll, your intention is not simply about what we've done before, which might be flattening the back, letting your pelvis roll in the direction of your head, up your mat. It's more that you're thinking that you're delivering force through yourself vertically. You're feeling that you push the weight of yourself upwards along the floor. And then, of course, although you let go of it, you kind of float back where you came from, you might have pushed yourself enough that you've actually slightly migrated, so we can also, between each push, we can pull. You could lightly drag yourself with traction of the feet. You could pull your body in the direction of your heels a little bit on the other end of the movement. And see if these pushes and pulls could start to meld together into a kind of slow, very, very slow vibration along your vertical dimension. And that means that there's no particular interruption anywhere, that it's not a matter of stopping at either end of the movement or stopping in the middle when you're in between pushing and pulling, which is that you simply and lightly direct yourself a little in the direction that is upwards for your body, not upwards towards the ceiling, but upwards for your body, and then downwards for your body, pulling yourself and pushing yourself along your verticality. And while you're doing this, can you check in with your breath and invite a little bit more ease? And just notice, was there a breath strategy you were employing? Do you time your breathing with the movement, or do you hold your breath frequently by accident? you invite an easy fullness of breath, just continuous, wherever you are in the movement. And notice your shoulders. What do the shoulder blades do with this? Could you think of tracking the pressure in the next couple pushes from your feet all the way up through your bones, feet through legs to skeleton, thinking of the pressure going up the links of your spine? mobilizing your ribs slightly, and arriving at your shoulders, and feeling almost like there's a way in which your shoulders are left behind as you push, or your shoulders, the weight of your arms on the floor gives them a sense of groundedness, and this direction, this intention to push up through, runs right through them or runs right over them a little bit. Like in the end, as you push, you get to the apex of your vertical pressure. Your shoulders might feel lower or like they've gone behind you a little more. Maybe that's true. Or maybe your ability to notice a similar idea is more about your neck. the length of your neck and head, the distance from ears to shoulders. What happens as you push and pull? A kind of turtle analogy isn't a bad thing with this, right? There's a little sense of kind of drawing your head into yourself and then pressing up through and sending it a little bit out of the shoulder structure, the neck a little longer, breathing all the while. And would you begin to make it a little bit faster and a little bit lighter?  Exploring how you have to soften yourself. There's kind of an agility in letting tissue go so that you can send this oscillation, this vertical push and pull through yourself really lightly and maybe even quicker. Just pause a moment from that rest as you are. Maybe the pushing has been emphasized a little more. Maybe it would be interesting to pull a little. Could you draw down more intentionally? Can you see how close can you lead your bottom to your heels? your feet stay where they are we drag yourself fully down and starting from there could you push all the way up through from there a little slower again a little fuller can you keep your back more or less relaxed as you draw down through yourself and push up through yourself. And going even slower, giving your attention time to sense the places where the tissue, the connective tissue, the soft parts of you can yield to allow the skeletal force to go very clearly up and down. What happens to the distance between your chin and your sternum? Let this quietly come to a rest and please lengthen your legs and pause. And So this lesson is going to be many quite varied variations on this vertical force idea. That's one of these kind of fundamental concepts of living in the world we live in with the gravity we live in. I want to give you the idea that if there were no ground beneath our feet, of course the Earth's gravity would just drag you eventually right to the center of it. So that's the downward force in your body, but there's an equal and opposite upward force that keeps you at the same altitude unless you're going up an incline or up some stairs or down. Because the floor is there, and we have this hard skeleton that allows us to stand vertically against gravity, we end up in a sense suspended between a crushing downward force and the floor. And we do a remarkable job of organizing ourselves for this to be clear and for us to be functional human beings within this incredible gravitational field, but often there are some clarity missing from how we could shape ourselves, how we could organize our bones to resist gravity very, very efficiently so that our musculature is free to do the things we want to do in life. So we're going to be looking at that tonight more than usual even, although it's always a kind of theme. What is this vertical dimension of ourselves, and how do we bring force clearly through it. Let your legs go a little wider than usual, a little extra wide, and think of your heels on the floor. And could you do a little movement of pushing and pulling your body with your heels? Do you actually think of moving your ankles as if to point the toes and then as if to bring the toes closer to your knees, and you'll feel that your heels will rub the floor. And again, there's a friction moment here, right? So if you like, take the time right now to shift, sit up, and move your friction down to your heels. I think it'll be nice for you to get some more clarity about this, or you can just move your body upwards temporarily. and then get settled again, get your legs a little extra wide. And have the idea that you could push the floor away from you and you could pull the floor closer up to you with your heels. The strange way of interacting with the world. Have your palms facing the floor near your sides. And what's it like to push and pull your body in this way? Do you have some purchase against the floor there? Could you get a little bit of a A little bit of clarity there. Nice and slow. Fine to keep playing with your surfaces or friction. Slow, slow, slow. Figuring out where do you have to firm up yourself to organize this power to come through you, and where do you actually need to learn to get out of the way of the movement? What about the hip joints? can be free and mobile, the belly and the groin muscles, the back, the thighs, buttocks. Push and pull yourself. And feel your arms, your hands, and your shoulders. Can you sense how this movement relates to your arms? and could you actually have the idea of helping with your hands that you would push and pull the ground with your arms, with your hands to assist your heels and slow and breathing letting go of the push letting go of the pull smoothly and pleasantly learning how around your collar and your upper ribs, your shoulder blades, how you can be soft and clear with the direction of the force going through, up your spine, through your shoulders to your head, and also helped by your shoulders and arms, slightly assisted. and by the way in all this what happens with your breathing good and let it be in pause for a moment And would you bend one knee and stand one foot? Your choice. And we'll see, depending on the placement of your foot and how you're organizing now, you might need to move your friction up. You might not, so you're welcome to try with the friction where it is if you don't think you need it or if you're curious. And then would you place the diagonal arm up on the floor above your head? So lay the diagonal arm from the bent knee. If it's your right knee bent, you lay your left arm up, and vice versa. Good. So if your left knee is bent, your right arm is up. And you lay that arm comfortably on the floor above your head. So if it can lie very near your ear, very, very vertical, that's fine. If it needs to be further away, that's okay, too. But so that it can rest up there. And feel this one foot placed on the floor. and could you push and pull yourself a little bit with this one foot? In the same dimension, up and down? Was there a question? The bent one, yep, yep. But you'll feel, although you direct it with the sole of the foot that's placed on the floor, you'll feel that if that long leg is free, that ankle actually is invited to shift a little bit. So the force comes from the bent leg. And your intention is to keep the whole exploration in the plane of the floor, moving up and down along the floor, sending forces vertically through yourself. And of course, they're a little skewed by the one-leggedness of it. It's not perfectly symmetrical. You allow the force to go whatever version of up and down emerges from having one knee bent. and your hips might roll right here your weight might shift a little bit to the side but it's not any part of your intention to lift the pelvis or roll the pelvis or lift a hip doesn't mean you can't do it it really is one of these moments of clarifying what you're trying to do and if it's easiest for you to allow the hip to lift a little bit than it does but this isn't simply pushing the floor with your standing foot and rolling your body off in the direction of the long leg. It's more that you seek to go up and down. And in your pushing and pulling with your one foot, could you think in yourself, what exactly is the direction of force? Like if you could have some kind of mental image, some representation of the push and pull as it goes through your body, where would it be going? Does it go towards your extended arm? Does it go more towards your head?"""


def main():
    parser = argparse.ArgumentParser(description="Integration test for analysis pipeline")
    parser.add_argument("--split", action="store_true", help="Split Pass B into two queries")
    parser.add_argument("--pass-a-provider", default=None, help="LLM provider for Pass A (gemini, openai, anthropic)")
    parser.add_argument("--pass-b-provider", default=None, help="LLM provider for Pass B (gemini, openai, anthropic)")
    parser.add_argument("--dry-run", action="store_true", help="Show config without running")
    args = parser.parse_args()

    from web import _run_analysis, DEFAULT_LLM, LLM_CONFIGS

    pa = args.pass_a_provider or DEFAULT_LLM
    pb = args.pass_b_provider or DEFAULT_LLM

    print(f"=== Integration Test ===")
    print(f"Lesson text: {len(LESSON_TEXT):,} chars")
    print(f"Split Pass B: {args.split}")
    print(f"Pass A provider: {pa} ({LLM_CONFIGS[pa]['model']})")
    print(f"Pass B provider: {pb} ({LLM_CONFIGS[pb]['model']})")
    print()

    if args.dry_run:
        print("Dry run — not calling LLMs.")
        return

    print("Running analysis...")
    t0 = time.time()

    result = _run_analysis(
        LESSON_TEXT,
        split=args.split,
        pass_a_provider=args.pass_a_provider,
        pass_b_provider=args.pass_b_provider,
    )

    elapsed = time.time() - t0
    timings = result.get("timings", {})

    print()
    print(f"=== Results ===")

    # Timings
    if timings.get("cached"):
        print("Source: CACHED")
    else:
        print(f"Total time: {elapsed:.1f}s")
        for k, v in timings.items():
            if k != "total":
                print(f"  {k}: {v}s")

    # Pass A summary
    pa_result = result.get("pass_a_result", {})
    print(f"\nPass A:")
    print(f"  Title: {pa_result.get('lesson_title', 'N/A')}")
    print(f"  Primary: {pa_result.get('primary_framework', 'N/A')}")
    print(f"  Secondary: {pa_result.get('secondary_frameworks', [])}")
    print(f"  Steps: {len(pa_result.get('steps', []))}")
    print(f"  Home position: {pa_result.get('home_position', 'N/A')}")
    classifications = pa_result.get("classifications", {})
    for k, v in sorted(classifications.items(), key=lambda x: -x[1]):
        print(f"    {k}: {v}")

    # Pass B summary
    vl_analyses = result.get("vl_analyses", {})
    for fw, entry in vl_analyses.items():
        status = entry.get("status", "unknown")
        print(f"\nPass B ({fw}): {status}")
        if status == "complete":
            data = entry.get("result", {})
            # Narrative
            neg = data.get("negotiation_framework", {})
            if neg:
                print(f"  Striver observations: {len(neg.get('striver_observations', []))}")
                print(f"  Foundation observations: {len(neg.get('foundation_observations', []))}")
            narrative = data.get("somatic_narrative", {})
            if narrative:
                for key in ("experiential_voice", "person_inside_voice"):
                    val = narrative.get(key, "")
                    if val:
                        print(f"  {key}: {len(val)} chars")
            # Table
            table = data.get("analytical_table", {})
            if table:
                rows = table.get("rows", [])
                print(f"  Table rows: {len(rows)}")
                # Check for truncation
                if rows:
                    last_row = rows[-1]
                    cols_with_content = sum(1 for k, v in last_row.items() if v)
                    total_cols = len(last_row)
                    print(f"  Last row completeness: {cols_with_content}/{total_cols} fields filled")
            # Total output size
            output_json = json.dumps(data)
            print(f"  Total output: {len(output_json):,} chars")
        elif status == "error":
            error = entry.get("error", "")
            # Show first 200 chars of error
            print(f"  Error: {error[:200]}")
            if len(error) > 200:
                print(f"  ... ({len(error):,} chars total)")

    # Debug log summary
    debug_log = result.get("debug_log", [])
    if debug_log:
        print(f"\nDebug log: {len(debug_log)} exchanges")
        for i, entry in enumerate(debug_log):
            name = entry.get("name", f"Query {i}")
            provider = entry.get("provider", "?")
            prompt_len = len(entry.get("prompt", ""))
            if "output" in entry:
                output_len = len(json.dumps(entry["output"]))
                print(f"  {name} [{provider}]: prompt={prompt_len:,} chars, output={output_len:,} chars")
            elif "error" in entry:
                print(f"  {name} [{provider}]: prompt={prompt_len:,} chars, ERROR")

    print(f"\nDone.")


if __name__ == "__main__":
    main()
