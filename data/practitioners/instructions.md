# Practitioner Research Instructions

## Goal

Build a comprehensive directory of people who trained directly with Moshe Feldenkrais, their lineage, and a catalog of available Feldenkrais resources on the web.

## Files

- `directory.md` — Human-readable summary of practitioners organized by training cohort
- `tracker.json` — Machine-readable research state for each practitioner
- `instructions.md` — This file

## tracker.json Schema

Each practitioner entry in `tracker.json` has a slug key (e.g. `mia-segal`) and these fields:

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Full name |
| `status` | string | `not_started`, `in_progress`, or `complete` |
| `training` | string | Training cohort: `tel_aviv_1969`, `san_francisco_1975`, `amherst_1980`, or `post_feldenkrais` |
| `relationship` | string | How they knew Feldenkrais, when they studied with him |
| `deceased` | string or null | Date or year of death, null if alive or unknown |
| `born` | string or null | Birth date if known |
| `born_location` | string or null | Birthplace if known |
| `publications` | array of strings | Books, audio programs, video programs, articles. Include publisher, year, format. |
| `students` | array of strings | Notable students they trained, with brief identifying info |
| `materials` | array of {url, desc} | URLs where their materials can be purchased or accessed online |
| `websites` | array of strings | Official websites, organizations founded |
| `podcasts` | array of strings | Podcast episodes featuring them or about their work |
| `notes` | string | Expanded biography: career highlights, organizations, awards, teaching history, unique contributions |

## Research Process

### Adding a new practitioner

1. Add an entry to `tracker.json` with status `not_started` and whatever seed info you have
2. Update `directory.md` if they belong to a training cohort not yet listed

### Researching a practitioner

1. Set status to `in_progress` in tracker.json
2. Web search for the practitioner using multiple queries:
   - `"[Name] Feldenkrais"` — primary search
   - `"[Name] Feldenkrais practitioner"` — if name is common
   - Their known website domains
   - `feldenkraisresources.com` listings (major publisher/retailer)
   - `feldenkraisaccess.com` listings (online platform)
   - Amazon/book retailer listings for publications
   - Podcast directories (Ryan Nagy Feldenkrais Podcast is a good source)
   - `movementandcreativity.com` (subscription platform with many teachers)
   - Memorial/obituary pages for deceased practitioners
3. Fill in all fields in tracker.json. For fields with no findings, leave as empty array `[]` or empty string `""`
4. Set status to `complete`
5. Update `directory.md` if new significant info warrants it

### What to look for

**Publications**: Books, audio lesson sets (CDs, MP3s, downloads), video programs (DVDs, streaming), journal articles, book chapters/forewords. Always note publisher, year, and format when available.

**Students**: People they trained who went on to become practitioners or trainers themselves. Include the student's current role/location for context.

**Materials**: Prioritize currently-available resources. Include:
- Direct purchase links (their own site, Feldenkrais Resources, Amazon)
- Free resources (Archive.org, YouTube channels, free podcast episodes)
- Subscription platforms where their lessons appear
- Training program websites they direct

**Websites**: Their personal/business site, organizations they founded, training program sites.

**Podcasts**: Specific episodes (not just "they have a podcast"). Include show name and episode title/date.

**Notes**: Biographical narrative. Include birth/death info, education, how they came to Feldenkrais, career trajectory, organizations founded, awards/honors, geographic base, what they're known for in the community.

### Quality standards

- Prefer primary sources (their own website, publisher listings) over secondhand mentions
- Include URLs wherever possible — the goal is a *resource catalog*, not just biography
- Note when information is uncertain (e.g. "likely deceased based on birth year ~1915")
- For practitioners with very limited online presence, note that explicitly rather than leaving fields empty without explanation
- Don't fabricate — if a web search turns up nothing, say so

### Training cohorts

| Cohort | Location | Years | Approx. Size |
|--------|----------|-------|-------------|
| `tel_aviv_1969` | 51 Nachmani St, Tel Aviv | 1969-1971 | 13 students |
| `san_francisco_1975` | Lone Mountain College, SF | 1975-1978 | ~65 students |
| `amherst_1980` | Hampshire College, Amherst MA | 1980-1983 | ~235 students |
| `post_feldenkrais` | Various | After 1984 | — |

The Tel Aviv 13 are the most historically important — they had the most direct, sustained contact with Feldenkrais. The SF and Amherst graduates who became trainers are next in importance. `post_feldenkrais` entries are included only for people with notable publications or outsized influence on the field.

### Expanding the practitioner list

The current list is not exhaustive. Good candidates to add:
- SF or Amherst graduates who became prominent trainers but aren't listed yet
- Practitioners with significant published materials (books, large audio/video catalogs)
- People who founded major training institutes or organizations
- International figures who brought the Method to new countries

When adding someone, note in their `relationship` field whether they trained directly with Feldenkrais or are second-generation.
