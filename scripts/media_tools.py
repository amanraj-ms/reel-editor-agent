#!/usr/bin/env python3
"""Local media evidence, frame-based reel rendering and validation."""

import argparse
import array
import io
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError


VIDEOS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff"}
HDR = {"arib-std-b67", "smpte2084"}
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Narrow Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


class MediaError(Exception):
    """A user-actionable media or plan failure."""


def run(args: list[str]) -> bytes:
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise MediaError(
            f"{args[0]} failed ({result.returncode}):\n"
            f"{result.stderr.decode(errors='replace')[-8000:]}"
        )
    return result.stdout


def ffmpeg(args: list[str]) -> bytes:
    return run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *args])


def write_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, allow_nan=False)
        file.write("\n")


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise MediaError(f"Expected a JSON object in {path}")
    return value


def fingerprint(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def source_path(entry: dict[str, Any]) -> Path:
    raw = entry.get("source")
    if not isinstance(raw, str) or not Path(raw).is_absolute():
        raise MediaError("Every source must be an absolute local path")
    path = Path(raw).resolve(strict=True)
    if not path.is_file():
        raise MediaError(f"Not a source file: {path}")
    if entry.get("fingerprint") != fingerprint(path):
        raise MediaError(f"Source changed or fingerprint is missing; rescan: {path}")
    return path


def number(value: Any, label: str, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MediaError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise MediaError(f"{label} must be finite and >= {minimum}")
    return result


def integer(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MediaError(f"{label} must be an integer >= {minimum}")
    return value


def probe(path: Path) -> dict[str, Any]:
    data = json.loads(run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path),
    ]))
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    duration = float(data["format"].get("duration", 0))
    result: dict[str, Any] = {"duration": duration, "has_audio": audio is not None}
    if audio:
        result["audio_duration"] = float(audio.get("duration", duration))
        result["audio_start"] = float(audio.get("start_time", 0))
    if video:
        rotation = next(
            (s["rotation"] for s in video.get("side_data_list", []) if "rotation" in s),
            float(video.get("tags", {}).get("rotate", 0)),
        )
        w, h = video["width"], video["height"]
        dw, dh = (h, w) if abs(rotation) % 180 == 90 else (w, h)
        a, b = video["avg_frame_rate"].split("/")
        result.update(
            width=w, height=h, display_width=dw, display_height=dh, rotation=rotation,
            fps=float(a) / float(b) if float(b) else 0,
            color_transfer=video.get("color_transfer", "unknown"),
            codec=video["codec_name"], pix_fmt=video["pix_fmt"],
            frames=int(video["nb_frames"]) if video.get("nb_frames", "").isdigit() else None,
            video_duration=float(video.get("duration", duration)),
            video_start=float(video.get("start_time", 0)),
        )
    return result


def new_output(path: Path, inputs: list[Path]) -> Path:
    path = path.resolve()
    for source in inputs:
        source = source.resolve()
        if path == source or source in path.parents:
            raise MediaError("Output must be outside every input folder/file")
    if path.exists():
        raise MediaError(f"Output already exists; choose a new version: {path}")
    path.mkdir(parents=True)
    return path


def open_photo(path: Path) -> Image.Image:
    try:
        with Image.open(path) as raw:
            return ImageOps.exif_transpose(raw).convert("RGB")
    except UnidentifiedImageError as exc:
        if path.suffix.lower() not in {".heic", ".heif"} or not shutil.which("sips"):
            raise MediaError(f"Cannot decode image; supply a supported conversion: {path}") from exc
        with tempfile.TemporaryDirectory(prefix="reel-heic-") as directory:
            converted = Path(directory) / "converted.jpg"
            run(["sips", "-s", "format", "jpeg", str(path), "--out", str(converted)])
            with Image.open(converted) as raw:
                return ImageOps.exif_transpose(raw).convert("RGB")


def video_frame(path: Path, seconds: float) -> Image.Image:
    image = ffmpeg([
        "-threads", "1", "-ss", str(seconds), "-i", str(path), "-frames:v", "1",
        "-vf", "scale=300:300:force_original_aspect_ratio=decrease",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-threads", "1", "pipe:1",
    ])
    with Image.open(io.BytesIO(image)) as raw:
        return raw.convert("RGB")


def sheets(cells: list[tuple[str, Image.Image]], output: Path, prefix: str) -> list[str]:
    paths = []
    for start in range(0, len(cells), 16):
        chunk = cells[start:start + 16]
        sheet = Image.new("RGB", (1280, math.ceil(len(chunk) / 4) * 350), "#171717")
        draw = ImageDraw.Draw(sheet)
        for i, (label, image) in enumerate(chunk):
            x, y = i % 4 * 320, i // 4 * 350
            small = ImageOps.contain(image, (310, 300))
            sheet.paste(small, (x + (320 - small.width) // 2, y + 32 + (300 - small.height) // 2))
            draw.text((x + 5, y + 5), label[:48], fill="white")
        name = f"{prefix}{'-' + str(start // 16 + 1) if start else ''}.jpg"
        sheet.save(output / name, quality=92)
        paths.append(name)
    return paths


def scan(input_path: Path, output: Path, samples: int) -> dict[str, Any]:
    input_path = input_path.resolve(strict=True)
    if not 1 <= samples <= 64:
        raise MediaError("samples must be between 1 and 64")
    output = new_output(output, [input_path])
    files = sorted(
        p for p in (input_path.rglob("*") if input_path.is_dir() else [input_path])
        if p.is_file() and p.suffix.lower() in VIDEOS | IMAGES
    )
    if not files:
        raise MediaError("No supported media found")
    rows = []
    failed = []
    sheet_paths = []
    cells: list[tuple[str, Image.Image]] = []
    for index, path in enumerate(files, 1):
        row: dict[str, Any] = {
            "id": index, "source": str(path.resolve()), "fingerprint": fingerprint(path),
            "kind": "video" if path.suffix.lower() in VIDEOS else "photo",
        }
        try:
            if row["kind"] == "photo":
                image = open_photo(path)
                row.update(width=image.width, height=image.height)
                cells.append((f"#{index} {path.name}", ImageOps.contain(image, (300, 300))))
            else:
                row.update(probe(path))
                if "width" not in row or row["duration"] <= 0:
                    raise MediaError(f"No usable video stream: {path}")
                if row["color_transfer"] in HDR:
                    row["warning"] = "HDR: review a verified tone-mapped SDR derivative before selection"
                    placeholder = Image.new("RGB", (300, 240), "#292929")
                    ImageDraw.Draw(placeholder).text((12, 100), "HDR - needs tone mapping", fill="white")
                    cells.append((f"#{index} {path.name}", placeholder))
                else:
                    for j in range(samples):
                        time = row["duration"] * (j + 0.5) / samples
                        cells.append((f"#{index} {path.name} {time:.2f}s", video_frame(path, time)))
            row["review_status"] = "needs_visual_review"
        except (MediaError, OSError, ValueError) as exc:
            row["error"] = str(exc)
            failed.append(str(path))
        rows.append(row)
        if len(cells) >= 16:
            batch = sheets(cells, output, f"contact-{index:04d}")
            sheet_paths.extend(batch)
            cells.clear()
        print(f"Scanned {index}/{len(files)}: {path.name}", flush=True)
    if cells:
        sheet_paths.extend(sheets(cells, output, "contact-last"))
    report = {"input": str(input_path), "assets": rows, "sheets": sheet_paths, "failures": failed}
    write_json(output / "inventory.json", report)
    if failed:
        raise MediaError(f"{len(failed)} assets failed; see {output / 'inventory.json'}")
    return report


def pcm(path: Path) -> array.array:
    values = array.array("f")
    values.frombytes(ffmpeg([
        "-i", str(path), "-map", "0:a:0", "-af", "asetpts=N/SR/TB",
        "-ar", "8000", "-ac", "1", "-f", "f32le", "pipe:1",
    ]))
    if sys.byteorder != "little":
        values.byteswap()
    return values


def reference(input_path: Path, output: Path) -> None:
    data = scan(input_path, output, 16)
    if len(data["assets"]) != 1 or data["assets"][0]["kind"] != "video":
        raise MediaError("A reference must be one video file")
    asset = data["assets"][0]
    if asset.get("color_transfer") in HDR:
        raise MediaError("Tone-map the HDR reference before style analysis")
    args = [
        "ffmpeg", "-hide_banner", "-nostdin", "-i", str(input_path.resolve()),
        "-vf", "scale=320:-2,select='gt(scene,0.12)',showinfo", "-an", "-f", "null", "-",
    ]
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise MediaError(result.stderr.decode(errors="replace")[-8000:])
    cuts = [float(t) for t in re.findall(r"pts_time:([0-9.]+)", result.stderr.decode())]
    envelope = []
    if asset["has_audio"]:
        values = pcm(input_path)
        for start in range(0, len(values), 400):
            window = values[start:start + 400]
            envelope.append({
                "seconds": start / 8000,
                "rms": math.sqrt(sum(x * x for x in window) / len(window)),
            })
    write_json(output / "reference.json", {
        "source": asset, "candidate_cuts_seconds": cuts, "audio_rms_50ms": envelope,
        "warning": "Candidates need visual/musical review; this is not beat detection",
    })


def coverage(timeline: list[dict[str, Any]], balance: dict[str, Any]) -> dict[str, Any]:
    photos = Counter(s["subject"] for s in timeline if s["kind"] == "photo")
    frames: Counter[str] = Counter()
    for shot in timeline:
        frames[shot["subject"]] += shot["frames"]
    for key, counts in [("equal_photo_counts", photos), ("equal_subject_frames", frames)]:
        labels = balance.get(key, [])
        if not isinstance(labels, list) or not all(isinstance(s, str) and s for s in labels):
            raise MediaError(f"balance.{key} must be a list of nonempty subject labels")
        if labels and (len(set(labels)) < 2 or any(counts[s] == 0 for s in labels)
                       or len({counts[s] for s in labels}) != 1):
            raise MediaError(f"Balance failed for {key}: {dict(counts)}; required {labels}")
    return {"photo_counts": dict(photos), "subject_frames": dict(frames)}


def validate_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(plan.get("balance", {}), dict):
        raise MediaError("balance must be an object")
    if plan.get("audio") is not None and not isinstance(plan["audio"], dict):
        raise MediaError("audio must be an object or null")
    if not isinstance(plan.get("captions", []), list):
        raise MediaError("captions must be a list")
    width = integer(plan.get("width", 1080), "width", 2)
    height = integer(plan.get("height", 1920), "height", 2)
    fps = integer(plan.get("fps", 30), "fps", 1)
    if width % 2 or height % 2 or width > 2160 or height > 3840 or fps > 60:
        raise MediaError("Use even dimensions up to 2160x3840 and fps <= 60")
    raw_shots = plan.get("shots")
    if not isinstance(raw_shots, list) or not raw_shots:
        raise MediaError("shots must be a nonempty array")
    timeline = []
    cursor = 0
    for index, shot in enumerate(raw_shots, 1):
        if not isinstance(shot, dict):
            raise MediaError(f"shot {index} must be an object")
        path = source_path(shot)
        frames = integer(shot.get("frames"), f"shot {index} frames", 1)
        kind = shot.get("kind")
        if kind not in {"photo", "video"}:
            raise MediaError(f"Invalid kind for shot {index}")
        if path.suffix.lower() not in (IMAGES if kind == "photo" else VIDEOS):
            raise MediaError(f"Source suffix does not match shot kind: {path}")
        orientation = shot.get("orientation", "upright")
        if orientation not in {"upright", "sideways"}:
            raise MediaError(f"Invalid orientation in shot {index}")
        center = shot.get("crop_center", [0.5, 0.5])
        if not isinstance(center, list) or len(center) != 2:
            raise MediaError("crop_center must be [x, y]")
        if any(number(c, "crop_center") > 1 for c in center):
            raise MediaError("crop_center coordinates must be between 0 and 1")
        subject = shot.get("subject", "scenery")
        if not isinstance(subject, str) or not subject:
            raise MediaError("subject must be a nonempty user-supplied label")
        start = number(shot.get("source_start", 0), "source_start")
        speed = number(shot.get("speed", 1), "speed", 0.05)
        if kind == "photo":
            if speed != 1 or start != 0:
                raise MediaError("Photo shots cannot have source_start or speed adjustments")
        else:
            metadata = probe(path)
            if "fps" not in metadata or metadata["color_transfer"] in HDR:
                raise MediaError(f"Requires a usable SDR video; tone-map HDR separately: {path}")
            if start + frames / fps * speed > metadata["video_duration"] + 0.001:
                raise MediaError(f"Source range exceeds clip duration for shot {index}")
            if metadata["fps"] + 0.15 < fps / speed and speed < 1:
                raise MediaError(f"Insufficient source fps for slow motion in shot {index}")
        timeline.append({
            **shot, "shot": index, "source": str(path), "subject": subject,
            "source_start": start, "speed": speed, "crop_center": center,
            "orientation": orientation, "start_frame": cursor, "end_frame": cursor + frames,
        })
        cursor += frames
    if cursor > fps * 600:
        raise MediaError("Reels longer than ten minutes are outside this tool's scope")
    for key in ["fade_in_frames", "fade_out_frames"]:
        if integer(plan.get(key, 0), key) > cursor:
            raise MediaError(f"{key} exceeds reel length")
    if plan.get("fade_in_frames", 0) + plan.get("fade_out_frames", 0) > cursor:
        raise MediaError("Opening and closing fades overlap")
    coverage(timeline, plan.get("balance", {}))
    if plan.get("audio") is not None:
        audio = plan["audio"]
        if audio.get("authorized") is not True:
            raise MediaError("Explicit audio reuse permission is required (authorized: true)")
        metadata = probe(source_path(audio))
        if not metadata["has_audio"] or metadata["audio_duration"] + 0.001 < cursor / fps:
            raise MediaError("Authorized soundtrack is missing or shorter than the reel")
    for caption in plan.get("captions", []):
        if not isinstance(caption, dict):
            raise MediaError("Each caption must be an object")
        start = integer(caption.get("start_frame"), "caption start_frame")
        end = integer(caption.get("end_frame"), "caption end_frame", 1)
        fade = integer(caption.get("fade_frames", 0), "caption fade_frames")
        if end > cursor or end <= start or 2 * fade > end - start:
            raise MediaError("Caption range/fades are outside the timeline or overlap")
        if caption.get("orientation", "upright") not in {"upright", "sideways"}:
            raise MediaError("Invalid caption orientation")
        if not isinstance(caption.get("lines"), list) or not caption["lines"]:
            raise MediaError("Caption lines must be a nonempty list")
        number(caption.get("y", height * 0.4), "caption y")
        for line in caption["lines"]:
            if not isinstance(line, dict):
                raise MediaError("Each caption line must be an object")
            if not isinstance(line.get("text"), str) or not line["text"]:
                raise MediaError("Caption text must be nonempty")
            integer(line.get("size"), "caption font size", 1)
            ImageColor.getrgb(line.get("color", "white"))
    return timeline


def font_path(plan: dict[str, Any]) -> str:
    font = plan.get("font")
    if font is not None:
        if not isinstance(font, str) or not Path(font).is_absolute() or not Path(font).is_file():
            raise MediaError("font must be an existing absolute font path")
        return font
    found = next((p for p in FONT_CANDIDATES if Path(p).is_file()), None)
    if found is None:
        raise MediaError("No font found; set an absolute font path in the plan")
    return found


def caption_image(caption: dict[str, Any], plan: dict[str, Any], path: Path) -> None:
    w, h = plan.get("width", 1080), plan.get("height", 1920)
    sideways = caption.get("orientation") == "sideways"
    if sideways:
        w, h = h, w
    image = Image.new("RGBA", (w, h))
    draw = ImageDraw.Draw(image)
    y = float(caption.get("y", h * 0.4))
    for line in caption["lines"]:
        font = ImageFont.truetype(font_path(plan), line["size"])
        box = draw.textbbox((0, 0), line["text"], font=font, stroke_width=1)
        tw, th = box[2] - box[0], box[3] - box[1]
        if tw > w * 0.9 or y + th + 4 > h * 0.95:
            raise MediaError("Caption exceeds safe canvas; shorten text or reduce font size/y")
        x = (w - tw) / 2 - box[0]
        draw.text((x, y - box[1]), line["text"], font=font, fill=line.get("color", "white"),
                  stroke_width=1, stroke_fill=(0, 0, 0, 150))
        y += th + max(8, line["size"] * 0.15)
    if sideways:
        image = image.transpose(Image.Transpose.ROTATE_270)
    image.save(path)


def render_segment(shot: dict[str, Any], plan: dict[str, Any], work: Path) -> Path:
    w, h, fps = plan.get("width", 1080), plan.get("height", 1920), plan.get("fps", 30)
    if shot["orientation"] == "sideways":
        w, h = h, w
    cx, cy = shot["crop_center"]
    if shot["kind"] == "photo":
        crop = ImageOps.fit(
            open_photo(Path(shot["source"])), (w * 2, h * 2),
            Image.Resampling.LANCZOS, centering=(cx, cy),
        )
        image = work / f"photo-{shot['shot']}.png"
        crop.save(image)
        inputs = ["-loop", "1", "-framerate", str(fps), "-i", str(image)]
        filters = [
            f"zoompan=z='1+0.035*on/{max(shot['frames'] - 1, 1)}'"
            f":x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={shot['frames']}:s={w}x{h}:fps={fps}",
        ]
    else:
        inputs = ["-ss", str(shot["source_start"]), "-i", shot["source"]]
        filters = [
            f"setpts=(PTS-STARTPTS)/{shot['speed']}", f"fps={fps}",
            f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos",
            f"crop={w}:{h}:(iw-ow)*{cx}:(ih-oh)*{cy}",
        ]
    if shot["orientation"] == "sideways":
        filters.append("transpose=clock")
    filters += [
        "setsar=1", "scale=out_color_matrix=bt709:out_range=tv", "format=yuv420p",
        "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        f"setpts=N/({fps}*TB)",
    ]
    output = work / f"shot-{shot['shot']:04d}.mp4"
    ffmpeg([
        "-y", "-threads", "1", *inputs, "-an", "-vf", ",".join(filters),
        "-frames:v", str(shot["frames"]), "-c:v", "libx264", "-preset", "fast",
        "-crf", "18", "-threads", "2", "-pix_fmt", "yuv420p",
        "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
        "-color_trc", "bt709", "-video_track_timescale", "60000", str(output),
    ])
    metadata = probe(output)
    if metadata["frames"] != shot["frames"]:
        raise MediaError(f"Wrong segment frame count for shot {shot['shot']}")
    return output


def render(plan_path: Path, output: Path) -> None:
    plan = read_json(plan_path)
    timeline = validate_plan(plan)
    inputs = [Path(s["source"]) for s in timeline]
    if plan.get("audio"):
        inputs.append(source_path(plan["audio"]))
    output = new_output(output, inputs)
    write_json(output / "plan.json", plan)
    write_json(output / "timeline.json", {"shots": timeline})
    fps = plan.get("fps", 30)
    frames = timeline[-1]["end_frame"]
    duration = frames / fps
    with tempfile.TemporaryDirectory(prefix="render-", dir=output) as directory:
        work = Path(directory)
        segments = [render_segment(s, plan, work) for s in timeline]
        concat = work / "segments.txt"
        concat.write_text("".join(f"file '{p.name}'\n" for p in segments), encoding="utf-8")
        montage = work / "montage.mp4"
        ffmpeg(["-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(montage)])
        inputs_args = ["-i", str(montage)]
        filters = []
        current = "[0:v]"
        for index, caption in enumerate(plan.get("captions", []), 1):
            image = work / f"caption-{index}.png"
            caption_image(caption, plan, image)
            inputs_args += ["-loop", "1", "-framerate", str(fps), "-i", str(image)]
            start, end = caption["start_frame"] / fps, caption["end_frame"] / fps
            fade = caption.get("fade_frames", 0) / fps
            effect = f"[{index}:v]format=rgba"
            if fade:
                effect += f",fade=t=in:st={start}:d={fade}:alpha=1"
                effect += f",fade=t=out:st={end - fade}:d={fade}:alpha=1"
            filters.append(effect + f"[caption{index}]")
            filters.append(
                f"{current}[caption{index}]overlay=0:0:enable='gte(t,{start})*lt(t,{end})'[text{index}]"
            )
            current = f"[text{index}]"
        fades = []
        fi, fo = plan.get("fade_in_frames", 0), plan.get("fade_out_frames", 0)
        if fi:
            fades.append(f"fade=t=in:st=0:d={fi / fps}")
        if fo:
            fades.append(f"fade=t=out:st={(frames - fo) / fps}:d={fo / fps}")
        filters.append(current + ",".join(fades + ["format=yuv420p"]) + "[v]")
        audio_args = ["-an"]
        if plan.get("audio"):
            audio_index = 1 + len(plan.get("captions", []))
            inputs_args += ["-i", str(source_path(plan["audio"]))]
            af = f"[{audio_index}:a:0]atrim=duration={duration},asetpts=N/SR/TB"
            if fo:
                af += f",afade=t=out:st={(frames - fo) / fps}:d={fo / fps}"
            filters.append(af + "[a]")
            audio_args = ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "44100"]
        ffmpeg([
            "-y", *inputs_args, "-filter_complex_threads", "2", "-filter_complex",
            ";".join(filters), "-map", "[v]", *audio_args,
            "-frames:v", str(frames), "-t", str(duration), "-r", str(fps),
            "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-threads", "2", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(output / "reel.mp4"),
        ])
    cells = [
        (f"#{s['shot']} {s['subject']} {s['start_frame'] / fps:.2f}s",
         video_frame(output / "reel.mp4", (s["start_frame"] + s["frames"] / 2) / fps))
        for s in timeline
    ]
    sheets(cells, output, "preview")
    validate_job(output)


def sync_check(reference_audio: array.array, actual: array.array, duration: float,
               closing_fade: float) -> list[dict[str, Any]]:
    usable = min(duration - closing_fade - 0.1, len(reference_audio) / 8000 - 0.1)
    window = min(2.0, (usable - 0.1) / 2)
    if window < 0.15:
        raise MediaError("Soundtrack is too short for a meaningful sync check")
    reports = []
    for fraction in [0.0, 0.33, 0.66, 1.0]:
        start = 0.1 + (usable - window - 0.1) * fraction
        lo, hi = round(start * 8000), round((start + window) * 8000)
        target = reference_audio[lo:hi:8]
        mean = sum(target) / len(target)
        x = [v - mean for v in target]
        xx = sum(v * v for v in x)
        if xx < 1e-10:
            continue
        best, best_lag = -1.0, 0
        for lag in range(-400, 401, 8):
            raw = actual[lo + lag:hi + lag:8]
            if len(raw) != len(x):
                continue
            mean_y = sum(raw) / len(raw)
            y = [v - mean_y for v in raw]
            yy = sum(v * v for v in y)
            correlation = sum(a * b for a, b in zip(x, y)) / math.sqrt(xx * yy) if yy else -1
            if correlation > best:
                best, best_lag = correlation, lag
        if best < 0.95 or abs(best_lag) > 80:
            raise MediaError(f"Soundtrack sync failed near {start:.2f}s: correlation={best:.4f}, lag={best_lag / 8}ms")
        reports.append({"start_seconds": round(start, 3), "correlation": round(best, 5),
                        "lag_ms": best_lag / 8})
    if not reports:
        raise MediaError("Soundtrack is silent; waveform alignment cannot be verified")
    return reports


def validate_job(job: Path) -> dict[str, Any]:
    job = job.resolve()
    report_path = job / "validation.json"
    try:
        plan = read_json(job / "plan.json")
        timeline = validate_plan(plan)
        if read_json(job / "timeline.json")["shots"] != timeline:
            raise MediaError("Saved timeline differs from the current plan")
        video = job / "reel.mp4"
        metadata = probe(video)
        fps = plan.get("fps", 30)
        frames = timeline[-1]["end_frame"]
        if (metadata.get("width"), metadata.get("height"), metadata.get("frames")) != (
            plan.get("width", 1080), plan.get("height", 1920), frames
        ) or abs(metadata["fps"] - fps) > 0.00001 or metadata["pix_fmt"] != "yuv420p":
            raise MediaError("Output dimensions, fps, pixel format or frame count mismatch")
        timestamps = json.loads(run([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
            "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(video),
        ]))["frames"]
        errors = [
            abs(float(f["best_effort_timestamp_time"]) - i / fps)
            for i, f in enumerate(timestamps)
        ]
        if len(errors) != frames or max(errors) > 0.001:
            raise MediaError("Nonuniform or missing output frame timestamps")
        decoded = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", str(video), "-f", "null", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if decoded.returncode or decoded.stderr.strip():
            raise MediaError(f"Full decode failed: {decoded.stderr.decode(errors='replace')[-4000:]}")
        checks: list[dict[str, Any]] = []
        if plan.get("audio"):
            if (not metadata["has_audio"]
                    or abs(metadata["audio_duration"] - frames / fps) > 1 / fps
                    or abs(metadata["audio_start"]) > 0.001):
                raise MediaError("Soundtrack missing, truncated or starts out of sync")
            checks = sync_check(
                pcm(source_path(plan["audio"])), pcm(video), frames / fps,
                plan.get("fade_out_frames", 0) / fps,
            )
        elif metadata["has_audio"]:
            raise MediaError("Silent draft unexpectedly contains audio")
        report = {
            "status": "passed", "frames": frames, "fps": fps,
            "duration_seconds": frames / fps, "max_frame_timestamp_error_ms": max(errors) * 1000,
            "coverage": coverage(timeline, plan.get("balance", {})),
            "soundtrack_checks": checks, "full_decode": "passed",
            "visual_review": "required: inspect preview sheets, crop extremes and motion before delivery",
        }
        write_json(report_path, report)
        print(json.dumps(report, indent=2))
        return report
    except (MediaError, OSError, ValueError, KeyError, TypeError) as exc:
        write_json(report_path, {"status": "failed", "error": str(exc)})
        raise


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    for name in ["scan", "reference"]:
        command = commands.add_parser(name)
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        if name == "scan":
            command.add_argument("--samples", type=int, default=6)
    render_parser = commands.add_parser("render")
    render_parser.add_argument("--plan", type=Path, required=True)
    render_parser.add_argument("--output", type=Path, required=True)
    check_parser = commands.add_parser("validate")
    check_parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        missing = [tool for tool in ["ffmpeg", "ffprobe"] if not shutil.which(tool)]
        if missing:
            raise MediaError(f"Missing required tools: {', '.join(missing)}")
        if args.command == "doctor":
            print(json.dumps({
                "python": sys.version.split()[0], "ffmpeg": shutil.which("ffmpeg"),
                "ffprobe": shutil.which("ffprobe"),
                "fonts": [p for p in FONT_CANDIDATES if Path(p).is_file()],
                "heic_sips_fallback": shutil.which("sips"),
            }, indent=2))
        elif args.command == "scan":
            scan(args.input, args.output, args.samples)
        elif args.command == "reference":
            reference(args.input, args.output)
        elif args.command == "render":
            render(args.plan, args.output)
        else:
            validate_job(args.job)
        return 0
    except (MediaError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
