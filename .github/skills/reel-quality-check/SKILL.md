---
name: reel-quality-check
description: Verify rendered reels and revisions for exact frame counts, shot timing, equal subject coverage, full audio, soundtrack offset, crop safety and fade behavior before delivery.
---

# Validate and revise

Use the [shared CLI](../../../scripts/media_tools.py):

```sh
python3 scripts/media_tools.py validate --job "jobs/reel-v1"
```

Rendering runs this automatically too. Validation must pass before delivery.

## What automation proves

- Current input fingerprints still match the approved plan.
- Dimensions, pixel format, fps, exact video frame count and every frame's
  presentation timestamp meet the integer-frame timeline.
- Full video/audio decode emits no errors.
- Authorized soundtrack starts at zero and lasts for the whole reel; output
  audio/video durations match to within one frame.
- Decoded soundtrack waveform correlation in several windows detects offset
  within a +/-50 ms search at 1 ms resolution (excluding opening/closing fades).
  Silence or poor correlation is an explicit validation failure, not a pass.
  The check is not lip-sync, music beat detection, or proof of subjective pacing.
- User-specified category counts and exact total subject frames meet the plan.
- Preview samples are made at shot centers so all shots can be inspected.

## What you must inspect

1. Open every output preview sheet. Check actual sources, the expected scene at
   each shot, orientation, caption legibility, face/head/feet crops, no unwanted
   beach/other excluded content and no inappropriate exposure changes.
2. Inspect first/middle/last frames of crops and fades. Frame counts alone cannot
   prove the right image is shown at a cut. Compare output to chosen source
   moments near each cut; use low-resolution pixel comparison if useful.
3. Preview motion/audio with playback tools when available; disclose if only
   sampled frames and decoded waveforms were inspected.
4. Check count balance AND time balance. A long closing fade still occupies
   nominal timeline time; do not use that to hide solo-coverage inequality.
5. If any check fails, fix the root cause and render to the next versioned
   directory. Mixed JPEG full-range / video limited-range filters can reset
   overlay timing and corrupt audio timestamps; keep every segment identical
   in dimensions, fps, pixel format, color range, primaries and time base.
6. Do not overwrite or delete prior versions. Temporary render segments are
   created in a narrowly scoped temporary directory and removed automatically.
   On failure, preserve job inputs and diagnostics; do not erase evidence.

Deliver the MP4, contact sheet, reproducible plan/timeline and validation report.
State the exact checks completed and any remaining visual/auditory uncertainty.
