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
  **X (Twitter)**, **Patreon**, and **Ko-fi** (via the monetization dispatcher).
- **DeviantArt multi-image deviations** — publish a single deviation with multiple
  images (10 by default, configurable): title, description, tags, gallery, and
  Mature/AI flags are filled in once, and the published image count is verified
  against the expected count after submission.
- **Local AI censoring** — ONNX NudeNet detection generates platform-compliant
  variants automatically (black bar, mosaic, or blur, with adjustable coverage),
  while the original can still go to platforms that allow it.
- **Watermarking** — configurable text watermark (font, size, opacity, position)
  applied only to the copies that need it.
- **Live preview & GIF optimization** — dark three-pane UI with a responsive
  canvas, interactive censor box adjustments, and GIF optimization, so animated
  work can be checked and compressed before it goes out.
- **Per-platform controls** — sensitive-content flags, visibility/tiers, image-size
  limits with automatic compression only when a platform limit is exceeded,
  publish guards, and success assertions instead of "fire and forget" posting.
- **Presets & signature** — reusable default tags, per-platform preferences, and a
  support-me signature template (e.g. Patreon / Ko-fi) appended on demand.
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
├── x_text_utils.py       # X post text handling & truncation
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
