# Markers to Render Queue

> Part of [PostFlows](https://github.com/postflows) toolkit for DaVinci Resolve

Batch add clips with timeline markers to the render queue. Filter by marker color and type (Single or Duration). Custom naming with components, naming presets, optional folder-per-render. **Python** script.

## What it does

GUI for adding clips at marker positions to the render queue. Filter by marker color or process all; choose Single markers (render entire clip at marker) or Duration markers (render only the marker’s frame range, with validation). Custom naming: components (ProjectName, TimelineName, MarkerName, etc.), shotID (auto/reel/source), task, version; save/load naming presets. Timeline and video track selection; markers table with double-click to jump to timecode. Export path history; optional subfolder per render (for EXR sequences). Use render preset filename or custom naming.

## Requirements

- DaVinci Resolve Studio
- Open project and timeline with markers
- Defined render preset

## Installation

Copy the **`markers-to-render-queue.py`** file to:

- **macOS:** `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/`
- **Windows:** `C:\ProgramData\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\`

Run from **Workspace → Scripts** in Resolve (or from the Fusion page Scripts menu).

## Usage

Run script. Select timeline, marker color and type (Single or Duration), render preset. Configure naming (or enable “Use filename from current render preset”). Choose export path; optionally “Create separate folder for each render”. Click **Add to Render Queue**. Double-click a row in the markers table to jump the playhead to that timecode.

## License

MIT © PostFlows
