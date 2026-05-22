# ================================================
# Markers to Render Queue
# Part of PostFlows toolkit for DaVinci Resolve
# https://github.com/postflows
# ================================================

"""
Markers to Render Queue — batch add clips with timeline markers to the render queue.

Description:
GUI for adding clips at marker positions to the render queue. Filter by marker color and type
(Single or Duration). Custom naming with components (Project/Timeline/Marker name, shotID, task, version),
naming presets, optional folder-per-render. Supports render preset naming or custom filenames.

Key features:
- Filter markers by color or process all
- Single markers: render entire clip at marker
- Duration markers: render only the marker's frame range (with validation)
- Single → Next Marker: render range from marker to the next marker (any color/type), last marker to timeline end
- Single → Next Same Color: render range from marker to the next marker with the same selected color, last marker to timeline end
- Naming: components (ProjectName, TimelineName, MarkerName, etc.), shotID (auto/reel/source), task, version
- Save/load naming presets
- Timeline and video track selection; markers table with double-click to jump to timecode
- Export path history; optional subfolder per render (for EXR sequences)

Requirements: DaVinci Resolve Studio; open project and timeline; defined render preset.

Usage: Select timeline, marker color and type, render preset. Configure naming (or use preset filename).
Choose export path. Click Add to Render Queue.

Author: Sergey Knyazkov
"""

import sys
import os
import json
import time
import re
import threading


################################################################################################
# GLOBAL VARIABLES AND INITIALIZATION
################################################################################################

# Initialize UI manager and dispatcher
ui = fu.UIManager
disp = bmd.UIDispatcher(ui)

DEBUG_MODE = False
_cancel_render = False
# List of available marker colors
color_lst = [
    'All', 'Blue', 'Cyan', 'Green', 'Yellow', 'Red', 'Pink', 'Purple',
    'Fuchsia', 'Rose', 'Lavender', 'Sky', 'Mint', 'Lemon', 'Sand', 'Cocoa', 'Cream'
]

class SMPTE(object):
    '''Frames to SMPTE timecode converter and reverse.'''

    def __init__(self):
        self.fps = 24
        self.df = False

    def getframes(self, tc):
        '''Converts SMPTE timecode to frame count.'''
        if int(tc[9:]) > self.fps:
            raise ValueError('SMPTE timecode to frame rate mismatch.', tc, self.fps)

        hours = int(tc[:2])
        minutes = int(tc[3:5])
        seconds = int(tc[6:8])
        frames = int(tc[9:])

        totalMinutes = int(60 * hours + minutes)

        if self.df:  # Drop frame calculation
            dropFrames = int(round(self.fps * 0.066666))
            timeBase = int(round(self.fps))
            frm = int(((hourFrames * hours) + (minuteFrames * minutes) + (timeBase * seconds) + frames) - (dropFrames * (totalMinutes - (totalMinutes // 10))))

        else:  # Non-drop frame
            self.fps = int(round(self.fps))
            frm = int((totalMinutes * 60 + seconds) * self.fps + frames)

        return frm

    def gettc(self, frames):
        '''Converts frame count to SMPTE timecode.'''
        frames = abs(frames)

        if self.df:  # Drop frame calculation
            spacer, spacer2 = ':', ';'
            dropFrames = int(round(self.fps * .066666))
            framesPerHour = int(round(self.fps * 3600))
            framesPer10Minutes = int(round(self.fps * 600))
            framesPerMinute = int(round(self.fps) * 60 - dropFrames)

            frames = frames % (framesPerHour * 24)
            d = frames // framesPer10Minutes
            m = frames % framesPer10Minutes

            if m > dropFrames:
                frames += (dropFrames * 9 * d) + dropFrames * ((m - dropFrames) // framesPerMinute)
            else:
                frames += dropFrames * 9 * d

            frRound = int(round(self.fps))
            hr = frames // frRound // 3600
            mn = (frames // frRound // 60) % 60
            sc = (frames // frRound) % 60
            fr = frames % frRound
        else:  # Non-drop frame
            spacer = spacer2 = ':'
            self.fps = int(round(self.fps))
            frHour = self.fps * 3600
            frMin = self.fps * 60

            hr = frames // frHour
            mn = (frames - hr * frHour) // frMin
            sc = (frames - hr * frHour - mn * frMin) // self.fps
            fr = int(round(frames - hr * frHour - mn * frMin - sc * self.fps))

        return f"{hr:02d}{spacer}{mn:02d}{spacer}{sc:02d}{spacer2}{fr:02d}"

# Initialize Resolve project and timeline
projectManager = resolve.GetProjectManager()
project = projectManager.GetCurrentProject()
timeline = project.GetCurrentTimeline(1)

# After timeline is available
smpte = SMPTE()
smpte.fps = float(timeline.GetSetting('timelineFrameRate')) if timeline else 24.0

# Settings directory: subfolder next to this script (user can find, edit, delete)
try:
    _script_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _script_dir = os.path.expanduser('~')
SETTINGS_DIR = os.path.join(_script_dir, 'RMT Markers_to_render_queue_settings')
PRESETS_FILE = os.path.join(SETTINGS_DIR, 'naming_presets.json')
SETTINGS_FILE = os.path.join(SETTINGS_DIR, 'render_paths.json')


def _ensure_settings_dir():
    """Create settings directory if it does not exist."""
    if not os.path.isdir(SETTINGS_DIR):
        try:
            os.makedirs(SETTINGS_DIR, exist_ok=True)
        except Exception as e:
            debug_print(f"Error creating settings dir: {e}")


def load_render_paths():
    """
    Loads the saved render paths from the settings file.
    Returns a list of paths, with home directory as default if no paths are saved.
    """
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        debug_print(f"Error loading render paths: {e}")
    return [os.path.expanduser('~')]


def save_render_paths(paths):
    """
    Saves the render paths to the settings file.
    """
    try:
        _ensure_settings_dir()
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(paths, f)
    except Exception as e:
        debug_print(f"Error saving render paths: {e}")

def update_render_paths(new_path):
    """
    Updates the list of render paths, maintaining the most recent 3 paths.
    """
    paths = load_render_paths()
    if new_path in paths:
        paths.remove(new_path)
    paths.insert(0, new_path)
    paths = paths[:3]  # Keep only the last 3 paths
    save_render_paths(paths)
    return paths


# Style for the primary action button
PRIMARY_ACTION_BUTTON_STYLE = """
    QPushButton {
        border: 1px solid #2C6E49;
        max-height: 28px;
        border-radius: 14px;
        background-color: #4C956C;
        color: #FFFFFF;
        min-height: 28px;
        font-size: 13px;
    }
    QPushButton:hover {
        border: 1px solid #c0c0c0;
        background-color: #61B15A;
    }
    QPushButton:pressed {
        border: 2px solid  #c0c0c0;
        background-color:  #76C893;
    }
    QPushButton:disabled {
        border: 2px solid #8c2f39;
        background-color: rgb(50,50,50);
        color: rgb(150, 150, 150);
    }
"""
STOP_BUTTON_STYLE = """
    QPushButton {
        border: 1px solid #8c2f39;
        max-height: 28px;
        border-radius: 14px;
        background-color: #8c2f39;
        color: #FFFFFF;
        min-height: 28px;
        font-size: 13px;
    }
    QPushButton:hover {
        background-color: #b03a46;
    }
    QPushButton:pressed {
        background-color: #c0505e;
    }
    QPushButton:disabled {
        border: 1px solid #555;
        background-color: rgb(50,50,50);
        color: rgb(100, 100, 100);
    }
"""
DIVIDER_CSS = """
    background-color: #555;
    border: none;
    height: 1px;
    max-height: 1px;
    margin: 3px 0;
"""

# Styles for export path labels
WARNING_PATH_STYLE = "color: rgb(255, 0, 0); font-weight: bold;"
NORMAL_PATH_STYLE = "color: rgb(176, 176, 176);"

################################################################################################
# UI FUNCTIONS
################################################################################################

def debug_print(*args, **kwargs):
    """
    Wrapper function for debug printing. Prints the provided arguments if DEBUG_MODE is enabled.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.
    """
    if DEBUG_MODE:
        print(*args, **kwargs)

def update_status(message):
    """
    Updates the status line in the UI.
    """
    itm["status_label"].Text = message
    itm["status_label"].Update()

def main_ui():
    """
    Creates and returns the main UI layout for the script.

    Returns:
        UI Group: The main UI layout containing all controls and settings.
    """
    return ui.VGroup({"Spacing": 10}, [
        # Marker and Timeline Selection
        ui.VGroup({"Spacing": 5}, [
            ui.HGroup({"Spacing": 5}, [
                ui.Label({"Text": "Timeline:", "Weight": 0}),
                ui.ComboBox({"ID": "tl_preset", "Weight": 1}),
                ui.Label({"Text": "Marker Color:", "Weight": 0}),
                ui.ComboBox({"ID": "marker_color", "Weight": 2})
            ]),
            ui.HGroup({"Spacing": 5}, [
                ui.Label({"Text": "Video Track:", "Weight": 0}),
                ui.ComboBox({"ID": "video_track", "Weight": 3})
            ]),
            ui.HGroup({"Spacing": 5}, [
                ui.Label({"Text": "Marker Type:", "Weight": 0}),
                ui.ComboBox({"ID": "marker_type", "Items": ["Single", "Duration", "Single → Next Marker", "Single → Next Same Color"], "CurrentText": "Single"})
            ]),
            ui.HGroup({"Spacing": 5}, [
                ui.CheckBox({
                    "ID": "render_all_tracks",
                    "Text":  "🎬 Render all video tracks at marker position",
                    "StyleSheet": "font-size: 13px;",
                    "Checked": False,
                    "ToolTip": "When enabled, renders clips from ALL video tracks at marker position, not just the selected track"
                })
            ])

        ]),

        # Markers Table
        ui.Tree({
            "ID": "markers_table",
            "HeaderText": "Timecode|Color|Name|Source Name|Clip Name|Note|Reel Name",
            "ColumnCount": 7,
            "ColumnWidth": "180,160,120,200,200,150,100",
            "SelectionMode": "MultiSelection",
            "Weight": 15,
            "AlternatingRowColors": True,
            "InitialSortColumn": 0,
            "InitialSortOrder": "AscendingOrder",
            "SortingEnabled": True,
            "Events": {"ItemDoubleClicked": True}
        }),

        # Render Settings
        ui.VGroup({"Spacing": 5}, [
            ui.HGroup({"Spacing": 5}, [
                ui.Label({"Text": "Render Preset:", "Weight": 1}),
                ui.ComboBox({"ID": "render_preset", "Weight": 3})
            ]),
            ui.HGroup({"Spacing": 5}, [
                ui.CheckBox({
                    "ID": "use_preset_naming",
                    "Text":  "📋 Use filename from current render preset",
                    "StyleSheet": "font-size: 14px;",
                    "Checked": False
                }),
            ])
        ]),

        # Naming Settings
        ui.Label({
            # "StyleSheet": DIVIDER_CSS,
            "Weight": 0,
            "FrameStyle": 4,
            "Margin": -5
        }),
        ui.VGroup({"Spacing": 10, "StyleSheet": "font-weight: bold;", }, [
            ui.Label({"ID": "Custom Naming Settings", "Text": "Custom Naming Settings", "StyleSheet": "font-size: 14px;"}),

            # Shot Fields
            ui.VGroup({"Spacing": 5}, [
                ui.Label({"Text": "Naming Components", "StyleSheet": "font-weight: bold;"}),

                # Component1
                ui.HGroup({"Spacing": 5}, [
                    ui.CheckBox({"ID": "component1_enabled", "Text": "Component 1", "Checked": True}),
                    ui.ComboBox({
                        "ID": "component1_source",
                        "Items": ["ProjectName", "TimelineName", "MarkerName", "MarkerNote", "Reel Name", "SourceName", "ClipName", "Custom"]
                    }),
                    ui.LineEdit({
                        "ID": "component1_custom",
                        "PlaceholderText": "Custom value",
                        "Enabled": False
                    })
                ]),

                # Component2
                ui.HGroup({"Spacing": 5}, [
                    ui.CheckBox({"ID": "component2_enabled", "Text": "Component 2", "Checked": True}),
                    ui.ComboBox({
                        "ID": "component2_source",
                        "Items": ["ProjectName", "TimelineName", "MarkerName", "MarkerNote", "Reel Name", "SourceName", "ClipName", "Custom"]
                    }),
                    ui.LineEdit({
                        "ID": "component2_custom",
                        "PlaceholderText": "Custom value",
                        "Enabled": False
                    })
                ]),

                # Component3
                ui.HGroup({"Spacing": 5}, [
                    ui.CheckBox({"ID": "component3_enabled", "Text": "Component 3"}),
                    ui.ComboBox({
                        "ID": "component3_source",
                        "Items": ["ProjectName", "TimelineName", "MarkerName", "MarkerNote", "Reel Name", "SourceName", "ClipName", "Custom"]
                    }),
                    ui.LineEdit({
                        "ID": "component3_custom",
                        "PlaceholderText": "Custom value",
                        "Enabled": False
                    })
                ]),

                # ShotID
                ui.HGroup({"Spacing": 5}, [
                    ui.CheckBox({"ID": "shotID_enabled", "Text": "shotID", "Checked": True}),
                    ui.ComboBox({
                        "ID": "shotID_source",
                        "Items": ["Auto Number", "Reel Name", "SourceName", "ClipName", "MarkerName", "MarkerNote"],
                        "Weight": 2
                    }),
                    ui.Label({"Text": "Start:", "Weight": 0}),
                    ui.SpinBox({"ID": "shotID_start", "Value": 10, "Minimum": 1, "MaximumWidth": 70}),
                    ui.Label({"Text": "Step:", "Weight": 0}),
                    ui.SpinBox({"ID": "shotID_step", "Value": 10, "Minimum": 1, "MaximumWidth": 70}),
                    ui.Label({"Text": "Padding:", "Weight": 0}),
                    ui.SpinBox({"ID": "shotID_padding", "Value": 4, "Minimum": 1, "MaximumWidth": 70})
                ])
            ]),

            # Version Fields
            ui.VGroup({"Spacing": 5}, [
                ui.Label({"Text": "Version Fields", "StyleSheet": "font-weight: bold;"}),

                # Task
                ui.HGroup({"Spacing": 5}, [
                    ui.CheckBox({"ID": "task_enabled", "Text": "task", "Checked": False}),
                    ui.ComboBox({
                        "ID": "task_source",
                        "Items": ["comp", "anim", "roto", "match", "paint", "Custom"]
                    }),
                    ui.LineEdit({
                        "ID": "task_custom",
                        "PlaceholderText": "Custom value",
                        "Enabled": False
                    })
                ]),

                # Version
                ui.HGroup({"Spacing": 5}, [
                    ui.CheckBox({"ID": "version_enabled", "Text": "version", "Checked": True}),
                    ui.Label({"Text": "Prefix:"}),
                    ui.LineEdit({"ID": "version_prefix", "Text": "v", "Weight": 1}),
                    ui.Label({"Text": "Start:"}),
                    ui.SpinBox({"ID": "version_start", "Value": 1, "Minimum": 1, "Weight": 1}),
                    ui.Label({"Text": "Padding:"}),
                    ui.SpinBox({"ID": "version_padding", "Value": 3, "Minimum": 1, "Weight": 1})
                ])
            ]),

        ]),
        ui.HGroup({"Spacing": 5}, [
            ui.Label({"Text": "Naming Presets:", "Weight": 0}),
            ui.ComboBox({"ID": "naming_presets", "Weight": 2}),
            ui.LineEdit({"ID": "preset_name_input", "PlaceholderText": "Enter preset name", "Weight": 2}),
            ui.Button({"ID": "save_preset", "Text": "Save", "Weight": 1}),
            ui.Button({"ID": "load_preset", "Text": "Load", "Weight": 1}),
            ui.Button({"ID": "delete_preset", "Text": "Delete", "Weight": 1})
        ]),
        ui.Label({
            # "StyleSheet": DIVIDER_CSS,
            "Weight": 0,
            "FrameStyle": 4
        }),
        ui.Label({
            "ID": "status_label",
            "Text": "Ready",
            "StyleSheet": "color: #686A6C; font-style: italic;"
        }),
        # Export Settings
        ui.VGroup({"Spacing": 10}, [
            ui.HGroup({"Spacing": 5}, [
                ui.Label({"Text": "Export to:", "StyleSheet": "font-size: 16px; font-weight: bold;",  "Weight": 1}),
                ui.HGroup({"Spacing": 5, "Weight": 5}, [
                    ui.ComboBox({
                        "ID": "export_path",
                        "StyleSheet": "color: rgb(176, 176, 176);",
                        "Weight": 3,
                        "Editable": True
                    }),
                    ui.Button({
                        "ID": "export_location",
                        "Text": "Select Path",
                        "StyleSheet": """
                            QPushButton {
                                border: 1px solid rgb(176,176,176);
                                max-height: 24px;
                                border-radius: 10px;
                                background-color: rgb(71,91,98);
                                color: rgb(255, 255, 255);
                                min-height: 24px;
                                font-size: 13px;
                            }
                            QPushButton:hover {
                                border: 1px solid rgb(176,176,176);
                                background-color: rgb(89,90,183);
                            }
                            QPushButton:pressed {
                                border: 2px solid rgb(119,121,252);
                                background-color: rgb(119,121,252);
                            }
                            QPushButton:disabled {
                                border: 2px solid #8c2f39;
                                background-color: rgb(50,50,50);
                                color: rgb(150, 150, 150);
                            }
                        """
                    })
                ])
            ]),
            ui.VGroup({"Spacing": 5}, [
                ui.CheckBox({
                    "ID": "create_folders",
                    "Text":  "📁 Create separate folder for each render (based on filename)",
                    "StyleSheet": "font-size: 13px;",
                    "Checked": True,
                    "ToolTip": "Creates a subfolder for each render job with the same name as the output file. Essential for EXR sequences."
                })
            ]),


        ]),

        # Export Button
        ui.HGroup({"Spacing": 5}, [
            ui.Label({"Text": "Preview:", "StyleSheet": "font-size: 16px; font-weight: bold;", "Weight": 0}),
            ui.Label({"ID": "naming_preview", "Text": "SHOW_EP01_SH010_comp_v001", "StyleSheet": "color: #469BE6; font-size: 14px; qproperty-alignment: AlignLeft",  "Weight": 5}),

        ]),
        ui.Label({"ID": "naming_format", "Text": "Format: showID_episode_shotID_task_version"}),
        ui.HGroup({"Spacing": 5}, [
            ui.Button({
                "ID": "Export",
                "Text": "Add to Render Queue",
                "StyleSheet": PRIMARY_ACTION_BUTTON_STYLE,
                "Enabled": True,
                "Weight": 3
            }),
            ui.Button({
                "ID": "StopRender",
                "Text": "Stop Render",
                "StyleSheet": STOP_BUTTON_STYLE,
                "Enabled": False,
                "Weight": 1
            })
        ])
    ])

def sanitize_filename(s):
    """
    Replaces invalid filename characters with underscore.
    """
    return re.sub(r'[<>:"/\\|?*]', '_', s) if isinstance(s, str) else s

def get_component_value(component_settings, clip_info, example_data, default_value, counter=0):
    """
    Returns the value for a naming component; used for preview and for generating filenames.

    Args:
        component_settings (dict): Component settings
        clip_info (dict): Clip information
        example_data (dict): Data from markers table
        default_value (str): Default value
        counter (int): Counter for auto-numbering

    Returns:
        str: Component value
    """
    if not component_settings["enabled"]:
        return None

    source = component_settings["source"]

    if source == "ProjectName":
        return sanitize_filename(project.GetName() or default_value)
    elif source == "TimelineName":
        return sanitize_filename(timeline.GetName() or default_value)
    elif source == "MarkerName":
        return sanitize_filename(example_data.get('marker_name', default_value))
    elif source == "MarkerNote":
        return sanitize_filename(example_data.get('marker_note', default_value))
    elif source == "Reel Name":
        if clip_info and clip_info.get('media_pool_item'):
            reel = clip_info['media_pool_item'].GetClipProperty('Reel Name')
            return sanitize_filename(reel if reel else "REEL_ERR")
        return sanitize_filename(example_data.get('reel_name', default_value))
    elif source == "SourceName":
        if clip_info and clip_info.get('media_pool_item'):
            return sanitize_filename(clip_info['media_pool_item'].GetName() or default_value)
        return sanitize_filename(example_data.get('source_name', default_value))
    elif source == "ClipName":
        if clip_info and clip_info.get('clip'):
            clip_name = clip_info['clip'].GetName()
            return sanitize_filename(clip_name if clip_name else default_value)
        return sanitize_filename(default_value)
    elif source == "Custom":
        return sanitize_filename(component_settings["custom"] or default_value)
    elif source == "Auto Number":
        start = component_settings["start"]
        step = component_settings["step"]
        padding = component_settings["padding"]
        return f"{str(start + counter * step).zfill(padding)}"

    return sanitize_filename(default_value)

def update_naming_preview():
    """
    Updates the naming preview based on the current settings in the UI.
    """
    if not itm["use_preset_naming"].Checked:
        try:
            components = []
            current_settings = get_current_settings()

            # Get example data from the markers table
            table = itm["markers_table"]
            example_data = {}
            clip_info = None
            if table.TopLevelItemCount() > 0:
                first_item = table.TopLevelItem(0)
                example_data = {
                    'timecode': first_item.Text[0],
                    'source_name': first_item.Text[3],
                    'clip_name': first_item.Text[4],
                    'marker_name': first_item.Text[2],
                    'marker_note': first_item.Text[5],
                    'reel_name': first_item.Text[6]
                }

                # Get clip info at the marker frame
                try:
                    marker_frame = smpte.getframes(first_item.Text[0])
                    clip_info = get_clip_at_marker(timeline, marker_frame)
                except:
                    clip_info = None

            # Component 1
            comp1 = get_component_value(current_settings["component1"], clip_info, example_data, "COMP1")
            if comp1:
                components.append(comp1)

            # Component 2
            comp2 = get_component_value(current_settings["component2"], clip_info, example_data, "COMP2")
            if comp2:
                components.append(comp2)

            # Component 3
            comp3 = get_component_value(current_settings["component3"], clip_info, example_data, "COMP3")
            if comp3:
                components.append(comp3)

            # ShotID
            if current_settings["shotID"]["enabled"]:
                shot_value = get_component_value(current_settings["shotID"], clip_info, example_data, "SHOT010", 0)
                if shot_value:
                    components.append(shot_value)

            # Task
            if current_settings["task"]["enabled"]:
                task_source = itm["task_source"].CurrentText
                components.append(task_source if task_source != "Custom" else current_settings["task"]["custom"] or "TASK")

            # Version
            if current_settings["version"]["enabled"]:
                version_prefix = current_settings["version"]["prefix"]
                version_pad = current_settings["version"]["padding"]
                version_num = str(current_settings["version"]["start"]).zfill(version_pad)
                components.append(f"{version_prefix}{version_num}")

            # Build the final preview
            separator = "_"
            example = separator.join(filter(None, components))
            format_text = separator.join([c for c in [
                "component1" if current_settings["component1"]["enabled"] else None,
                "component2" if current_settings["component2"]["enabled"] else None,
                "component3" if current_settings["component3"]["enabled"] else None,
                "shotID" if current_settings["shotID"]["enabled"] else None,
                "task" if current_settings["task"]["enabled"] else None,
                "version" if current_settings["version"]["enabled"] else None
            ] if c])

            itm["naming_preview"].Text = f"{example}" if example else "No components selected"
            itm["naming_format"].Text = f"Format: {format_text}" if format_text else "Invalid format"

        except Exception as e:
            print(f"Preview error: {str(e)}")
            itm["naming_preview"].Text = "Error in preview"
    else:
        itm["naming_preview"].Text = "Uncheck 'Use filename from current render preset' for custom filename"

# Create main window
window = disp.AddWindow({
    "WindowTitle": "Markers to Render Queue",
    "ID": "MTRWin",
    'WindowFlags': {'Window': True, 'WindowStaysOnTopHint': True},
    "Geometry": [1000, 400, 670, 770],
}, main_ui())

# Get UI items for global access
itm = window.GetItems()

def get_current_settings():
    """
    Retrieves the current naming settings from the UI.

    Returns:
        dict: A dictionary containing all naming settings.
    """
    return {
        "component1": {
            "enabled": itm["component1_enabled"].Checked,
            "source": itm["component1_source"].CurrentText,
            "custom": itm["component1_custom"].Text
        },
        "component2": {
            "enabled": itm["component2_enabled"].Checked,
            "source": itm["component2_source"].CurrentText,
            "custom": itm["component2_custom"].Text
        },
        "component3": {
            "enabled": itm["component3_enabled"].Checked,
            "source": itm["component3_source"].CurrentText,
            "custom": itm["component3_custom"].Text
        },
        "shotID": {
            "enabled": itm["shotID_enabled"].Checked,
            "source": itm["shotID_source"].CurrentText,
            "start": itm["shotID_start"].Value,
            "step": itm["shotID_step"].Value,
            "padding": itm["shotID_padding"].Value
        },
        "task": {
            "enabled": itm["task_enabled"].Checked,
            "source": itm["task_source"].CurrentText,
            "custom": itm["task_custom"].Text
        },
        "version": {
            "enabled": itm["version_enabled"].Checked,
            "prefix": itm["version_prefix"].Text,
            "start": itm["version_start"].Value,
            "padding": itm["version_padding"].Value
        }
    }

################################################################################################
# MARKER AND TIMELINE MANAGEMENT
################################################################################################

def get_marker_type(marker):
    """
    Determines if a marker is single or duration type.

    Args:
        marker (dict): Marker data from timeline

    Returns:
        str: "single" or "duration"
    """
    if "duration" in marker:
        duration = marker.get("duration", 0)
        if duration > 1:
            debug_print(f"Duration marker found: {duration} frames")
            return "duration"
        else:
            debug_print(f"Single marker found: {duration} frame(s)")
            return "single"

    if "end" in marker and marker.get("end", 0) > marker.get("start", 0):
        debug_print("Duration marker found (end-start)")
        return "duration"

    debug_print("Single marker found (no duration info)")
    return "single"

def get_markers(tl):
    """
    Retrieves markers of the selected color and type from the timeline.

    Args:
        tl (Timeline): The timeline object to search for markers.

    Returns:
        tuple: A tuple containing:
            - A list of marker positions.
            - A dictionary of all markers with their details.
    """
    color_text = itm["marker_color"].CurrentText
    color = color_text.split(" (")[0]
    marker_type = "duration" if itm["marker_type"].CurrentText == "Duration" else "single"
    debug_print(f"Looking for {color} {marker_type} markers")

    markers = tl.GetMarkers()
    color_markers = []

    for frame, marker in markers.items():
        # Check color filter
        if color != "All" and marker.get("color") != color:
            continue

        # Check marker type filter
        if get_marker_type(marker) != marker_type:
            continue

        color_markers.append(frame)

    if not color_markers:
        print(f"ERROR: No {color} {marker_type} markers found")

    return sorted(color_markers), markers


def get_used_marker_colors(timeline):
    """
    Retrieves a list of marker colors with their counts used in the timeline.

    Args:
        timeline (Timeline): The timeline object to analyze.

    Returns:
        list: A list of colors with their counts in the format "Color (Count)".
    """
    markers = timeline.GetMarkers()
    color_counts = {}

    for marker in markers.values():
        if "color" in marker:
            color = marker["color"]
            color_counts[color] = color_counts.get(color, 0) + 1

    color_list = [f"All ({len(markers)})"]
    color_list.extend(f"{color} ({count})" for color, count in sorted(color_counts.items()))

    return color_list


def tl_idx(proj, tl_name):
    """
    Retrieves the index of a timeline by its name.

    Args:
        proj (Project): The project object containing the timelines.
        tl_name (str): The name of the timeline to find.

    Returns:
        int: The index of the timeline, or None if not found.
    """
    debug_print(f"Looking for timeline: {tl_name}")
    for i in range(1, proj.GetTimelineCount() + 1):
        if proj.GetTimelineByIndex(i).GetName() == tl_name:
            debug_print(f"Found timeline at index: {i}")
            return int(i)
    return None


def tl_lst(proj):
    """
    Retrieves the name of the current timeline.

    Args:
        proj (Project): The project object containing the timelines.

    Returns:
        str: The name of the current timeline, or None if no timeline is active.
    """
    current_timeline = proj.GetCurrentTimeline()
    return current_timeline.GetName() if current_timeline else None

def validate_frame_range(in_point, out_point, timeline):
    """
    Validate and adjust frame range if necessary

    Args:
        in_point (int): Start frame
        out_point (int): End frame
        timeline (Timeline): Timeline object

    Returns:
        tuple: (validated_in, validated_out) or (None, None) if invalid
    """
    timeline_start = timeline.GetStartFrame()
    timeline_end = timeline.GetEndFrame()

    # Adjust values if they go beyond timeline boundaries
    in_point = max(timeline_start, in_point)
    out_point = min(timeline_end, out_point)

    # Check range validity
    if in_point >= out_point:
        print(f"Warning: Invalid frame range: {in_point} to {out_point}")
        return None, None

    return in_point, out_point

def get_duration_marker_range(marker):
    """
    Gets the render range for a duration marker.

    Args:
        marker (dict): Duration marker data

    Returns:
        tuple: (start_frame, end_frame)
    """
    start_frame = marker.get("start", 0)
    duration = marker.get("duration", 0)

    if duration <= 1:
        print(f"Warning: Duration marker has duration <= 1: {duration}")
        return start_frame, start_frame + 1

    end_frame = start_frame + duration
    debug_print(f"Duration marker range: {start_frame} - {end_frame} ({duration} frames)")
    return start_frame, end_frame

def get_sorted_marker_frames(markers):
    """
    Returns marker frames sorted by their position on timeline
    """
    sorted_frames = sorted(markers)
    if DEBUG_MODE:
        print("Sorted marker frames:")
        for frame in sorted_frames:
            print(f"Frame position: {frame}")
    return sorted_frames

def analyze_timeline_tracks(timeline, marker_frame):
    """
    Analyzes presence of video and audio clips on the timeline.

    Returns:
        tuple: (has_video, has_audio, video_info, audio_info)
    """
    video_clips = []
    audio_clips = []

    # Check video tracks
    video_track_count = timeline.GetTrackCount("video")
    for track_index in range(video_track_count):
        track = timeline.GetItemListInTrack("video", track_index + 1)
        for clip in track:
            if clip.GetMediaPoolItem():
                video_clips.append({
                    'track': track_index + 1,
                    'count': len(track)
                })
                break

    # Check audio tracks only if no video
    if not video_clips:
        audio_track_count = timeline.GetTrackCount("audio")
        for track_index in range(audio_track_count):
            track = timeline.GetItemListInTrack("audio", track_index + 1)
            for clip in track:
                if clip.GetMediaPoolItem():
                    audio_clips.append({
                        'track': track_index + 1,
                        'count': len(track)
                    })
                    break

    return bool(video_clips), bool(audio_clips), video_clips, audio_clips

################################################################################################
# CLIP MANAGEMENT
################################################################################################
def process_track_clips(track, track_index, marker_frame, track_type):
    """
    Processes clips on the track and finds the clip at the given marker position.

    Args:
        track: Track to process
        track_index: Track index
        marker_frame: Marker frame
        track_type: Track type ("video" or "audio")

    Returns:
        dict: Found clip info or None
    """
    for clip in track:
        clip_start_timeline = clip.GetStart()
        clip_end_timeline = clip_start_timeline + clip.GetDuration() - 1

        media_pool_item = clip.GetMediaPoolItem()
        if media_pool_item and clip_start_timeline <= marker_frame <= clip_end_timeline:
            return {
                'clip': clip,
                'timeline_start': clip_start_timeline,
                'timeline_end': clip_end_timeline,
                'clip_in': clip.GetLeftOffset(),
                'clip_out': clip.GetLeftOffset() + clip.GetDuration() - 1,
                'duration': clip.GetDuration(),
                'track': track_index + 1,
                'track_type': track_type,
                'media_pool_item': media_pool_item
            }
    return None

def get_clip_at_marker(timeline, marker_frame):

    has_video, has_audio, video_tracks, audio_tracks = analyze_timeline_tracks(timeline, marker_frame)

    if has_video:
        selected_track = itm["video_track"].CurrentText
        video_track_count = timeline.GetTrackCount("video")

        if selected_track != "Default (Topmost)":

            try:
                track_index = int(selected_track.split()[-1]) - 1
                if 0 <= track_index < video_track_count:
                    track = timeline.GetItemListInTrack("video", track_index + 1)
                    clip_info = process_track_clips(track, track_index, marker_frame, "video")
                    if clip_info:
                        update_status(f"Processing video track {track_index + 1}")
                        return clip_info
                    else:
                        update_status(f"No clip found on selected Video Track {track_index + 1}")
                        return None
                else:
                    update_status(f"Invalid track index: {track_index + 1}")
                    return None
            except ValueError:
                update_status("Error parsing selected track")
                return None
        else:

            for track_index in range(video_track_count - 1, -1, -1):
                track = timeline.GetItemListInTrack("video", track_index + 1)
                clip_info = process_track_clips(track, track_index, marker_frame, "video")
                if clip_info:
                    update_status(f"Processing video track {track_index + 1}")
                    return clip_info
    elif has_audio:

        for track_index in range(timeline.GetTrackCount("audio")):
            track = timeline.GetItemListInTrack("audio", track_index + 1)
            clip_info = process_track_clips(track, track_index, marker_frame, "audio")
            if clip_info:
                update_status(f"Processing audio track {track_index + 1}")
                return clip_info

    return None


def get_all_clips_at_marker(timeline, marker_frame, next_marker_frame=None):
    """
    Gets all clips from all video tracks at the given marker position.
    Uses next marker position as boundary for clip search (all clips between markers are included).

    Args:
        timeline: Timeline object
        marker_frame: Marker frame position (start of search zone)
        next_marker_frame: Next marker frame position (end of search zone), None if last marker

    Returns:
        list: List of clip_info dicts from all tracks with clips at marker position
    """
    clips = []
    has_video, _, _, _ = analyze_timeline_tracks(timeline, marker_frame)

    if has_video:
        video_track_count = timeline.GetTrackCount("video")

        for track_index in range(video_track_count):
            track = timeline.GetItemListInTrack("video", track_index + 1)
            for clip in track:
                clip_start = clip.GetStart()
                clip_end = clip_start + clip.GetDuration() - 1
                media_pool_item = clip.GetMediaPoolItem()

                if not media_pool_item:
                    continue

                # Include clip if it starts at or after the marker frame
                # This avoids including long background clips that started before the marker
                zone_end = next_marker_frame if next_marker_frame else clip_end + 1

                # Clip is included if:
                # 1. It starts at or after marker_frame (NOT before - excludes background clips)
                # 2. It starts before zone_end
                # This gets the "staircase" clips, not background clips that pass through
                debug_print(f"Checking clip at {clip_start}-{clip_end}, marker zone: {marker_frame}-{zone_end}, starts at/after marker: {clip_start}>={marker_frame}={clip_start >= marker_frame}")
                if clip_start >= marker_frame and clip_start < zone_end:
                    clip_info = {
                        'clip': clip,
                        'timeline_start': clip_start,
                        'timeline_end': clip_end,
                        'clip_in': clip.GetLeftOffset(),
                        'clip_out': clip.GetLeftOffset() + clip.GetDuration() - 1,
                        'duration': clip.GetDuration(),
                        'track': track_index + 1,
                        'track_type': "video",
                        'media_pool_item': media_pool_item
                    }
                    clips.append(clip_info)
                    debug_print(f"Found clip on video track {track_index + 1} at {clip_start}-{clip_end}")

    return clips

################################################################################################
# RENDER AND EXPORT
################################################################################################

def create_render_folder_path(base_path, filename, clip_info=None, all_markers=None):
    """
    Creates a folder path for render output based on filename and settings.

    Args:
        base_path (str): Base export directory
        filename (str): Generated filename for the render (empty if using preset naming)
        clip_info (dict): Information about the clip (optional)
        all_markers (dict): Dictionary with all markers (optional)

    Returns:
        str: Full path to the render folder
    """
    if not itm["create_folders"].Checked:
        return base_path

    try:
        # If filename is empty (preset naming), generate folder name from marker/clip info
        if not filename or filename.strip() == "":
            folder_name = "Render"
            if clip_info and clip_info.get('marker_frame') is not None:
                marker_frame = clip_info['marker_frame']
                if all_markers and marker_frame in all_markers:
                    marker_data = all_markers[marker_frame]
                    marker_name = marker_data.get('name', '')
                    if marker_name:
                        folder_name = sanitize_filename(marker_name)
                    else:
                        # Use marker note or generate from frame number
                        marker_note = marker_data.get('note', '')
                        if marker_note:
                            folder_name = sanitize_filename(marker_note)
                        else:
                            folder_name = f"Marker_{marker_frame}"
                else:
                    folder_name = f"Marker_{marker_frame}"
            elif clip_info and clip_info.get('media_pool_item'):
                # Fallback to source name
                source_name = clip_info['media_pool_item'].GetName()
                if source_name:
                    folder_name = sanitize_filename(source_name.rsplit('.', 1)[0] if '.' in source_name else source_name)
        else:
            # Clean filename - remove extension and trailing dot for EXR sequences
            folder_name = filename.rstrip('.')
            # Remove any file extension that might be present
            if '.' in folder_name:
                # For EXR sequences, filename ends with ".", so we just strip it
                # For other formats, remove the extension
                parts = folder_name.rsplit('.', 1)
                if len(parts) > 1 and parts[1] in ['mov', 'mp4', 'mxf', 'dpx', 'tiff', 'tif', 'jpg', 'jpeg', 'png', 'exr']:
                    folder_name = parts[0]
                else:
                    folder_name = folder_name.rstrip('.')

        # Sanitize folder name
        folder_name = sanitize_filename(folder_name)

        # Standard folder creation - just create folder with filename
        final_path = os.path.join(base_path, folder_name)
        os.makedirs(final_path, exist_ok=True)
        print(f"Created render folder: {final_path}")
        return final_path

    except Exception as e:
        error_msg = f"Error creating render folder: {str(e)}"
        print(error_msg)
        update_status(error_msg)
        # Return base path if folder creation fails
        return base_path

def update_export_button_state():
    """
    Updates the enabled state of the Export button based on the export path.

    If the export path is set and not empty, the Export button is enabled.
    Otherwise, it is disabled.
    """
    export_path = itm["export_path"].CurrentText
    itm["Export"].Enabled = bool(export_path and export_path.strip())


def get_filenames(markers, all_markers):
    if itm["use_preset_naming"].Checked:
        return {}

    filename_map = {}


    render_preset_name = itm["render_preset"].CurrentText
    project.LoadRenderPreset(render_preset_name)


    render_info = project.GetCurrentRenderFormatAndCodec()
    render_format = render_info.get('format', '').lower()
    debug_print(f"Current render format: {render_format}")


    is_exr = render_format == 'exr'
    debug_print(f"is_exr: {is_exr}")

    for counter, mark in enumerate(sorted(markers)):
        clip_info = get_clip_at_marker(timeline, timeline.GetStartFrame() + mark)
        if clip_info:
            clip_info['marker_frame'] = mark
            components = generate_naming_components(clip_info, counter, all_markers)

            filename = "_".join(filter(None, components.values()))


            if is_exr:
                filename = filename + "."
                debug_print(f"EXR sequence detected, adding dot suffix. Filename: {filename}")

            filename_map[mark] = filename

    return filename_map

def preset_lst(proj):
    """
    Retrieves a list of render presets available in the project.

    Args:
        proj (Project): The project object containing the render presets.

    Returns:
        list: A list of render preset names, sorted alphabetically.
    """
    presets = []

    # Get all render presets
    all_presets = proj.GetRenderPresetList()

    # Sort presets alphabetically
    if all_presets:
        presets = sorted(all_presets)

    return presets



def export_stills(proj, tl, markers, all_markers, path, filenames):
    proj.SetCurrentTimeline(tl)
    print("Timeline set.")

    print("Loading render preset...")
    proj.LoadRenderPreset(itm["render_preset"].CurrentText)
    print("Render preset loaded.")



    has_video, has_audio, video_tracks, audio_tracks = analyze_timeline_tracks(tl, 0)

    if not (has_video or has_audio):
        update_status("No media clips found to export")
        return

    media_type = "video" if has_video else "audio"
    update_status(f"Starting export of {len(markers)} {media_type} clips...")

    start_frame = tl.GetStartFrame()
    queued_clips = []
    queued_set = set()

    initial_jobs = set(job['JobId'] for job in proj.GetRenderJobList() or [])

    print(f"Processing {media_type} clips under markers...")

    for mark in sorted(markers):
        if _cancel_render:
            update_status("Render cancelled by user.")
            print("Render cancelled by user.")
            break

        marker_frame = start_frame + mark
        marker_data = all_markers.get(mark, {})
        marker_type = get_marker_type(marker_data)

        if marker_type == "duration":
            # Handle duration markers
            in_point = start_frame + mark
            out_point = start_frame + mark + marker_data['duration'] - 1

            # Validate frame range
            validated_in, validated_out = validate_frame_range(in_point, out_point, tl)
            if validated_in is None or validated_out is None:
                print(f"Skipping duration marker {mark} due to invalid frame range")
                continue

            print(f"\nProcessing duration marker {mark}: {validated_in} - {validated_out} (duration: {marker_data['duration']})")

            filename = filenames.get(mark, "")

            # Create folder path based on filename and settings
            clip_info_for_folder = {
                'marker_frame': mark,
                'timeline_start': validated_in,
                'timeline_end': validated_out
            }
            target_dir = create_render_folder_path(path, filename, clip_info_for_folder, all_markers)

            render_settings = {
                "MarkIn": validated_in,
                "MarkOut": validated_out,
                "TargetDir": target_dir
            }

            if not itm["use_preset_naming"].Checked and filename:
                render_settings["CustomName"] = filename

            try:
                proj.SetRenderSettings(render_settings)
                proj.AddRenderJob()

                clip_info = {
                    'timeline_start': validated_in,
                    'timeline_end': validated_out,
                    'job_id': None,
                    'target_dir': target_dir,
                    'media_type': media_type,
                    'marker_frame': mark,
                    'marker_type': 'duration'
                }
                queued_clips.append(clip_info)

                update_status(f"Added render job for duration marker at frame {marker_frame}")
                print(f"Added render job for duration marker at frame {marker_frame} to {target_dir}")
            except Exception as e:
                error_msg = f"Error adding render job: {str(e)}"
                update_status(error_msg)
                print(error_msg)
        else:
            # Handle single markers
            render_all_tracks = itm["render_all_tracks"].Checked

            if render_all_tracks:
                # Use ALL timeline markers (not just filtered) for zone boundary —
                # must match populate_markers_table which also uses all markers
                sorted_all_marker_frames = sorted(all_markers.keys())
                current_idx_all = sorted_all_marker_frames.index(mark) if mark in sorted_all_marker_frames else -1
                if current_idx_all >= 0 and current_idx_all + 1 < len(sorted_all_marker_frames):
                    next_marker_frame = start_frame + sorted_all_marker_frames[current_idx_all + 1]
                else:
                    next_marker_frame = None

                # Get all clips from all video tracks
                all_clips = get_all_clips_at_marker(tl, marker_frame, next_marker_frame)

                if all_clips:
                    print(f"\nProcessing single marker {mark} - found {len(all_clips)} clip(s) on different tracks")

                    filename = filenames.get(mark, "")

                    clip_info_for_folder = {
                        'marker_frame': mark,
                        'timeline_start': min(clip['timeline_start'] for clip in all_clips),
                        'timeline_end': max(clip['timeline_end'] for clip in all_clips)
                    }
                    target_dir = create_render_folder_path(path, filename, clip_info_for_folder, all_markers)

                    # Group clips by track number
                    clips_by_track = {}
                    for clip_info in all_clips:
                        t = clip_info['track']
                        clips_by_track.setdefault(t, []).append(clip_info)

                    # Process tracks bottom-to-top so suffix numbers are logical
                    # (Track 1 → _001/_002, Track 2 → _003/_004, etc.)
                    # For each track: disable all others → render → restore all others
                    tracks_sorted_bottom_up = sorted(clips_by_track.keys())
                    total_video_tracks = tl.GetTrackCount("video")
                    global_clip_idx = 0

                    for track_num in tracks_sorted_bottom_up:
                        track_clips = clips_by_track[track_num]

                        # Disable every track except the current one
                        tracks_to_restore = []
                        try:
                            resolve.OpenPage("edit")
                            time.sleep(0.2)
                            for t in range(1, total_video_tracks + 1):
                                if t != track_num:
                                    tl.SetTrackEnable("video", t, False)
                                    tracks_to_restore.append(t)
                                    print(f"Disabled track {t} (rendering track {track_num})")
                        except Exception as e:
                            debug_print(f"Error disabling tracks: {e}")

                        for clip_info in track_clips:
                            if _cancel_render:
                                break

                            global_clip_idx += 1
                            clip_info['marker_frame'] = mark

                            clip_id = (clip_info['timeline_start'], clip_info['timeline_end'], clip_info['track'])
                            if clip_id in queued_set:
                                print(f"Clip on track {track_num} already queued.")
                                continue

                            # Clamp MarkOut to next marker boundary so clips that extend
                            # past the next marker are not rendered beyond it
                            mark_out = clip_info['timeline_end']
                            if next_marker_frame is not None:
                                mark_out = min(mark_out, next_marker_frame - 1)

                            render_settings = {
                                "MarkIn": clip_info['timeline_start'],
                                "MarkOut": mark_out,
                                "TargetDir": target_dir
                            }

                            if not itm["use_preset_naming"].Checked and filename:
                                custom_name = filename
                                if len(all_clips) > 1:
                                    counter_suffix = f"_{global_clip_idx:03d}"
                                    if custom_name.endswith('.'):
                                        custom_name = custom_name[:-1] + counter_suffix + '.'
                                    elif '.' in custom_name:
                                        parts = custom_name.rsplit('.', 1)
                                        custom_name = parts[0] + counter_suffix + '.' + parts[1]
                                    else:
                                        custom_name = custom_name + counter_suffix
                                render_settings["CustomName"] = custom_name

                            try:
                                jobs_before = set(j['JobId'] for j in proj.GetRenderJobList() or [])
                                proj.SetRenderSettings(render_settings)
                                proj.AddRenderJob()

                                jobs_after = proj.GetRenderJobList() or []
                                new_job = next((j for j in jobs_after if j['JobId'] not in jobs_before), None)

                                if new_job:
                                    update_status(f"Rendering track {track_num}: frames {clip_info['timeline_start']}-{mark_out}")
                                    print(f"Rendering track {track_num}: frames {clip_info['timeline_start']}-{mark_out}")
                                    proj.StartRendering([new_job['JobId']])
                                    while proj.IsRenderingInProgress():
                                        if _cancel_render:
                                            proj.StopRendering()
                                            print(f"Render stopped by user on track {track_num}")
                                            break
                                        time.sleep(0.1)
                                    print(f"Done rendering track {track_num}")

                                    clip_info.update({
                                        'job_id': new_job['JobId'],
                                        'target_dir': target_dir,
                                        'media_type': media_type
                                    })
                                    queued_clips.append(clip_info)
                                    queued_set.add(clip_id)

                            except Exception as e:
                                error_msg = f"Error rendering clip on track {track_num}: {str(e)}"
                                update_status(error_msg)
                                print(error_msg)

                        # Restore all tracks disabled for this pass
                        try:
                            resolve.OpenPage("edit")
                            time.sleep(0.2)
                            for t in tracks_to_restore:
                                tl.SetTrackEnable("video", t, True)
                                debug_print(f"Restored track {t}")
                        except Exception as e:
                            debug_print(f"Error restoring tracks: {e}")
                else:
                    print(f"No {media_type} clips found at marker frame {marker_frame}")
            else:
                # Original single track logic
                clip_info = get_clip_at_marker(tl, marker_frame)

                if clip_info:
                    print(f"\nProcessing single marker {mark}:")

                    # Add marker_frame to clip_info
                    clip_info['marker_frame'] = mark
                    print(f"Added marker_frame to clip_info: {mark}")

                    clip_id = (clip_info['timeline_start'], clip_info['timeline_end'], clip_info['track'])

                    if clip_id not in queued_set:
                        filename = filenames.get(mark, "")

                        # Create folder path based on filename and settings
                        target_dir = create_render_folder_path(path, filename, clip_info, all_markers)

                        render_settings = {
                            "MarkIn": clip_info['timeline_start'],
                            "MarkOut": clip_info['timeline_end'],
                            "TargetDir": target_dir
                        }

                        if not itm["use_preset_naming"].Checked and filename:
                            render_settings["CustomName"] = filename

                        try:
                            proj.SetRenderSettings(render_settings)
                            proj.AddRenderJob()

                            clip_info.update({
                                'job_id': None,
                                'target_dir': target_dir,
                                'media_type': media_type
                            })
                            queued_clips.append(clip_info)
                            queued_set.add(clip_id)

                            update_status(f"Added render job for {media_type} clip at frame {marker_frame}")
                            print(f"Added render job for {media_type} clip at frame {marker_frame} to {target_dir}")
                        except Exception as e:
                            error_msg = f"Error adding render job: {str(e)}"
                            update_status(error_msg)
                            print(error_msg)
                    else:
                        print("Clip already queued.")
                else:
                    print(f"No {media_type} clip found at marker frame {marker_frame}")

    final_jobs = proj.GetRenderJobList() or []
    new_job_ids = set(job['JobId'] for job in final_jobs) - initial_jobs

    for clip_info in queued_clips:
        for job in final_jobs:
            if job['JobId'] in new_job_ids:
                if job['MarkIn'] == clip_info['timeline_start'] and job['MarkOut'] == clip_info['timeline_end']:
                    clip_info['job_id'] = job['JobId']
                    clip_info['render_name'] = job['OutputFilename']

    # Return to Deliver page so user can see the populated render queue
    if itm["render_all_tracks"].Checked:
        try:
            resolve.OpenPage("deliver")
            print("Switched back to Deliver page.")
        except Exception as e:
            debug_print(f"Could not switch to Deliver page: {e}")

    if _cancel_render:
        status_message = f"Render cancelled. Clips processed before stop: {len(queued_clips)}"
    else:
        status_message = f"Render queue setup complete. Total {media_type} clips queued: {len(queued_clips)}"
    update_status(status_message)
    print(status_message)


################################################################################################
# MAIN EXECUTION
################################################################################################

def _main(ev):
    """
    Main execution function for rendering. Processes markers and adds clips to the render queue.

    Args:
        ev: The event object triggering this function.
    """
    global _cancel_render
    _cancel_render = False
    itm["Export"].Enabled = False
    itm["StopRender"].Enabled = True

    # Snapshot UI values on the main thread before handing off
    tl_name = itm["tl_preset"].CurrentText
    path = itm["export_path"].CurrentText

    def _run():
        try:
            pm = resolve.GetProjectManager()
            proj = pm.GetCurrentProject()
            tl = proj.GetTimelineByIndex(tl_idx(proj, tl_name))
            markers, all_markers = get_markers(tl)
            filename_map = get_filenames(markers, all_markers)
            export_stills(proj, tl, markers, all_markers, path, filename_map)
        except Exception as e:
            import traceback
            traceback.print_exc()
            update_status(f"Export error: {str(e)}")
            print(f"Export error: {str(e)}")
        finally:
            itm["Export"].Enabled = True
            itm["StopRender"].Enabled = False

    threading.Thread(target=_run, daemon=True).start()

################################################################################################
# UI EVENT HANDLERS
################################################################################################
def load_naming_presets():
    """
    Loads naming presets into the dropdown list.
    """
    try:
        itm["naming_presets"].Clear()

        if not os.path.exists(PRESETS_FILE):
            _ensure_settings_dir()
            with open(PRESETS_FILE, 'w') as f:
                json.dump({}, f)
            itm["naming_presets"].AddItem("No presets available")
            return

        with open(PRESETS_FILE, 'r') as f:
            presets = json.load(f)

        if not presets:
            itm["naming_presets"].AddItem("No presets available")
            return


        preset_names = sorted(presets.keys())
        itm["naming_presets"].AddItems(preset_names)

    except Exception as e:
        print(f"Error loading naming presets: {str(e)}")
        itm["naming_presets"].AddItem("Error loading presets")
        update_status(f"Error loading presets: {str(e)}")

def save_naming_preset(ev=None):
    """
    Saves current naming settings as a preset.
    """
    try:
        preset_name = itm["preset_name_input"].Text.strip()
        if not preset_name:
            update_status("Preset name cannot be empty")
            return False


        settings = get_current_settings()


        presets = {}
        if os.path.exists(PRESETS_FILE):
            with open(PRESETS_FILE, 'r') as f:
                presets = json.load(f)


        if preset_name in presets:
            if not fu.AskQuestion("Overwrite Preset",
                                f"Preset '{preset_name}' already exists. Overwrite?"):
                update_status("Preset save cancelled")
                return False


        presets[preset_name] = settings

        _ensure_settings_dir()
        with open(PRESETS_FILE, 'w') as f:
            json.dump(presets, f, indent=4)


        load_naming_presets()


        itm["naming_presets"].CurrentText = preset_name
        itm["preset_name_input"].Text = ""

        update_status(f"Preset '{preset_name}' saved successfully")
        return True

    except Exception as e:
        update_status(f"Error saving preset: {str(e)}")
        print(f"Error saving preset: {str(e)}")
        return False

def load_naming_preset(ev=None):

    try:
        preset_name = itm["naming_presets"].CurrentText
        if not preset_name or preset_name == "No presets available":
            update_status("No preset selected")
            return False


        with open(PRESETS_FILE, 'r') as f:
            presets = json.load(f)

        if preset_name not in presets:
            update_status(f"Preset '{preset_name}' not found")
            return False


        settings = presets[preset_name]
        apply_settings_to_ui(settings)
        update_naming_preview()

        update_status(f"Preset '{preset_name}' loaded successfully")
        return True

    except Exception as e:
        update_status(f"Error loading preset: {str(e)}")
        print(f"Error loading preset: {str(e)}")
        return False

def delete_naming_preset(ev=None):

    try:
        preset_name = itm["naming_presets"].CurrentText
        if not preset_name or preset_name == "No presets available":
            update_status("No preset selected")
            return False



        with open(PRESETS_FILE, 'r') as f:
            presets = json.load(f)

        if preset_name not in presets:
            update_status(f"Preset '{preset_name}' not found")
            return False


        del presets[preset_name]

        _ensure_settings_dir()
        with open(PRESETS_FILE, 'w') as f:
            json.dump(presets, f, indent=4)


        load_naming_presets()

        update_status(f"Preset '{preset_name}' deleted successfully")
        return True

    except Exception as e:
        update_status(f"Error deleting preset: {str(e)}")
        print(f"Error deleting preset: {str(e)}")
        return False


def apply_settings_to_ui(settings):
    """
    Applies the given settings to the UI

    Args:
        settings (dict): The settings to apply
    """
    # Component 1
    if "component1" in settings:
        itm["component1_enabled"].Checked = settings["component1"]["enabled"]
        itm["component1_source"].CurrentText = settings["component1"]["source"]
        itm["component1_custom"].Text = settings["component1"]["custom"]
        toggle_custom_field("component1_source", "component1_custom")

    # Component 2
    if "component2" in settings:
        itm["component2_enabled"].Checked = settings["component2"]["enabled"]
        itm["component2_source"].CurrentText = settings["component2"]["source"]
        itm["component2_custom"].Text = settings["component2"]["custom"]
        toggle_custom_field("component2_source", "component2_custom")

    # Component 3
    if "component3" in settings:
        itm["component3_enabled"].Checked = settings["component3"]["enabled"]
        itm["component3_source"].CurrentText = settings["component3"]["source"]
        itm["component3_custom"].Text = settings["component3"]["custom"]
        toggle_custom_field("component3_source", "component3_custom")

    # ShotID
    if "shotID" in settings:
        itm["shotID_enabled"].Checked = settings["shotID"]["enabled"]
        itm["shotID_source"].CurrentText = settings["shotID"]["source"]
        itm["shotID_start"].Value = settings["shotID"]["start"]
        itm["shotID_step"].Value = settings["shotID"]["step"]
        itm["shotID_padding"].Value = settings["shotID"]["padding"]

    # Task
    if "task" in settings:
        itm["task_enabled"].Checked = settings["task"]["enabled"]
        itm["task_source"].CurrentText = settings["task"]["source"]
        itm["task_custom"].Text = settings["task"]["custom"]
        toggle_custom_field("task_source", "task_custom")

    # Version
    if "version" in settings:
        itm["version_enabled"].Checked = settings["version"]["enabled"]
        itm["version_prefix"].Text = settings["version"]["prefix"]
        itm["version_start"].Value = settings["version"]["start"]
        itm["version_padding"].Value = settings["version"]["padding"]

def _stop_render(ev):
    global _cancel_render
    _cancel_render = True
    itm["StopRender"].Enabled = False
    update_status("Stop requested — finishing current clip...")

def _close(ev):
    """
    Handles the window close event. Exits the UI event loop.

    Args:
        ev: The event object triggering this function.
    """
    disp.ExitLoop()



def _file_browser(ev):
    location = fu.RequestDir()
    if location:
        paths = update_render_paths(location)
        itm["export_path"].Clear()
        itm["export_path"].AddItems(paths)
        itm["export_path"].CurrentText = location

last_preview_update = 0
preview_cooldown = 0.5


is_updating_table = False
is_initializing = False

def initialize_naming_settings():
    """
    Initializes the naming settings comboboxes with predefined options.
    """
    naming_sources = {
        "component1_source": ["ProjectName", "TimelineName", "MarkerName", "MarkerNote", "Reel Name", "SourceName", "ClipName", "Custom"],
        "component2_source": ["ProjectName", "TimelineName", "MarkerName", "MarkerNote", "Reel Name", "SourceName", "ClipName", "Custom"],
        "component3_source": ["ProjectName", "TimelineName", "MarkerName", "MarkerNote", "Reel Name", "SourceName", "ClipName", "Custom"],
        "shotID_source": ["Auto Number", "Reel Name", "SourceName", "ClipName", "MarkerName", "MarkerNote"],
        "task_source": ["comp", "anim", "roto", "match", "paint", "Custom"]
    }

    for source_id, sources in naming_sources.items():
        itm[source_id].Clear()
        itm[source_id].AddItems(sources)

    load_naming_presets()

def toggle_custom_field(source_id, custom_id):
    """
    Enables or disables a custom field based on the selected source.

    Args:
        source_id (str): The ID of the source combobox.
        custom_id (str): The ID of the custom field to toggle.
    """
    is_custom = itm[source_id].CurrentText == "Custom"
    itm[custom_id].Enabled = is_custom
    update_naming_preview()


def setup_custom_field_handlers():
    """
    Sets up event handlers for all custom fields to toggle their enabled state.
    """
    custom_mappings = [
        ("component1_source", "component1_custom"),
        ("component2_source", "component2_custom"),
        ("component3_source", "component3_custom"),
        ("task_source", "task_custom")
    ]

    for source_id, custom_id in custom_mappings:
        # Set initial state
        toggle_custom_field(source_id, custom_id)

        # Add event handler for source change
        window.On[source_id].CurrentIndexChanged = lambda ev, s=source_id, c=custom_id: toggle_custom_field(s, c)

def bind_naming_preview_handlers():
    """
    Binds event handlers to update the naming preview when settings change.
    """
    # Checkboxes
    checkboxes = [
        "component1_enabled", "component2_enabled", "component3_enabled",
        "shotID_enabled", "task_enabled", "version_enabled"
    ]
    for cb in checkboxes:
        window.On[cb].Clicked = lambda _: update_naming_preview()

    # ComboBoxes
    comboboxes = [
        "component1_source", "component2_source", "component3_source",
        "shotID_source", "task_source"
    ]
    for cb in comboboxes:
        window.On[cb].CurrentIndexChanged = lambda _: update_naming_preview()

    # Text fields and spinboxes
    window.On.version_prefix.TextChanged = lambda _: update_naming_preview()
    window.On.version_start.ValueChanged = lambda _: update_naming_preview()
    window.On.version_padding.ValueChanged = lambda _: update_naming_preview()
    window.On.shotID_start.ValueChanged = lambda _: update_naming_preview()
    window.On.shotID_step.ValueChanged = lambda _: update_naming_preview()
    window.On.shotID_padding.ValueChanged = lambda _: update_naming_preview()

    # Custom fields
    custom_fields = [
        "component1_custom", "component2_custom", "component3_custom", "task_custom"
    ]
    for field in custom_fields:
        window.On[field].TextChanged = lambda _: update_naming_preview()

    # Preset naming checkbox
    window.On.use_preset_naming.Clicked = lambda _: update_naming_preview()


def toggle_naming_settings(ev):
    """
    Enables or disables naming settings based on render preset naming checkbox state
    """
    is_preset_naming = itm["use_preset_naming"].Checked

    # List of naming settings UI elements to disable
    naming_elements = [
        "component1_enabled", "component1_source", "component1_custom",
        "component2_enabled", "component2_source", "component2_custom",
        "component3_enabled", "component3_source", "component3_custom",
        "shotID_enabled", "shotID_source", "shotID_start", "shotID_step", "shotID_padding",
        "task_enabled", "task_source", "task_custom",
        "version_enabled", "version_prefix", "version_start", "version_padding"
    ]

    for element_id in naming_elements:
        itm[element_id].Enabled = not is_preset_naming
    label_style = "font-weight: bold; color: #777; font-size: 14px;" if is_preset_naming else "font-weight: bold; color: #FFFFFF; font-size: 16px;"
    window.Find("Custom Naming Settings").SetStyleSheet(label_style)
    update_naming_preview()


# Call this during final initialization
bind_naming_preview_handlers()
# Call this during initialization
setup_custom_field_handlers()
# Call this during initialization
initialize_naming_settings()

def populate_markers_table(ev=None):
    """
    Populates the markers table with markers from the current timeline.
    """
    table = itm["markers_table"]
    table.SetHeaderLabels(["Timecode", "Color", "Marker Name", "Source Name", "Clip Name", "Note", "Reel Name"])

    global is_updating_table
    debug_print(f"populate_markers_table called, event: {ev}, is_updating_table: {is_updating_table}")
    if is_updating_table:
        debug_print("Skipping due to is_updating_table")
        return

    try:
        is_updating_table = True
        current_timeline = project.GetCurrentTimeline()
        if not current_timeline:
            return

        markers = current_timeline.GetMarkers()
        if not markers:
            update_status("No markers found on timeline")
            return

        has_video, has_audio, video_tracks, audio_tracks = analyze_timeline_tracks(current_timeline, 0)

        if not (has_video or has_audio):
            update_status("No media clips found on timeline")
            return

        if has_video:
            tracks_info = ", ".join([f"Track {t['track']}: {t['count']} clips" for t in video_tracks])
            update_status(f"Found {len(markers)} markers. Video tracks: {tracks_info}")
        else:
            tracks_info = ", ".join([f"Track {t['track']}: {t['count']} clips" for t in audio_tracks])
            update_status(f"No video clips found. Processing {len(markers)} markers for audio. Audio tracks: {tracks_info}")

        table = itm["markers_table"]
        if not table:
            return

        # Clear the table before populating
        table.Clear()

        selected_color = itm["marker_color"].CurrentText.split(" (")[0]
        fps = float(current_timeline.GetSetting('timelineFrameRate'))
        start_frame = current_timeline.GetStartFrame()

        # Prepare data for the table
        table_items = []

        for frame, marker in sorted(markers.items()):
            if selected_color != "All" and marker.get("color", "") != selected_color:
                continue

            # Check marker type filter
            marker_type = get_marker_type(marker)
            selected_type = "duration" if itm["marker_type"].CurrentText == "Duration" else "single"
            if marker_type != selected_type:
                continue

            timeline_frame = start_frame + frame

            # For single markers with render_all_tracks enabled, get all clips
            render_all_tracks = itm["render_all_tracks"].Checked
            if marker_type == "single" and render_all_tracks:
                sorted_markers = sorted(markers.keys())
                current_idx = sorted_markers.index(frame)
                next_marker = sorted_markers[current_idx + 1] if current_idx + 1 < len(sorted_markers) else None
                next_marker_frame = (start_frame + next_marker) if next_marker is not None else None
                all_clips = get_all_clips_at_marker(current_timeline, timeline_frame, next_marker_frame)
                clip_infos = all_clips if all_clips else []
                print(f"DEBUG: Found {len(clip_infos)} clips at marker {frame} (render_all_tracks={render_all_tracks})")
            else:
                clip_info = get_clip_at_marker(current_timeline, timeline_frame)
                clip_infos = [clip_info] if clip_info else []
                print(f"DEBUG: Found clip_info at marker {frame}, render_all_tracks={render_all_tracks}, marker_type={marker_type}")

            # For duration markers, we don't need clip_info to be valid
            if marker_type == "duration" or clip_infos:
                total_frames = int(timeline_frame)
                timecode = smpte.gettc(total_frames)

                if marker_type == "duration" and not clip_infos:
                    # For duration markers without clips, add single row with marker info
                    table_items.append({
                        'timecode': timecode,
                        'color': marker.get("color", ""),
                        'name': marker.get("name", ""),
                        'source': "Duration Range",
                        'clip_name': "",
                        'note': marker.get("note", ""),
                        'reel_name': "",
                        'type': marker_type
                    })
                else:
                    # For markers with clips, add first row with marker info + first clip
                    for idx, clip_info in enumerate(clip_infos):
                        if clip_info:
                            source_name = clip_info['media_pool_item'].GetName()
                            clip_name = ""
                            if clip_info.get('clip'):
                                clip_name = clip_info['clip'].GetName() or ""

                            try:
                                reel_name = clip_info['media_pool_item'].GetClipProperty('Reel Name')
                            except Exception as e:
                                reel_name = ""

                            # First clip: include marker info (timecode, color, name, note)
                            # Additional clips: only show source and clip name (marker info empty)
                            if idx == 0:
                                table_items.append({
                                    'timecode': timecode,
                                    'color': marker.get("color", ""),
                                    'name': marker.get("name", ""),
                                    'source': source_name,
                                    'clip_name': clip_name,
                                    'note': marker.get("note", ""),
                                    'reel_name': reel_name,
                                    'type': marker_type
                                })
                            else:
                                # Additional clips: same timecode to keep them grouped, empty other marker info
                                table_items.append({
                                    'timecode': timecode,
                                    'color': "",
                                    'name': "",
                                    'source': source_name,
                                    'clip_name': clip_name,
                                    'note': "",
                                    'reel_name': reel_name,
                                    'type': ""
                                })

        # Add items to the table
        for item_data in table_items:
            item = table.NewItem()
            item.Text[0] = item_data['timecode']
            item.Text[1] = item_data['color']
            item.Text[2] = item_data['name']
            item.Text[3] = item_data['source']
            item.Text[4] = item_data['clip_name']
            item.Text[5] = item_data['note']
            item.Text[6] = item_data['reel_name']  # Add Reel Name to the table
            table.AddTopLevelItem(item)
        table.SortByColumn(0, "AscendingOrder")
    except Exception as e:
        update_status(f"Error: {str(e)}")
        print(f"Error in populate_markers_table: {str(e)}")
    finally:
        is_updating_table = False
        debug_print("populate_markers_table finished")

def on_marker_double_clicked(ev):
    """
    Jumps to the marker position when a marker is double-clicked in the table.

    Args:
        ev: The event object containing the clicked item.
    """
    try:
        timecode = ev["item"].Text[0]
        timeline = project.GetCurrentTimeline()
        if timeline:
            timeline.SetCurrentTimecode(timecode)
            print(f"Moved playhead to timecode {timecode}")
    except Exception as e:
        print(f"Error in double click handler: {str(e)}")
        import traceback
        traceback.print_exc()

################################################################################################
# FILENAME MANAGEMENT
################################################################################################
def generate_naming_components(clip_info, counter, markers):
    """
    Generates naming components based on current settings

    Args:
        clip_info (dict): Information about the clip
        counter (int): Counter for the current clip
        markers (dict): Dictionary with marker information

    Returns:
        dict: Dictionary with naming components
    """
    current_settings = get_current_settings()
    components = {}

    # Get marker data
    marker_frame = clip_info.get('marker_frame')
    example_data = {}
    if marker_frame is not None and marker_frame in markers:
        marker_data = markers[marker_frame]
        example_data = {
            'marker_name': marker_data.get('name', ''),
            'marker_note': marker_data.get('note', ''),
            'reel_name': marker_data.get('reel_name', '')
        }

    # Process each component
    components["component1"] = get_component_value(current_settings["component1"], clip_info, example_data, "comp1", counter)
    components["component2"] = get_component_value(current_settings["component2"], clip_info, example_data, "comp2", counter)
    components["component3"] = get_component_value(current_settings["component3"], clip_info, example_data, "comp3", counter)

    # ShotID
    if current_settings["shotID"]["enabled"]:
        shot_value = get_component_value(current_settings["shotID"], clip_info, example_data, "SHOT010", counter)
        if shot_value:
            components["shotID"] = shot_value

    # Task
    if current_settings["task"]["enabled"]:
        if current_settings["task"]["source"] == "Custom":
            components["task"] = current_settings["task"]["custom"] or "task"
        else:
            components["task"] = current_settings["task"]["source"]

    # Version
    if current_settings["version"]["enabled"]:
        prefix = current_settings["version"]["prefix"] or "v"
        version_pad = current_settings["version"]["padding"]
        version_num = str(current_settings["version"]["start"]).zfill(version_pad)
        components["version"] = f"{prefix}{version_num}"

    # Remove None values
    return {k: v for k, v in components.items() if v is not None}

################################################################################################
# UI INITIALIZATION
################################################################################################

# Initialize UI state
is_initializing = True
try:
    # Populate marker colors, render presets, and timelines
    used_colors_with_counts = get_used_marker_colors(timeline)
    itm['marker_color'].AddItems(used_colors_with_counts)
    itm["render_preset"].AddItems(preset_lst(project))
    itm["tl_preset"].AddItems([tl_lst(project)])
    # Load saved render paths
    itm["export_path"].AddItems(load_render_paths())

    # Populate video tracks
    video_track_count = timeline.GetTrackCount("video")
    video_track_options = ["Default (Topmost)"] + [f"Video Track {i+1}" for i in range(video_track_count)]
    itm["video_track"].AddItems(video_track_options)

    # Initialize marker type combobox
    itm["marker_type"].AddItems(["Single", "Duration", "Single → Next Marker", "Single → Next Same Color"])
    itm["marker_type"].CurrentText = "Single"

    # Populate markers table
    populate_markers_table()
finally:
    is_initializing = False

# Restore the marker color change handler
window.On.marker_color.CurrentIndexChanged = populate_markers_table
window.On["video_track"].CurrentIndexChanged = populate_markers_table
window.On.marker_type.CurrentIndexChanged = populate_markers_table
window.On.render_all_tracks.Clicked = populate_markers_table

# Set up event handlers
window.On["export_path"].TextChanged = update_export_button_state
window.On.Export.Clicked = _main
window.On.StopRender.Clicked = _stop_render
window.On.export_location.Clicked = _file_browser
window.On.MTRWin.Close = _close
window.On["markers_table"].ItemDoubleClicked = on_marker_double_clicked
window.On.use_preset_naming.Clicked = toggle_naming_settings
# Button handlers
window.On.save_preset.Clicked = save_naming_preset
window.On.load_preset.Clicked = load_naming_preset
window.On.delete_preset.Clicked = delete_naming_preset

# Initialize naming preview
update_naming_preview()

# Initial state setup
toggle_naming_settings(None)

# Show window and start event loop
window.Show()
disp.RunLoop()
window.Hide()
