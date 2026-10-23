# ArtCrossPost

A free, open-source Windows desktop client for artists who publish the same artwork
on multiple platforms — write it once, post it everywhere.

[中文说明](README.zh-CN.md)

## Why ArtCrossPost?

Re-uploading the same image and re-typing the same title, tags, and description on
every platform is slow and error-prone. ArtCrossPost keeps one local workspace for
the artwork, its metadata, and its platform-specific variants, then posts to each
selected platform through a local Playwright-driven browser session — no paid
platform APIs, no monthly API fees, and your logins never leave your machine.

## Features

- **One post, multiple platforms** — built-in dispatchers for **DeviantArt**,
  **X (Twitter)**, and **Patreon**. (A Ko-fi dispatcher is included but disabled
  by default — set `platform_kofi_enabled` to re-enable it.)
- **DeviantArt multi-image deviations** — publish a single deviation with multiple
  images (10 by default, configurable): title, description, tags, gallery, and
  Mature/AI flags are filled in once, and the published image count is verified
  against the expected count after submission.
- **Drag-to-reorder thumbnails** — reorder the image set by dragging, with a live
  insertion indicator; the new order propagates to the X image selection and to
  every platform's send order.
- **Smart GIF optimization** — oversized GIFs are shrunk resolution-first
  (each step re-sampled from the original, down to a 640px / 50% floor), then by
  palette reduction, and only as a last resort by frame decimation.
- **X teaser posts** — X's 280 weighted-character limit is handled with official
  Twitter-Text weighting: the post keeps a sentence-boundary teaser of the body,
  an 🔞 NSFW notice with your DeviantArt link, and your tags, while DeviantArt
  and Patreon receive the full text. A **live preview** shows the exact final X
  text and weighted count as you type.
- **Scheduled publishing** — snapshot a post (images, order, text, platforms) and
  let the built-in timer publish it at the chosen time; tasks that come due while
  the app is closed are surfaced on next launch — never silently dropped.
- **Retry failed platforms only** — if one platform fails, retry just that one
  from the results panel, reusing the rendered images and text snapshot;
  platforms that already succeeded are never posted twice.
- **Publish history & duplicate warning** — every publish is recorded locally
  (with perceptual image fingerprints); importing a set you already posted
  raises a warning banner instead of silently double-posting.
- **Folder batch import** — import a whole folder and let the auto-grouper split
  it into separate posts by filename pattern (shared prefix + sequence, `Vol. N`,
  chapters), with a review dialog to merge, split, or rename groups.
- **Data export / import** — back up or move your whole setup (config, drafts,
  browser sessions, schedules, history) as a single zip; imports automatically
  back up your current data first.
- **Local AI censoring** — ONNX NudeNet detection generates platform-compliant
  variants automatically (black bar, mosaic, or blur, with adjustable coverage),
  while the original can still go to platforms that allow it.
- **Watermarking** — configurable text watermark (font, size, opacity, position)
  applied only to the copies that need it.
- **Live preview** — dark three-pane UI with a responsive canvas and interactive
  censor box adjustments, so work can be checked before it goes out.
- **Per-platform controls** — sensitive-content flags, visibility/tiers, image-size
  limits with automatic compression only when a platform limit is exceeded,
  publish guards, and success assertions instead of "fire and forget" posting.
- **Presets & signature** — reusable default tags, per-platform preferences, and a
  support-me signature template appended on demand.
- **Local-first & private** — sessions, presets, and window state live in a local
  `data/` folder (git-ignored); nothing is uploaded anywhere except the posts you
  explicitly publish.

## Requirements

- Windows 10/11
- Python 3.10+
- Microsoft Edge (used by the Playwright automation)

## Quick start

Double-click `start.bat`, or run:

```bash
pip install -r requirements.txt
python app.py
```

On first use, sign in to each platform inside the app's browser window; the
session is stored locally for later runs.

## Project structure

```
├── app.py                # PyQt5 desktop application & unified preview canvas
├── config.py             # Configuration & presets manager
├── censor_engine.py      # Local AI censoring (ONNX NudeNet)
├── watermark_engine.py   # Watermarking engine
├── diagnostics.py        # System health & pre-flight diagnostics
├── x_text_utils.py       # X post text handling (weighted count & teaser format)
├── schedule_manager.py   # Scheduled publishing (snapshots & timer queue)
├── schedule_dialogs.py   # Schedule editor & task list dialogs
├── history_manager.py    # Publish history & duplicate detection
├── history_dialog.py     # Publish history viewer
├── data_transfer.py      # Data export / import (zip backup & restore)
├── auto_grouper.py       # Folder batch import filename auto-grouping
├── auto_group_dialog.py  # Auto-group review dialog
├── logger.py             # Unified logging system
├── dispatchers/
│   ├── base.py           # Base dispatcher & Playwright session manager
│   ├── deviantart.py     # DeviantArt (incl. multi-image deviations)
│   ├── x_twitter.py      # X (Twitter) dispatcher
│   └── monetization.py   # Patreon & Ko-fi dispatchers
└── data/                 # Local sessions & presets (git-ignored)
```

## License

MIT — see [LICENSE](LICENSE).
