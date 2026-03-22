# Project Enhancement Prompts

## 1. Interiority Language Analyzer
Build an analysis pass that scores each lesson segment for "interiority density" — how much the teacher's language directs attention to inner body space vs. external movement. Use the research from the interiority booklet (motor imagery, container schemas, Leder's absent body) to define scoring criteria. Compare teachers against each other. Surface the most interior-directed moments across the entire corpus as a searchable collection. Wire it into the existing pass0b pipeline as a new step.

## 2. Lesson Similarity Network
Build a visual graph (force-directed or spatial) showing how lessons relate to each other based on their embeddings, concept tags, and spatial analysis. Cluster by body region, movement quality, and teacher. Make it interactive — clicking a node opens the lesson, hovering shows shared concepts with neighbors. Deploy it as a new route. Use the existing pgvector embeddings and concept tags as the data source, no new LLM calls needed.

## 3. Teaching Voice Fingerprints
Each teacher in the corpus has a distinctive verbal style — Gibbons uses anatomical precision, Haller uses philosophical framing, Feldenkrais Project teachers vary widely. Build a "voice fingerprint" for each teacher by aggregating their language analysis results across all their lessons. Visualize as radar charts (dimensions: directiveness, metaphor density, anatomical specificity, interiority focus, question frequency). Let users compare any two teachers side-by-side. This creates a novel dataset about somatic education pedagogy that doesn't exist anywhere.

## 4. Lesson Recommender
Build a recommendation engine that suggests lessons based on what a user has explored. Track which lessons each logged-in user has viewed/analyzed. Use the concept tags and embeddings to find "adjacent" lessons — similar enough to build on what they've done, different enough to expand their range. Add a "surprise me" mode that deliberately picks lessons from underexplored body regions or movement qualities. Surface recommendations on the landing page for logged-in users.

## 5. Movement Sequence Extraction & Cross-Reference
The pass_a decomposition already breaks lessons into steps. Build a system that extracts canonical movement sequences (e.g., "supine → bend knees → tilt knees side to side → extend one leg") and finds the same or similar sequences across different lessons and teachers. This would reveal how different teachers approach the same movement vocabulary — who adds what, who skips what, where the variations are. Display as a timeline comparison view.

## 6. Live Lesson Companion
Build a real-time mode where a user pastes or streams a lesson transcript as they're taking a class, and the system provides live analysis: which body regions are being addressed, what the biomechanical focus is, how this moment connects to earlier moments in the lesson, and links to similar passages in other lessons they've studied. Use the existing analysis pipeline in streaming/incremental mode. Could start as a simple paste-and-analyze interface, evolve toward real-time audio transcription integration.
