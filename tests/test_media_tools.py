import array
import copy
import hashlib
import importlib.util
import json
import math
import random
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("media_tools", ROOT / "scripts" / "media_tools.py")
assert SPEC is not None and SPEC.loader is not None
media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(media)


def shot(path, subject, frames, kind="photo", **extra):
    return {
        "kind": kind, "source": str(path), "fingerprint": media.fingerprint(path),
        "subject": subject, "frames": frames, **extra,
    }


class PlanGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reel-plan-test-")
        self.root = Path(self.temp.name)
        self.photo = self.root / "source with spaces & 'quote.jpg"
        Image.new("RGB", (240, 400), "blue").save(self.photo)
        self.plan = {
            "width": 180, "height": 320, "fps": 30,
            "shots": [shot(self.photo, "male", 30), shot(self.photo, "female", 30)],
            "balance": {
                "equal_photo_counts": ["male", "female"],
                "equal_subject_frames": ["male", "female"],
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_equal_counts_but_unequal_time_fails(self):
        self.plan["shots"][1]["frames"] = 60
        with self.assertRaisesRegex(media.MediaError, "equal_subject_frames"):
            media.validate_plan(self.plan)

    def test_missing_subject_does_not_pass_as_zero_equal_zero(self):
        self.plan["balance"]["equal_photo_counts"] = ["person-a", "person-b"]
        with self.assertRaisesRegex(media.MediaError, "Balance failed"):
            media.validate_plan(self.plan)

    def test_changed_source_requires_rescan(self):
        self.photo.write_bytes(b"replaced media")
        with self.assertRaisesRegex(media.MediaError, "Source changed"):
            media.validate_plan(self.plan)

    def test_permission_and_invalid_frames_are_rejected(self):
        invalid = copy.deepcopy(self.plan)
        invalid["shots"][0]["frames"] = 30.5
        with self.assertRaisesRegex(media.MediaError, "integer"):
            media.validate_plan(invalid)
        self.plan["audio"] = {
            "source": str(self.photo), "fingerprint": media.fingerprint(self.photo),
            "authorized": False,
        }
        with self.assertRaisesRegex(media.MediaError, "permission"):
            media.validate_plan(self.plan)

    def test_hdr_and_unsuitable_slow_motion_are_blocked(self):
        fake_video = self.root / "video.mp4"
        fake_video.write_bytes(b"metadata is mocked in this validation test")
        plan = {"shots": [shot(fake_video, "action", 30, kind="video")]}
        with patch.object(media, "probe", return_value={"fps": 60, "color_transfer": "arib-std-b67"}):
            with self.assertRaisesRegex(media.MediaError, "tone-map"):
                media.validate_plan(plan)
        plan["shots"][0]["speed"] = 0.5
        with patch.object(media, "probe", return_value={
            "fps": 30, "color_transfer": "bt709", "video_duration": 10,
        }):
            with self.assertRaisesRegex(media.MediaError, "Insufficient source fps"):
                media.validate_plan(plan)

    def test_existing_or_nested_output_is_protected(self):
        with self.assertRaisesRegex(media.MediaError, "outside"):
            media.new_output(self.root / "export", [self.root])
        existing = self.root / "existing"
        existing.mkdir()
        with self.assertRaisesRegex(media.MediaError, "already exists"):
            media.new_output(existing, [])

    def test_exif_orientation_is_applied(self):
        path = self.root / "rotated.jpg"
        image = Image.new("RGB", (40, 20), "red")
        exif = image.getexif()
        exif[274] = 6
        image.save(path, exif=exif)
        self.assertEqual(media.open_photo(path).size, (20, 40))

    def test_caption_overflow_is_explicit(self):
        caption = {
            "y": 10, "lines": [{"text": "THIS CAPTION CANNOT FIT " * 20, "size": 40}],
        }
        with self.assertRaisesRegex(media.MediaError, "safe canvas"):
            media.caption_image(caption, self.plan, self.root / "caption.png")


class WaveformChecks(unittest.TestCase):
    def test_zero_offset_passes_and_shift_is_detected(self):
        rng = random.Random(42)
        original = array.array("f", (rng.uniform(-0.4, 0.4) for _ in range(24000)))
        checks = media.sync_check(original, original, 3, 0.2)
        self.assertTrue(all(c["lag_ms"] == 0 for c in checks))
        delayed = array.array("f", [0] * 240)
        delayed.extend(original)
        with self.assertRaisesRegex(media.MediaError, "sync failed"):
            media.sync_check(original, delayed, 3, 0.2)

    def test_silence_is_not_a_success_shaped_fallback(self):
        silence = array.array("f", [0] * 24000)
        with self.assertRaisesRegex(media.MediaError, "silent"):
            media.sync_check(silence, silence, 3, 0.2)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class LocalWorkflow(unittest.TestCase):
    def test_scan_reference_render_and_validate(self):
        with tempfile.TemporaryDirectory(prefix="reel-workflow-test-") as directory:
            root = Path(directory)
            sources = root / "sources"
            sources.mkdir()
            photos = []
            for i, color in enumerate(["#3261b8", "#bd4261", "#529577", "#9762ae", "#b98439"]):
                path = sources / f"photo {i}.jpg"
                image = Image.new("RGB", (420, 640), color)
                draw = ImageDraw.Draw(image)
                draw.rectangle((100, 160, 320, 500), outline="white", width=8)
                draw.text((160, 320), f"SOURCE {i}", fill="white")
                image.save(path)
                photos.append(path)
            video = sources / "moving 'original.mp4"
            media.ffmpeg([
                "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2",
                "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", str(video),
            ])
            soundtrack = sources / "permitted music.wav"
            rng = random.Random(19)
            values = []
            for i in range(8000 * 6):
                t = i / 8000
                sample = 0.3 * math.sin(2 * math.pi * (220 * t + 21 * t * t))
                sample += 0.07 * rng.uniform(-1, 1)
                values.append(round(sample * 32767))
            with wave.open(str(soundtrack), "wb") as file:
                file.setnchannels(1)
                file.setsampwidth(2)
                file.setframerate(8000)
                file.writeframes(struct.pack("<" + "h" * len(values), *values))
            originals = {
                str(p): (media.fingerprint(p), hashlib.sha256(p.read_bytes()).hexdigest())
                for p in sources.iterdir()
            }
            scan = media.scan(sources, root / "review", 3)
            self.assertEqual(len(scan["assets"]), 6)
            self.assertFalse(scan["failures"])
            self.assertTrue(all((root / "review" / name).is_file() for name in scan["sheets"]))
            plan = {
                "width": 180, "height": 320, "fps": 30,
                "fade_in_frames": 3, "fade_out_frames": 15,
                "balance": {
                    "equal_photo_counts": ["male", "female"],
                    "equal_subject_frames": ["male", "female"],
                },
                "audio": {
                    "source": str(soundtrack), "fingerprint": media.fingerprint(soundtrack),
                    "authorized": True,
                },
                "captions": [{
                    "start_frame": 2, "end_frame": 25, "fade_frames": 3,
                    "orientation": "sideways", "y": 40,
                    "lines": [{"text": "OUR JOURNEY", "size": 22, "color": "#e6002b"}],
                }],
                "shots": [
                    shot(photos[0], "male", 15, orientation="sideways"),
                    shot(photos[1], "female", 15, orientation="sideways"),
                    shot(photos[2], "male", 30),
                    shot(photos[3], "female", 30),
                    shot(photos[4], "couple", 30),
                    shot(video, "action", 30, kind="video", source_start=0.25),
                ],
            }
            plan_path = root / "edit.json"
            media.write_json(plan_path, plan)
            job = root / "reel-v1"
            media.render(plan_path, job)
            report = media.validate_job(job)
            self.assertEqual(report["frames"], 150)
            self.assertEqual(report["coverage"]["photo_counts"], {"male": 2, "female": 2, "couple": 1})
            self.assertEqual(report["coverage"]["subject_frames"]["male"], 45)
            self.assertEqual(report["coverage"]["subject_frames"]["female"], 45)
            self.assertTrue(all(c["lag_ms"] == 0 for c in report["soundtrack_checks"]))
            self.assertTrue((job / "preview.jpg").is_file())
            self.assertFalse(list(job.glob("render-*")))
            raw = media.ffmpeg([
                "-i", str(job / "reel.mp4"), "-an", "-vf", "scale=90:160,format=rgb24",
                "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1",
            ])
            frame_bytes = 90 * 160 * 3
            self.assertEqual(len(raw), 150 * frame_bytes)
            for frame, photo in zip([7, 22, 45, 75, 105], photos):
                offset = frame * frame_bytes
                rgb = raw[offset:offset + 3]
                expected = media.open_photo(photo).getpixel((0, 0))
                self.assertLess(max(abs(a - b) for a, b in zip(rgb, expected)), 25)
            caption_frame = raw[7 * frame_bytes:8 * frame_bytes]
            red_pixels = sum(
                caption_frame[i] > 140 and caption_frame[i] > caption_frame[i + 1] * 1.5
                and caption_frame[i] > caption_frame[i + 2] * 1.5
                for i in range(0, len(caption_frame), 3)
            )
            self.assertGreater(red_pixels, 20, "Caption must appear at its scheduled time")
            self.assertLess(sum(raw[-frame_bytes:]) / frame_bytes, 20, "Closing fade must reach near-black")
            media.reference(job / "reel.mp4", root / "reference")
            ref = media.read_json(root / "reference" / "reference.json")
            self.assertTrue(ref["candidate_cuts_seconds"])
            self.assertGreater(len(ref["audio_rms_50ms"]), 80)
            self.assertEqual(originals, {
                str(p): (media.fingerprint(p), hashlib.sha256(p.read_bytes()).hexdigest())
                for p in sources.iterdir()
            })
            with self.assertRaisesRegex(media.MediaError, "already exists"):
                media.render(plan_path, job)
            silent = {
                "width": 180, "height": 320, "fps": 30,
                "shots": [shot(photos[0], "scenery", 15)],
            }
            media.write_json(plan_path, silent)
            silent_job = root / "silent-draft"
            media.render(plan_path, silent_job)
            self.assertFalse(media.probe(silent_job / "reel.mp4")["has_audio"])

    def test_corrupt_media_is_reported_not_skipped(self):
        with tempfile.TemporaryDirectory(prefix="reel-corrupt-test-") as directory:
            root = Path(directory)
            source = root / "broken.mp4"
            source.write_bytes(b"not a video")
            with self.assertRaisesRegex(media.MediaError, "assets failed"):
                media.scan(source, root / "review", 2)
            report = media.read_json(root / "review" / "inventory.json")
            self.assertIn("error", report["assets"][0])
            self.assertEqual(report["failures"], [str(source.resolve())])


class CustomizationFiles(unittest.TestCase):
    def test_agent_skills_and_local_links(self):
        agent = ROOT / ".github" / "agents" / "reel-editor.agent.md"
        self.assertIn("name: Reel Editor", agent.read_text())
        skills = sorted((ROOT / ".github" / "skills").glob("*/SKILL.md"))
        self.assertEqual(len(skills), 3)
        for skill in skills:
            text = skill.read_text()
            self.assertTrue(text.startswith("---\n"))
            self.assertIn(f"name: {skill.parent.name}\n", text)
            self.assertIn("description:", text)
        import re
        for path in [agent, *skills, ROOT / "README.md"]:
            for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
                if not target.startswith(("https://", "http://", "#")):
                    self.assertTrue((path.parent / target).resolve().exists(), (path, target))


if __name__ == "__main__":
    unittest.main()
