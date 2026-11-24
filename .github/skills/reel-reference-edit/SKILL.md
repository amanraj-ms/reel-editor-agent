---
name: reel-reference-edit
description: Analyze sample reels and build a frame-based FFmpeg edit plan with original footage, caption fades, crop control, photo motion, optional slow motion and authorized music.
---

# Reference-driven edit

Begin only after the media review has explicitly reported enough usable
material for the proposed scope. Request the sample at that point, not at the
initial greeting. Keep samples already supplied. If the user has none, obtain
confirmation to design an original concept instead.

Use the [shared CLI](../../../scripts/media_tools.py) and
[example plan](../../../examples/plan.example.json).

```sh
python3 scripts/media_tools.py reference --input "/absolute/sample.mp4" --output "jobs/reference-01"
```

The reference command creates timestamped sheets, metadata, scene-change
candidates and an audio RMS envelope. These are evidence, not an automatic edit
decision: a scene detector can mistake motion for cuts and miss fades; RMS
peaks aren't necessarily musical beats. Inspect frames immediately around
candidate cuts and fading text. If tools support playback, review sound and
motion too; otherwise explicitly state that limitation.

Record in the plan's `brief`: primary reference, caption copy, duration,
orientation, palette/font hierarchy, opening/closing fades, source exclusions
and confirmed music permission. Transcribe only the short caption passages
needed for analysis; write original text for the new reel.

## Timing and selection

- Derive shot boundaries in integer output frames. A 706-frame reel at 30 fps
  lasts exactly 706/30 seconds. Cumulative shot frames define each cut.
- Use the PRIMARY sample's cut grid only when reusing that same authorized
  soundtrack from its beginning. New music needs a new rhythm/phrase analysis,
  not blindly recycled timestamps.
- Preserve approved timing when swapping visuals. Start with scenic caption
  backgrounds, vary the quick montage, then allow meaningful portraits to hold.
- Treat user-labeled subjects separately: `male`, `female`, `couple`, `action`,
  `scenery` or any user-defined label. For equal solo coverage configure BOTH
  `equal_photo_counts` and `equal_subject_frames`; the latter includes video.
  Couple screen time is independent. Labels are editorial inputs, not inferred
  identity classifications.
- Store absolute source paths and `fingerprint` objects from the latest
  inventory in every shot and audio entry. The CLI rejects changed/deleted
  inputs before rendering. Generated prepared JPEGs are not source identities.
- `speed: 0.5` uses half-speed source playback; only use it when the source has
  enough actual frames for the requested output fps. The renderer rejects
  frame duplication caused by an unsuitable slow-motion rate.
- `orientation` is `upright` or `sideways`; sideways means clockwise rotation of
  a landscape composition INSIDE a portrait output, not metadata rotation.
- `crop_center: [x,y]` ranges 0..1, defaults to center. Photos get a gentle 3.5%
  push-in. Check subjects remain safe through the push-in.

## Captions, sound and render

Captions use one or more lines, per-line size/color, a center `y` position in the
logical canvas, integer start/end frames and opacity fades. Pick a local font
path; CLI doctor lists candidates. No shell text expansion or fragile drawtext
escaping is used. Typography is rasterized with Pillow.

Reference audio requires explicit permission; set `audio.authorized: true`
only after the user confirms. If permission isn't given, omit audio for a silent
draft or request a licensed/local track. No downloading music or watermark
removal. Original voices require consent before reuse.

```sh
python3 scripts/media_tools.py render --plan "jobs/edit-plan.json" --output "jobs/reel-v1"
```

Before rendering, present the reference analysis and a concrete edit concept:
duration, framing, caption text/design, ordered scene beats, proposed assets,
subject counts/time, music rhythm, transitions and ending. Explain differences
from the sample. Ask for explicit approval of that concept and STOP to await
the user's answer. File uploads, initial editing requests and music permission
are not approval. Save the concept version and actual approval message in
`brief.workflow`. Material changes require renewed approval; a user's explicit
narrow revision request authorizes only that revision to an approved edit.

Render only after this approval, visual source review and resolution of
creative ambiguities. These are agent workflow gates, not authentication
enforced by the standalone CLI. The renderer uses uniform SDR BT.709, fixed fps, exact
per-shot frame counts, muted source video tracks and optional external audio.
It writes `timeline.json`, `reel.mp4`, `preview.jpg` and `validation.json` in a
new directory. Do not deliver a failed or partial render as success.
