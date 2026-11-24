---
name: reel-media-review
description: Inventory and visually review local videos and photos for reel editing. Use for new media folders, replacement footage, contact sheets, quality checks or balanced shot selection.
---

# Review media

Run commands from the project root using an available Python 3.9+ interpreter.
The [local CLI](../../../scripts/media_tools.py) requires FFmpeg, ffprobe and
Pillow. Do not install anything without a missing-dependency failure or an
approved dependency change.

```sh
python3 scripts/media_tools.py doctor
python3 scripts/media_tools.py scan --input "/absolute/media" --output "jobs/trip-review"
```

`scan` recursively discovers supported video/image files, probes rotation,
frame rate, color transfer and resolution, fingerprints each source, and writes
`inventory.json` and numbered JPEG contact sheets. The output must be new and
outside the input tree. HEIC uses Pillow if supported, then macOS `sips` if
available. Decode failures are explicit errors in the inventory and a nonzero
exit. HDR video is catalogued but not rendered into misleading SDR thumbnails.

1. Open EVERY sheet using image-viewing tools. Record scene, people labels
   supplied by the user, framing, quality concerns, duplicate alternatives and
   candidate source ranges in `selection-notes.json` in the job directory.
2. For clips longer than 30 seconds or changing scenes, sample more densely:
   `scan --samples 16` into a separate new review directory, using a narrower
   input subfolder if appropriate. For specific candidate ranges, extract a
   short local preview and inspect motion; contact sheets cannot establish
   smoothness, expression timing or lack of brief obstructions.
3. Reject blurry starts, blocked faces, camera drops, accidental rolls and
   unwanted scenes. Prefer distinctive road, landscape, human and detail shots
   over many interchangeable POVs. Don't use every asset just because it exists.
4. Full-decode prospective sources with:
   `ffmpeg -v error -nostdin -i INPUT -map 0:v:0 -an -f null -`
   Keep errors in the review report. Scanning alone is NOT full decoding.
5. HDR (`arib-std-b67` / `smpte2084`) needs a separately verified tone-mapped SDR
   derivative, with the original retained and lineage recorded. This CLI blocks
   HDR rendering. Check FFmpeg's available color-conversion filters before
   proposing a conversion. Never simply relabel HDR as BT.709.
6. Photos must honor EXIF orientation. Pick wide images for sideways/landscape
   slots, tall ones for upright slots. Set crop centers per shot and inspect
   first/middle/last frames, especially around faces and feet.
7. Prefer current originals to WhatsApp/recompressed copies. On replacements,
   make a new inventory and remap by current path and fingerprint, not old IDs.

## Required sufficiency checkpoint

Before requesting or analyzing a sample reel, tell the user whether the reviewed
material is sufficient for a draft and why. Include total/usable photo and video
counts, usable ranges, distinct scenes, crop/quality limitations and a feasible
duration. Do not equate many files with enough good footage, invent a fixed
minimum or require both photos and videos.

If sufficient, proceed to request the sample (or acknowledge one already
provided). If insufficient, explain exactly what to add, or offer a shorter or
simpler edit and wait for the user's response. Reassess after reference review
if its pacing/coverage demands exceed the available material. Record evidence
in `brief.workflow.media_assessment`; the assistant makes this editorial
assessment, not the scan command.

Never upload media to a service, scan unrelated folders, extract GPS/person
metadata for reports, or modify originals. Local analysis is local; Copilot
image-viewing tools may send viewed frames to its configured model, so don't
promise offline AI or confidentiality beyond the host's data-handling settings.
