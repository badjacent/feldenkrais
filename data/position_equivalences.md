# Position Equivalence Test Dataset

Pairs of body position descriptions with expected similarity scores (0-10).
10 = same physical configuration. 0 = completely different.

Used to test whether LLMs can do spatial/proprioceptive reasoning
about body positions described in natural language.

## Pairs

| id | a | b | expected | category |
|----|---|---|----------|----------|
| 1 | stand the feet | knees pointing to the ceiling | 10 | lying basics |
| 2 | stand the feet | legs long on the floor | 0 | lying basics |
| 3 | lie on your back | supine | 10 | position name |
| 4 | lie on your stomach | prone | 10 | position name |
| 5 | lie on your side | lateral position | 10 | position name |
| 6 | interlace your fingers behind your head | cradle the head with both hands | 9 | hand position |
| 7 | interlace your fingers behind your head | hands resting at your sides | 0 | hand position |
| 8 | arms reaching toward the ceiling | hands pointing up | 9 | arm position |
| 9 | cross your right knee over your left | right leg resting on top of left leg | 9 | leg crossing |
| 10 | right ankle resting on left knee | figure four position | 10 | leg crossing |
| 11 | right ankle resting on left knee | right knee crossed over left knee | 4 | leg crossing — different configs |
| 12 | tilt both knees to the right | let the legs fall to the right side | 9 | leg movement |
| 13 | slide your right foot down your left shin | extend the right leg while it's crossed over the left | 8 | compound movement |
| 14 | roll your head to the right | turn your face to the right | 8 | head movement |
| 15 | roll your head to the right | look to the right with your eyes | 3 | head vs eyes |
| 16 | lift your head | bring your chin toward your chest | 7 | head — similar but not identical |
| 17 | lift your head | tilt your head back | 1 | head — opposite directions |
| 18 | round your back | flex your spine | 8 | spine |
| 19 | arch your back | extend your spine | 8 | spine |
| 20 | round your back | arch your back | 1 | spine — opposites |
| 21 | press your lower back into the floor | flatten the lumbar curve | 9 | spine — same action |
| 22 | press your lower back into the floor | arch your lower back away from the floor | 1 | spine — opposites |
| 23 | standing foot | foot flat on the floor with knee bent | 10 | foot — lying context |
| 24 | standing foot | foot that is bearing weight in standing | 10 | foot — standing context |
| 25 | lengthen your right leg | slide your right heel away from you | 8 | leg extension |
| 26 | lengthen your right leg | lift your right leg | 2 | different actions |
| 27 | bring your elbow toward your knee | twist your torso | 6 | implies twisting but more specific |
| 28 | palms together above your chest | hands in prayer position reaching to ceiling | 9 | hand position |
| 29 | palms together above your chest | arms crossed over your chest | 2 | different positions |
| 30 | take hold of your right knee with your left hand | left hand on right knee | 10 | same grip |
| 31 | both knees standing, feet apart | hook lying position | 9 | clinical vs colloquial |
| 32 | sit cross-legged | sit in easy pose | 9 | same position different traditions |
| 33 | come to hands and knees | quadruped position | 10 | position name |
| 34 | come to hands and knees | crawling position | 8 | close but crawling implies movement |
| 35 | let your right knee open to the side | right knee falls outward | 9 | same movement |
| 36 | let your right knee open to the side | right knee crosses over to the left | 1 | opposite directions |
| 37 | one foot standing, other leg long | asymmetrical supine with one knee bent | 9 | same config |
| 38 | head heavy in your hands | let the weight of your head sink into your palms | 10 | same image |
| 39 | find the contact of your shoulder blades with the floor | sense where your upper back touches the ground | 9 | same attention |
| 40 | walk your feet a little to the left | shift your base of support to the left | 7 | same action described differently |
