# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**wxnote2html** converts WeChat notes to HTML by controlling an Android phone via ADB: auto-scroll screenshot → image stitching → HTML with embedded base64 image.

## Pipeline (2 stages)

```
run.py → main.py → capture.py (ADB screenshots) → stitch_v2.py (multi-vote UI detection + constrained NCC matching)
```

## Environment

- **Python**: `C:\Users\lzf_8\.conda\envs\openclaw`
- **ADB**: `C:\Program Files\MuMu Player 12\nx_main` (MuMu emulator)
- **Git**: `C:\Program Files\Git\cmd`

## Commands

```bash
# Install dependencies
pip install -r wxnote2html/requirements.txt

# Run (connect phone first, open WeChat note scrolled to top)
python run.py -o note.html

# Debug mode (detailed logs + match debug images + tmp/debug.log)
python run.py --debug -o note.html

# Use existing screenshots instead of ADB capture
python run.py --images ./screenshots/ -o note.html

# Output PNG instead of HTML
python run.py -o stitched.png

# Save raw screenshots for debugging
python run.py --save-screenshots ./debug/ -o note.html
```

## Architecture

### `run.py`
Single-file launcher. Inserts the project root into `sys.path`, sets UTF-8 stdout, and calls `main()`.

### `wxnote2html/main.py`
CLI entry point (argparse). Orchestrates the full pipeline:
1. Acquire images — from `--images` directory or ADB capture
2. Stitch — `stitch_v2.stitch()` with constrained-search template matching + 60-row NCC verification
3. Output — HTML with inline base64 image, or PNG

Key args: `--debug`, `--confidence-threshold` (0.6), `--max-overlap-ratio` (0.85).

### `wxnote2html/capture.py`
`ADBCapture` class wraps `adb shell` commands:
- `list_devices()` — parse `adb devices`
- `get_screen_size()` — `wm size`
- `screenshot()` — `exec-out screencap -p` via stdout pipe (no temp files on device)
- `scroll_down()` — `input swipe` from 90% → (90% - distance), min 10% of screen height
- `images_similar()` — grayscale resize to 200px wide, compare mean pixel diff (threshold 0.985) to detect bottom
- `capture_scroll()` — **先截首张→估算内容高度→基于内容高度计算滚动距离→循环(滚动→截图→相似度检测)**

Returns `tuple[list[Image.Image], list[int]]` (screenshots + per-scroll distances).

`_estimate_content_height()` — 单图方差法检测 header，减去固定 footer 估算值。

`_capture_log()` — debug 模式下写入 `tmp/debug.log`。

`save_screenshots()` — 保存截图到目录。

### `wxnote2html/stitch_v2.py`
V2 stitching engine:
- **`detect_header()`** — multi-image voting to detect WeChat fixed UI height (status bar + title bar + "来自" separator lines)
- **`detect_footer()`** — bottom blank detection + multi-image bottom common region voting
- **`crop_image()`** — crop header/footer from a single image
- **`match_overlap()`** — constrained search [expected × 0.1, expected × 2.0] with `cv2.matchTemplate` + 60-row consecutive non-blank NCC (>0.75) verification
- **`_verify_ncc()`** — 60 consecutive non-blank row NCC check (blanks skip without breaking continuity, NCC failure resets counter)
- **FR-7 fallback**: sliding window of last 3 successful overlaps (median) → global search (10%-90% of top image, first pair only) → geometric expected value
- **FR-8 clamping**: overlap ≥ bottom_h - 10 → clamp to bottom_h - 30; overlap < 0 → clamp to 0; overlap > bottom_h × 0.85 → warn only
- **`blend_seam()`** — 5px linear gradient blend at stitch seams
- **`stitch()`** — streaming entry point, two-phase memory (sample detection → stream stitch), peak memory ~18MB. Accepts `scroll_distances: list[int] | None` for per-pair expected overlap calculation
- **`_write_log()`** — debug 模式下追加写入 `tmp/debug.log`

### `wxnote2html/stitch.py`
V1 stitching engine (kept for reference, no longer used).

## Dependencies

| Package | Used In | Purpose |
|---------|---------|---------|
| `opencv-python-headless` | stitch_v2.py | Template matching for overlap detection |
| `Pillow` | capture, stitch_v2 | Image I/O, resize, crop, encode |
| `numpy` | capture, stitch_v2 | Array operations, std/NCC calculation |

## Key Design Details

- **No temp files on device**: `ADBCapture.screenshot()` pipes PNG data through stdout via `adb exec-out screencap -p`, avoiding filesystem writes on the phone.
- **Adaptive scroll distance**: `capture_scroll()` takes the first screenshot, estimates content height via variance-based header detection, then calculates scroll distance from content height (not full screen height). This addresses the fact that `input swipe` distance ≠ actual WeChat content scroll distance.
- **Per-pair scroll distances**: `capture_scroll()` returns a list of scroll distances (one per scroll). `stitch()` uses each pair's scroll distance independently to calculate the expected overlap, so variable scroll amounts don't cause content loss.
- **Bottom detection**: compares consecutive screenshots after resizing to 200px wide grayscale. If mean pixel difference < 1.5%, treats as reached bottom and discards the duplicate.
- **WeChat header structure**: status bar → "微信 ···" title bar → thin line → "来自" → thin line → content area. `detect_header()` detects the boundary below the second thin line.
- **Constrained search**: template matching is restricted to [expected × 0.1, expected × 2.0] window around the expected overlap, preventing distant false matches.
- **Adaptive expected update**: After each successful match (conf ≥ 0.8), the actual overlap calibrates the expected value for the next pair. After a failed match, falls back to the per-pair scroll distance.
- **Module imports**: `main.py` uses a `_import_module()` helper that tries relative imports first (`from . import X`) then falls back to absolute (`import X`), so the package works both as `python -m wxnote2html.main` and via `run.py`.
- **OpenCV optional**: stitching works without OpenCV but with lower quality (geometric estimation, no template matching).
- **Chinese encoding**: All entry point files call `sys.stdout.reconfigure(encoding="utf-8")` for correct Chinese output in Windows bash.
- **Debug files**: `capture_scroll()` always saves screenshots to `tmp/screen_*.png`. `stitch()` always saves cropped images to `tmp/cropped_*.png`. `--debug` additionally saves match debug images to `tmp/match_*_search.png` and `tmp/match_*_template.png`, and writes `tmp/debug.log`.

## Prerequisites (not in code, needed to run)

- Android phone with USB debugging enabled, connected via USB
- ADB installed and on PATH (`adb devices` shows device)
- Python 3.10+
- WeChat note open and scrolled to the very top on the phone
- Phone screen stays on during capture (don't lock)
