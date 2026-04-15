from __future__ import annotations

import hashlib
import json
import math
import queue
import subprocess
import threading
import time
import wave
from datetime import datetime
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import numpy as np

try:
    from .run_prepare_tts_dataset import DEFAULT_EXCLUDE_PATTERNS
    from .run_prepare_tts_dataset import main as prepare_main
    from .run_prepare_tts_dataset import set_cancel_event as set_prepare_cancel_event
    from .run_stage_and_prepare_tts_dataset import main as stage_and_prepare_main
    from .run_stage_and_prepare_tts_dataset import set_cancel_event as set_stage_cancel_event
except ImportError:
    from gamma.run_prepare_tts_dataset import DEFAULT_EXCLUDE_PATTERNS
    from gamma.run_prepare_tts_dataset import main as prepare_main
    from gamma.run_prepare_tts_dataset import set_cancel_event as set_prepare_cancel_event
    from gamma.run_stage_and_prepare_tts_dataset import main as stage_and_prepare_main
    from gamma.run_stage_and_prepare_tts_dataset import set_cancel_event as set_stage_cancel_event


@dataclass(slots=True)
class ReviewRecord:
    clip_id: str
    source_path: str
    clip_path: str
    text: str
    episode_id: str
    duration_seconds: float
    start_seconds: float
    end_seconds: float
    language: str
    avg_logprob: float | None
    no_speech_prob: float | None
    compression_ratio: float | None


class QueueWriter:
    def __init__(self, output_queue: queue.Queue[tuple[str, str]]) -> None:
        self._queue = output_queue
        self._buffer = ""

    def write(self, value: str) -> int:
        if not value:
            return 0
        self._buffer += value
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._queue.put(("log", line))
        return len(value)

    def flush(self) -> None:
        if self._buffer:
            self._queue.put(("log", self._buffer))
            self._buffer = ""


class ToolTip:
    def __init__(
        self,
        widget: tk.Widget,
        text: str,
        *,
        background: str,
        foreground: str,
        border: str,
        wraplength: int = 360,
        delay_ms: int = 450,
    ) -> None:
        self.widget = widget
        self.text = text
        self.background = background
        self.foreground = foreground
        self.border = border
        self.wraplength = wraplength
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._window: tk.Toplevel | None = None

        widget.bind("<Enter>", self._schedule_show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<FocusOut>", self._hide, add="+")

    def _schedule_show(self, _event: tk.Event[tk.Widget] | None = None) -> None:
        self._cancel_pending()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel_pending(self) -> None:
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self) -> None:
        if self._window is not None or not self.text.strip():
            return
        self._after_id = None
        x = self.widget.winfo_rootx() + 14
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        window.configure(bg=self.border)

        label = tk.Label(
            window,
            text=self.text,
            justify="left",
            anchor="w",
            wraplength=self.wraplength,
            bg=self.background,
            fg=self.foreground,
            relief="flat",
            bd=0,
            padx=10,
            pady=8,
            font=("Segoe UI", 9),
        )
        label.pack(padx=1, pady=1)
        self._window = window

    def _hide(self, _event: tk.Event[tk.Widget] | None = None) -> None:
        self._cancel_pending()
        if self._window is not None:
            self._window.destroy()
            self._window = None


class TTSDataPrepApp:
    BG = "#111315"
    PANEL = "#1a1d21"
    PANEL_ALT = "#16191c"
    TEXT = "#e7ebef"
    MUTED = "#9ca7b3"
    ACCENT = "#ff6b57"
    ACCENT_ALT = "#ff8d7a"
    BORDER = "#2a2f36"
    SUCCESS = "#6ecb8b"
    WARN = "#f2b36f"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Gamma TTS Dataset Prep")
        self.root.geometry("1580x940")
        self.root.configure(bg=self.BG)

        self.event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.pipeline_cancel_event: threading.Event | None = None
        self.data_root = self._default_data_root()
        self.data_root.mkdir(parents=True, exist_ok=True)

        self.source_var = tk.StringVar(value=r"\\10.78.78.250\Media\Anime\Shakugan no Shana\Shakugan no Shana")
        self.staging_var = tk.StringVar(value=str(self.data_root / "source_media" / "shakugan_no_shana"))
        self.dataset_var = tk.StringVar(value=str(self.data_root / "shana_dataset"))
        self.language_var = tk.StringVar(value="ja")
        self.model_var = tk.StringVar(value="small")
        self.stream_lang_var = tk.StringVar(value="jpn")
        self.exclude_var = tk.StringVar(value="recap")
        self.limit_files_var = tk.StringVar(value="0")
        self.max_seconds_var = tk.StringVar(value="12.0")
        self.overwrite_var = tk.BooleanVar(value=False)
        self.vocals_only_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Idle. The extractor finds candidate speech, not confirmed Shana lines.")
        self.review_filter_var = tk.StringVar(value="All")
        self.review_status_var = tk.StringVar(value="No manifest loaded.")
        self.export_status_var = tk.StringVar(value="No export has run yet.")
        self.trim_start_var = tk.StringVar(value="0.0")
        self.trim_end_var = tk.StringVar(value="")
        self.auto_advance_var = tk.BooleanVar(value=True)
        self.auto_play_var = tk.BooleanVar(value=False)
        self.bulk_label_var = tk.StringVar(value="Shana")

        self.records: list[ReviewRecord] = []
        self.labels: dict[str, dict[str, Any]] = {}
        self.current_record: ReviewRecord | None = None
        self.current_manifest_path: Path | None = None
        self.playback_thread: threading.Thread | None = None
        self.playback_process: subprocess.Popen[str] | None = None
        self.playback_started_at = 0.0
        self.playback_start_offset = 0.0
        self.playback_target_clip_id: str | None = None
        self.timeline_dragging = False
        self.playback_position_var = tk.DoubleVar(value=0.0)
        self.playback_time_var = tk.StringVar(value="00:00.00 / 00:00.00")
        self.timeline_scale: tk.Scale | None = None
        self.signature_cache: dict[str, np.ndarray] = {}
        self.similarity_scores: dict[str, float] = {}
        self.audio_hash_cache: dict[str, str] = {}
        self.exact_duplicates: dict[str, str] = {}
        self.near_duplicates: dict[str, tuple[str, float]] = {}
        self.tooltips: list[ToolTip] = []
        self.waveform_canvas: tk.Canvas | None = None
        self._waveform_sample_cache: dict[str, list[float]] = {}

        self.transcribe_path_var = tk.StringVar()
        self.transcribe_lang_var = tk.StringVar(value="ja")
        self.transcribe_model_var = tk.StringVar(value="small")
        self.transcribe_stream_lang_var = tk.StringVar(value="jpn")
        self.transcribe_timestamps_var = tk.BooleanVar(value=False)
        self.transcribe_status_var = tk.StringVar(value="Paste or browse to a media file and click Transcribe.")
        self.transcribe_worker: threading.Thread | None = None
        self.transcribe_text: tk.Text | None = None

        self._configure_style()
        self._build_ui()
        self._install_context_menus()
        self._bind_hotkeys()
        self.root.after(100, self._poll_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _default_data_root(self) -> Path:
        local_app_data = Path.home() / "AppData" / "Local"
        return (local_app_data / "GammaTTSDataPrep").resolve()

    def _configure_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=self.BG, foreground=self.TEXT, fieldbackground=self.PANEL_ALT)
        style.configure("TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.PANEL)
        style.configure("Panel.TFrame", background=self.PANEL_ALT)
        style.configure("TLabel", background=self.BG, foreground=self.TEXT)
        style.configure("Muted.TLabel", background=self.BG, foreground=self.MUTED)
        style.configure("CardTitle.TLabel", background=self.PANEL, foreground=self.TEXT, font=("Segoe UI Semibold", 11))
        style.configure("Accent.TButton", background=self.ACCENT, foreground="#101214", borderwidth=0, padding=8)
        style.map("Accent.TButton", background=[("active", self.ACCENT_ALT), ("disabled", self.BORDER)])
        style.configure("Ghost.TButton", background=self.PANEL_ALT, foreground=self.TEXT, bordercolor=self.BORDER, padding=8)
        style.map("Ghost.TButton", background=[("active", self.BORDER)])
        style.configure("TEntry", foreground=self.TEXT, fieldbackground=self.PANEL_ALT, bordercolor=self.BORDER)
        style.configure("TCombobox", foreground=self.TEXT, fieldbackground=self.PANEL_ALT, bordercolor=self.BORDER)
        style.configure("TNotebook", background=self.BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=self.PANEL_ALT, foreground=self.MUTED, padding=(12, 8))
        style.map("TNotebook.Tab", background=[("selected", self.PANEL)], foreground=[("selected", self.TEXT)])
        style.configure("Treeview", background=self.PANEL_ALT, foreground=self.TEXT, fieldbackground=self.PANEL_ALT, bordercolor=self.BORDER)
        style.configure("Treeview.Heading", background=self.PANEL, foreground=self.TEXT, bordercolor=self.BORDER)
        style.map("Treeview", background=[("selected", "#27313b")], foreground=[("selected", self.TEXT)])

    def _build_ui(self) -> None:
        wrapper = ttk.Frame(self.root)
        wrapper.pack(fill="both", expand=True, padx=16, pady=16)
        wrapper.columnconfigure(0, weight=5)
        wrapper.columnconfigure(1, weight=2)
        wrapper.rowconfigure(1, weight=1)

        header = ttk.Frame(wrapper, style="Card.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="nsew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Gamma TTS Dataset Prep", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", padx=14, pady=(12, 2))
        ttk.Label(
            header,
            text="Stage media locally, extract candidate speech, review clips, and tag which lines are actually Shana.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 12))

        notebook = ttk.Notebook(wrapper)
        notebook.grid(row=1, column=0, sticky="nsew", padx=(0, 14))

        pipeline_tab = ttk.Frame(notebook)
        review_tab = ttk.Frame(notebook)
        transcribe_tab = ttk.Frame(notebook)
        notebook.add(pipeline_tab, text="Pipeline")
        notebook.add(review_tab, text="Review")
        notebook.add(transcribe_tab, text="Transcribe")

        self._build_pipeline_tab(pipeline_tab)
        self._build_review_tab(review_tab)
        self._build_transcribe_tab(transcribe_tab)
        self._build_log_panel(wrapper)

    def _build_pipeline_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        form = ttk.Frame(parent, style="Card.TFrame")
        form.grid(row=0, column=0, sticky="nsew", pady=(0, 14))
        for index in range(3):
            form.columnconfigure(index, weight=1 if index == 1 else 0)

        ttk.Label(form, text="Source Share", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", padx=14, pady=(12, 8))
        source_widgets = self._labeled_entry(form, 1, "Source", self.source_var, self._browse_source)
        staging_widgets = self._labeled_entry(form, 2, "Staging", self.staging_var, self._browse_staging)
        dataset_widgets = self._labeled_entry(form, 3, "Dataset", self.dataset_var, self._browse_dataset)

        language_label = ttk.Label(form, text="Language", style="Muted.TLabel")
        language_label.grid(row=4, column=0, sticky="w", padx=14, pady=6)
        language_combo = ttk.Combobox(form, textvariable=self.language_var, values=("ja", "en", "auto"), state="readonly")
        language_combo.grid(row=4, column=1, sticky="ew", padx=8, pady=6)
        stt_label = ttk.Label(form, text="STT Model", style="Muted.TLabel")
        stt_label.grid(row=5, column=0, sticky="w", padx=14, pady=6)
        model_combo = ttk.Combobox(
            form,
            textvariable=self.model_var,
            values=("base", "small", "medium", "large-v3", "base.en", "small.en"),
        )
        model_combo.grid(row=5, column=1, sticky="ew", padx=8, pady=6)
        stream_label = ttk.Label(form, text="Audio Stream Lang", style="Muted.TLabel")
        stream_label.grid(row=6, column=0, sticky="w", padx=14, pady=6)
        stream_combo = ttk.Combobox(form, textvariable=self.stream_lang_var, values=("jpn", "eng", "ja", "en"), state="readonly")
        stream_combo.grid(row=6, column=1, sticky="ew", padx=8, pady=6)
        exclude_label = ttk.Label(form, text="Exclude Patterns", style="Muted.TLabel")
        exclude_label.grid(row=7, column=0, sticky="w", padx=14, pady=6)
        exclude_entry = ttk.Entry(form, textvariable=self.exclude_var)
        exclude_entry.grid(row=7, column=1, sticky="ew", padx=8, pady=6)
        limit_label = ttk.Label(form, text="Limit Files", style="Muted.TLabel")
        limit_label.grid(row=8, column=0, sticky="w", padx=14, pady=6)
        limit_entry = ttk.Entry(form, textvariable=self.limit_files_var)
        limit_entry.grid(row=8, column=1, sticky="ew", padx=8, pady=6)
        max_seconds_label = ttk.Label(form, text="Max Seconds", style="Muted.TLabel")
        max_seconds_label.grid(row=9, column=0, sticky="w", padx=14, pady=6)
        max_seconds_entry = ttk.Entry(form, textvariable=self.max_seconds_var)
        max_seconds_entry.grid(row=9, column=1, sticky="ew", padx=8, pady=6)
        overwrite_check = ttk.Checkbutton(form, text="Overwrite staged files", variable=self.overwrite_var)
        overwrite_check.grid(row=10, column=1, sticky="w", padx=8, pady=(6, 4))
        vocals_only_check = ttk.Checkbutton(form, text="Separate Vocals (Demucs)", variable=self.vocals_only_var)
        vocals_only_check.grid(row=11, column=1, sticky="w", padx=8, pady=(0, 12))

        actions = ttk.Frame(parent, style="Card.TFrame")
        actions.grid(row=1, column=0, sticky="nsew")
        actions.columnconfigure((0, 1, 2, 3), weight=1)

        ttk.Label(actions, text="Actions", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", padx=14, pady=(12, 8))
        stage_only_button = ttk.Button(actions, text="Stage Only", style="Ghost.TButton", command=self._run_stage_only)
        stage_only_button.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12))
        stage_prepare_button = ttk.Button(actions, text="Stage + Prepare", style="Accent.TButton", command=self._run_stage_and_prepare)
        stage_prepare_button.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(0, 12))
        prepare_staged_button = ttk.Button(actions, text="Prepare Staged", style="Ghost.TButton", command=self._run_prepare_only)
        prepare_staged_button.grid(row=1, column=2, sticky="ew", padx=(0, 8), pady=(0, 12))
        load_manifest_button = ttk.Button(actions, text="Load Manifest", style="Ghost.TButton", command=self._load_manifest_from_dataset)
        load_manifest_button.grid(row=1, column=3, sticky="ew", padx=(0, 14), pady=(0, 12))
        stop_pipeline_button = ttk.Button(actions, text="Stop Pipeline", style="Ghost.TButton", command=self._request_pipeline_stop)
        stop_pipeline_button.grid(row=2, column=0, columnspan=4, sticky="ew", padx=14, pady=(0, 12))

        status = ttk.Frame(parent, style="Card.TFrame")
        status.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        status.columnconfigure(0, weight=1)
        ttk.Label(status, text="Status", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", padx=14, pady=(12, 6))
        ttk.Label(status, textvariable=self.status_var, style="Muted.TLabel", wraplength=860, justify="left").grid(row=1, column=0, sticky="w", padx=14, pady=(0, 12))

        self._attach_tooltip(source_widgets[0], "Folder that contains the original episode files. This can be a UNC share or any local directory.")
        self._attach_tooltip(source_widgets[1], "Folder that contains the original episode files. The app copies from here into local staging so your originals stay untouched.")
        self._attach_tooltip(source_widgets[2], "Open a folder picker for the source media location.")
        self._attach_tooltip(staging_widgets[0], "Local working copy of the source media. Demuxing and extraction operate from here instead of the NAS share.")
        self._attach_tooltip(staging_widgets[1], "Local working copy of the source media. Rebuilds will not wipe this because it lives under Local AppData by default.")
        self._attach_tooltip(staging_widgets[2], "Open a folder picker for the local staging directory.")
        self._attach_tooltip(dataset_widgets[0], "Folder where extracted clips, manifests, labels, exports, and derived trims are stored.")
        self._attach_tooltip(dataset_widgets[1], "Folder where extracted clips, manifests, labels, exports, and derived trims are stored.")
        self._attach_tooltip(dataset_widgets[2], "Open a folder picker for the dataset output directory.")
        self._attach_tooltip(language_label, "Language to transcribe from the selected audio stream. Use Japanese for the original voice track and English for the dub.")
        self._attach_tooltip(language_combo, "Language to transcribe from the selected audio stream. 'auto' lets Whisper guess, but explicit language is usually more stable.")
        self._attach_tooltip(stt_label, "Whisper model used to transcribe and segment the audio. Larger models are slower but usually more accurate.")
        self._attach_tooltip(model_combo, "Whisper model used to transcribe and segment the audio. Avoid '.en' models when working with Japanese audio.")
        self._attach_tooltip(stream_label, "Preferred audio track language inside a dual-audio container. This decides whether the extractor uses Japanese or English.")
        self._attach_tooltip(stream_combo, "Preferred audio track language inside a dual-audio container. Use 'jpn' for the original voice and 'eng' for the dub.")
        self._attach_tooltip(exclude_label, "Comma-separated filename filters to skip low-value files like recaps, openings, endings, or extras.")
        self._attach_tooltip(exclude_entry, "Comma-separated filename filters to skip low-value files like recaps, openings, endings, or extras.")
        self._attach_tooltip(limit_label, "How many source files to process in this run. Use 0 to process everything.")
        self._attach_tooltip(limit_entry, "How many source files to process in this run. Use a small number for testing.")
        self._attach_tooltip(max_seconds_label, "Maximum accepted candidate clip length before the prep step rejects it as too long.")
        self._attach_tooltip(max_seconds_entry, "Maximum accepted candidate clip length before the prep step rejects it as too long. Raise this if useful lines are getting discarded.")
        self._attach_tooltip(overwrite_check, "Copy staged files again even if the destination already exists and looks unchanged.")
        self._attach_tooltip(vocals_only_check, "Run Demucs vocal separation before transcription. Strips music, SFX, and ambience so clips contain vocals only. Requires the 'demucs' package. Significantly slower — one pass per episode.")
        self._attach_tooltip(stage_only_button, "Copy media from the source folder into local staging without running transcription or clip extraction.")
        self._attach_tooltip(stage_prepare_button, "Copy media into staging, then run transcription and candidate clip extraction in one pass.")
        self._attach_tooltip(load_manifest_button, "Load the current dataset manifest into the review queue so you can inspect and label clips.")
        self._attach_tooltip(prepare_staged_button, "Extract candidate speech from already-staged files without re-copying from source. Use this to re-extract with different model or settings.")
        self._attach_tooltip(stop_pipeline_button, "Request a safe stop for the active staging or extraction job. Cancellation happens at checkpoints between files and clip operations.")

    def _build_review_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=5)
        parent.rowconfigure(1, weight=1)

        topbar = ttk.Frame(parent, style="Card.TFrame")
        topbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        for index in range(8):
            topbar.columnconfigure(index, weight=1 if index in {1, 3} else 0)
        ttk.Label(topbar, text="Review Queue", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", padx=14, pady=(12, 8))
        filter_label = ttk.Label(topbar, text="Filter", style="Muted.TLabel")
        filter_label.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 12))
        filter_combo = ttk.Combobox(
            topbar,
            textvariable=self.review_filter_var,
            values=("All", "Unlabeled", "Shana", "Shana-light-noise", "Shana-heavy-noise", "Not Shana", "Reject"),
            state="readonly",
        )
        filter_combo.grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=(0, 12))
        apply_filter_button = ttk.Button(topbar, text="Apply", style="Ghost.TButton", command=self._refresh_review_tree)
        apply_filter_button.grid(row=1, column=2, sticky="ew", padx=(0, 12), pady=(0, 12))
        rank_button = ttk.Button(topbar, text="Rank Likely Shana", style="Ghost.TButton", command=self._rank_similarity)
        rank_button.grid(row=1, column=3, sticky="ew", padx=(0, 12), pady=(0, 12))
        duplicate_button = ttk.Button(topbar, text="Find Duplicates", style="Ghost.TButton", command=self._detect_duplicates)
        duplicate_button.grid(row=1, column=4, sticky="ew", padx=(0, 12), pady=(0, 12))
        auto_advance_check = ttk.Checkbutton(topbar, text="Auto-Advance", variable=self.auto_advance_var)
        auto_advance_check.grid(row=1, column=5, sticky="w", padx=(0, 12), pady=(0, 12))
        auto_play_check = ttk.Checkbutton(topbar, text="Auto-Play", variable=self.auto_play_var)
        auto_play_check.grid(row=1, column=6, sticky="w", padx=(0, 12), pady=(0, 12))
        ttk.Label(topbar, textvariable=self.review_status_var, style="Muted.TLabel").grid(row=1, column=7, sticky="e", padx=14, pady=(0, 12))

        tree_card = ttk.Frame(parent, style="Card.TFrame")
        tree_card.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        tree_card.columnconfigure(0, weight=1)
        tree_card.rowconfigure(0, weight=1)

        self.review_tree = ttk.Treeview(tree_card, columns=("episode", "duration", "score", "dupe", "label"), show="headings", height=24, selectmode="extended")
        self.review_tree.heading("episode", text="Episode")
        self.review_tree.heading("duration", text="Seconds")
        self.review_tree.heading("score", text="Score")
        self.review_tree.heading("dupe", text="Duplicate")
        self.review_tree.heading("label", text="Label")
        self.review_tree.column("episode", width=280, anchor="w")
        self.review_tree.column("duration", width=80, anchor="center")
        self.review_tree.column("score", width=90, anchor="center")
        self.review_tree.column("dupe", width=120, anchor="center")
        self.review_tree.column("label", width=120, anchor="center")
        tree_scrollbar = ttk.Scrollbar(tree_card, orient="vertical", command=self.review_tree.yview)
        self.review_tree.configure(yscrollcommand=tree_scrollbar.set)
        self.review_tree.grid(row=0, column=0, sticky="nsew", padx=(12, 0), pady=12)
        tree_scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 12), pady=12)
        self.review_tree.bind("<<TreeviewSelect>>", self._on_review_select)
        self.review_tree.tag_configure("quality_ok", foreground=self.TEXT)
        self.review_tree.tag_configure("quality_warn", foreground=self.WARN)
        self.review_tree.tag_configure("quality_bad", foreground="#e07070")

        detail = ttk.Frame(parent, style="Card.TFrame")
        detail.grid(row=1, column=1, sticky="nsew")
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(2, weight=1)

        ttk.Label(detail, text="Clip Details", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", padx=14, pady=(12, 6))
        self.detail_meta = ttk.Label(detail, text="Select a clip from the queue.", style="Muted.TLabel", justify="left", wraplength=760)
        self.detail_meta.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))
        self.detail_text = tk.Text(
            detail,
            bg=self.PANEL_ALT,
            fg=self.TEXT,
            insertbackground=self.TEXT,
            wrap="word",
            relief="flat",
            font=("Consolas", 10),
            highlightthickness=1,
            highlightbackground=self.BORDER,
            padx=10,
            pady=10,
        )
        self.detail_text.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 10))

        self.waveform_canvas = tk.Canvas(
            detail,
            bg=self.PANEL_ALT,
            height=56,
            highlightthickness=1,
            highlightbackground=self.BORDER,
        )
        self.waveform_canvas.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 6))

        playback_panel = ttk.Frame(detail, style="Panel.TFrame")
        playback_panel.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 14))
        for index in range(5):
            playback_panel.columnconfigure(index, weight=1)
        self.timeline_scale = tk.Scale(
            playback_panel,
            from_=0.0,
            to=1.0,
            resolution=0.01,
            orient="horizontal",
            showvalue=False,
            variable=self.playback_position_var,
            command=self._on_timeline_change,
            bg=self.PANEL_ALT,
            fg=self.TEXT,
            highlightthickness=0,
            troughcolor=self.BORDER,
            activebackground=self.ACCENT_ALT,
            bd=0,
            sliderlength=18,
        )
        self.timeline_scale.grid(row=0, column=0, columnspan=5, sticky="ew", padx=10, pady=(10, 4))
        self.timeline_scale.bind("<ButtonPress-1>", self._on_timeline_press)
        self.timeline_scale.bind("<ButtonRelease-1>", self._on_timeline_release)

        playback_time = ttk.Label(playback_panel, textvariable=self.playback_time_var, style="Muted.TLabel")
        playback_time.grid(row=1, column=0, columnspan=5, sticky="e", padx=10, pady=(0, 8))
        prev_clip_button = ttk.Button(playback_panel, text="⏮", style="Ghost.TButton", command=self._select_previous_clip)
        prev_clip_button.grid(row=2, column=0, sticky="ew", padx=(10, 8), pady=(0, 10))
        replay_button = ttk.Button(playback_panel, text="↺", style="Ghost.TButton", command=self._replay_current_clip)
        replay_button.grid(row=2, column=1, sticky="ew", padx=(0, 8), pady=(0, 10))
        play_clip_button = ttk.Button(playback_panel, text="▶", style="Accent.TButton", command=self._play_current_clip)
        play_clip_button.grid(row=2, column=2, sticky="ew", padx=(0, 8), pady=(0, 10))
        stop_audio_button = ttk.Button(playback_panel, text="■", style="Ghost.TButton", command=self._stop_audio)
        stop_audio_button.grid(row=2, column=3, sticky="ew", padx=(0, 8), pady=(0, 10))
        next_clip_button = ttk.Button(playback_panel, text="⏭", style="Ghost.TButton", command=self._select_next_clip)
        next_clip_button.grid(row=2, column=4, sticky="ew", padx=(0, 10), pady=(0, 10))

        controls = ttk.Frame(detail, style="Panel.TFrame")
        controls.grid(row=5, column=0, sticky="ew", padx=14, pady=(0, 14))
        for index in range(5):
            controls.columnconfigure(index, weight=1)
        mark_shana_button = ttk.Button(controls, text="Mark Shana", style="Accent.TButton", command=lambda: self._set_label("Shana"))
        mark_shana_button.grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(10, 8))
        mark_shana_light_noise_button = ttk.Button(
            controls,
            text="Shana-light-noise",
            style="Ghost.TButton",
            command=lambda: self._set_label("Shana-light-noise"),
        )
        mark_shana_light_noise_button.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(10, 8))
        mark_shana_heavy_noise_button = ttk.Button(
            controls,
            text="Shana-heavy-noise",
            style="Ghost.TButton",
            command=lambda: self._set_label("Shana-heavy-noise"),
        )
        mark_shana_heavy_noise_button.grid(row=0, column=2, sticky="ew", padx=(0, 8), pady=(10, 8))
        mark_not_shana_button = ttk.Button(controls, text="Not Shana", style="Ghost.TButton", command=lambda: self._set_label("Not Shana"))
        mark_not_shana_button.grid(row=0, column=3, sticky="ew", padx=(0, 8), pady=(10, 8))
        reject_button = ttk.Button(controls, text="Reject", style="Ghost.TButton", command=lambda: self._set_label("Reject"))
        reject_button.grid(row=0, column=4, sticky="ew", pady=(10, 8))
        clear_label_button = ttk.Button(controls, text="Clear Label", style="Ghost.TButton", command=lambda: self._set_label(None))
        clear_label_button.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(0, 10))
        ttk.Label(controls, text="Bulk:", style="Muted.TLabel").grid(row=2, column=0, sticky="e", padx=(10, 8), pady=(0, 10))
        bulk_label_combo = ttk.Combobox(
            controls,
            textvariable=self.bulk_label_var,
            values=("Shana", "Shana-light-noise", "Shana-heavy-noise", "Not Shana", "Reject"),
            state="readonly",
        )
        bulk_label_combo.grid(row=2, column=1, sticky="ew", padx=(0, 8), pady=(0, 10))
        bulk_selected_button = ttk.Button(controls, text="Label Selected", style="Ghost.TButton", command=self._bulk_label_selected)
        bulk_selected_button.grid(row=2, column=2, sticky="ew", padx=(0, 8), pady=(0, 10))
        bulk_visible_button = ttk.Button(controls, text="Label Visible", style="Ghost.TButton", command=self._bulk_label_visible)
        bulk_visible_button.grid(row=2, column=3, sticky="ew", pady=(0, 10))

        export_panel = ttk.Frame(detail, style="Panel.TFrame")
        export_panel.grid(row=6, column=0, sticky="ew", padx=14, pady=(0, 14))
        export_panel.columnconfigure((0, 1), weight=1)
        export_button = ttk.Button(export_panel, text="Export Training Subsets", style="Accent.TButton", command=self._export_training_subsets)
        export_button.grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=10)
        ttk.Label(export_panel, textvariable=self.export_status_var, style="Muted.TLabel").grid(row=0, column=1, sticky="e", pady=10)

        trim_panel = ttk.Frame(detail, style="Panel.TFrame")
        trim_panel.grid(row=7, column=0, sticky="ew", padx=14, pady=(0, 14))
        for index in range(5):
            trim_panel.columnconfigure(index, weight=1 if index in {1, 3} else 0)
        trim_start_label = ttk.Label(trim_panel, text="Trim Start", style="Muted.TLabel")
        trim_start_label.grid(row=0, column=0, sticky="w", padx=(10, 8), pady=10)
        trim_start_entry = ttk.Entry(trim_panel, textvariable=self.trim_start_var)
        trim_start_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=10)
        trim_end_label = ttk.Label(trim_panel, text="Trim End", style="Muted.TLabel")
        trim_end_label.grid(row=0, column=2, sticky="w", padx=(0, 8), pady=10)
        trim_end_entry = ttk.Entry(trim_panel, textvariable=self.trim_end_var)
        trim_end_entry.grid(row=0, column=3, sticky="ew", padx=(0, 8), pady=10)
        create_trim_button = ttk.Button(trim_panel, text="Create Trimmed Clip", style="Ghost.TButton", command=self._create_trimmed_clip)
        create_trim_button.grid(row=0, column=4, sticky="ew", padx=(0, 10), pady=10)

        self._attach_tooltip(filter_label, "Show only clips with a specific review label so you can focus on one bucket at a time.")
        self._attach_tooltip(filter_combo, "Filter the review queue to All, Unlabeled, Shana, Shana-light-noise, Shana-heavy-noise, Not Shana, or Reject.")
        self._attach_tooltip(apply_filter_button, "Refresh the queue using the selected review filter.")
        self._attach_tooltip(rank_button, "Use confirmed Shana clips as seeds and sort the review queue by voice similarity. This is heuristic ranking, not true identification.")
        self._attach_tooltip(duplicate_button, "Scan the loaded clips for exact duplicate WAVs and near-duplicate acoustic matches.")
        self._attach_tooltip(auto_advance_check, "After labeling a clip, automatically move selection to the next visible clip in the review queue.")
        self._attach_tooltip(auto_play_check, "Automatically replay the selected clip when you navigate to it. Pairs well with Auto-Advance for hands-free audition.")
        self._attach_tooltip(self.review_tree, "List of extracted candidate clips. Select a row to inspect the transcript, metadata, duplicate hints, and playback. Rows are colored: white=normal quality, amber=borderline (no_speech>0.2 or logprob<-0.5), red=low quality (no_speech>0.4).")
        self._attach_tooltip(self.detail_text, "Transcript text for the selected clip. Use it as context, but trust the audio more than the text when labeling.")
        self._attach_tooltip(self.timeline_scale, "Drag or click the timeline to seek to a different point inside the selected clip. Playback restarts from the chosen position.")
        self._attach_tooltip(playback_time, "Current playback position and total duration for the selected clip.")
        self._attach_tooltip(prev_clip_button, "Move to the previous visible clip in the current review queue.")
        self._attach_tooltip(replay_button, "Jump back to the start of the selected clip and play it again.")
        self._attach_tooltip(play_clip_button, "Play the selected clip from the current timeline position.")
        self._attach_tooltip(stop_audio_button, "Stop playback without changing the current timeline position.")
        self._attach_tooltip(next_clip_button, "Move to the next visible clip in the current review queue.")
        self._attach_tooltip(mark_shana_button, "Label the selected clip as clean core Shana training audio.")
        self._attach_tooltip(mark_shana_light_noise_button, "Label the selected clip as Shana audio with minor contamination, such as tiny bleed, light ambience, or a trivial tail from another speaker.")
        self._attach_tooltip(mark_shana_heavy_noise_button, "Label the selected clip as Shana audio with substantial contamination, such as strong music, overlap, yelling, effects, or a large mixed-speaker section.")
        self._attach_tooltip(mark_not_shana_button, "Label the selected clip as valid speech from someone other than Shana.")
        self._attach_tooltip(reject_button, "Mark the selected clip as unusable for training or export, such as bad cuts, overlap, distortion, or junk audio.")
        self._attach_tooltip(clear_label_button, "Remove the saved label from the selected clip so it returns to the unlabeled queue.")
        self._attach_tooltip(export_button, "Copy labeled Shana, Shana-light-noise, and Shana-heavy-noise clips into separate export folders and write matching JSONL manifests.")
        self._attach_tooltip(trim_start_label, "Start time, in seconds relative to the selected clip, for creating a smaller derived clip.")
        self._attach_tooltip(trim_start_entry, "Start time, in seconds relative to the selected clip, for creating a smaller derived clip.")
        self._attach_tooltip(trim_end_label, "End time, in seconds relative to the selected clip. Leave blank to trim to the clip's end.")
        self._attach_tooltip(trim_end_entry, "End time, in seconds relative to the selected clip. Leave blank to trim to the clip's end.")
        self._attach_tooltip(create_trim_button, "Create a new derived WAV from the selected clip using the trim start and end values, then add it to the manifest for review.")
        self._attach_tooltip(self.waveform_canvas, "Waveform preview of the selected clip. The accent-colored cursor tracks playback position in real time.")
        self._attach_tooltip(bulk_label_combo, "Label to apply when using Label Selected or Label Visible.")
        self._attach_tooltip(bulk_selected_button, "Apply the bulk label to all highlighted clips in the queue (multi-select with Shift+click or Ctrl+click).")
        self._attach_tooltip(bulk_visible_button, "Apply the bulk label to every clip visible under the current filter. Confirm before applying to large views.")

    def _build_log_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.Frame(parent, style="Card.TFrame")
        panel.grid(row=1, column=1, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        parent.rowconfigure(1, weight=1)

        ttk.Label(panel, text="Live Log", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", padx=14, pady=(12, 6))
        self.log_text = tk.Text(
            panel,
            bg="#0f1113",
            fg=self.TEXT,
            insertbackground=self.TEXT,
            wrap="word",
            relief="flat",
            font=("Consolas", 10),
            highlightthickness=1,
            highlightbackground=self.BORDER,
            padx=10,
            pady=10,
        )
        log_scrollbar = ttk.Scrollbar(panel, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scrollbar.set)
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=(14, 0), pady=(0, 10))
        log_scrollbar.grid(row=1, column=1, sticky="ns", padx=(0, 14), pady=(0, 10))
        self.log_text.tag_configure("system", foreground=self.MUTED)
        self.log_text.tag_configure("success", foreground=self.SUCCESS)
        self.log_text.tag_configure("warn", foreground=self.WARN)

        controls = ttk.Frame(panel, style="Panel.TFrame")
        controls.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 14))
        controls.columnconfigure(0, weight=1)
        clear_log_button = ttk.Button(controls, text="Clear Log", style="Ghost.TButton", command=lambda: self.log_text.delete("1.0", "end"))
        clear_log_button.grid(row=0, column=0, sticky="ew", pady=10)

        self._attach_tooltip(self.log_text, "Live pipeline output from staging, transcription, extraction, playback, duplicate scans, and export operations.")
        self._attach_tooltip(clear_log_button, "Clear the visible log panel. This does not delete any files or saved labels.")

    def _labeled_entry(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        browse_command: Any,
    ) -> tuple[ttk.Label, ttk.Entry, ttk.Button]:
        label_widget = ttk.Label(parent, text=label, style="Muted.TLabel")
        label_widget.grid(row=row, column=0, sticky="w", padx=14, pady=6)
        entry_widget = ttk.Entry(parent, textvariable=variable)
        entry_widget.grid(row=row, column=1, sticky="ew", padx=8, pady=6)
        browse_widget = ttk.Button(parent, text="Browse", style="Ghost.TButton", command=browse_command)
        browse_widget.grid(row=row, column=2, sticky="ew", padx=(0, 14), pady=6)
        return label_widget, entry_widget, browse_widget

    def _attach_tooltip(self, widget: tk.Widget, text: str) -> None:
        self.tooltips.append(
            ToolTip(
                widget,
                text,
                background=self.PANEL_ALT,
                foreground=self.TEXT,
                border=self.BORDER,
            )
        )

    def _install_context_menus(self) -> None:
        self.root.bind_class("Entry", "<Button-3>", self._show_edit_context_menu, add="+")
        self.root.bind_class("TEntry", "<Button-3>", self._show_edit_context_menu, add="+")
        self.root.bind_class("Text", "<Button-3>", self._show_edit_context_menu, add="+")
        self.root.bind_class("TCombobox", "<Button-3>", self._show_edit_context_menu, add="+")

    def _bind_hotkeys(self) -> None:
        self.root.bind("<KeyPress-1>", lambda _event: self._set_label_with_options("Shana"))
        self.root.bind("<KeyPress-2>", lambda _event: self._set_label_with_options("Shana-light-noise"))
        self.root.bind("<KeyPress-3>", lambda _event: self._set_label_with_options("Shana-heavy-noise"))
        self.root.bind("<KeyPress-4>", lambda _event: self._set_label_with_options("Not Shana"))
        self.root.bind("<KeyPress-5>", lambda _event: self._set_label_with_options("Reject"))
        self.root.bind("<KeyPress-0>", lambda _event: self._set_label_with_options(None))
        self.root.bind("<Left>", lambda _event: self._select_previous_clip())
        self.root.bind("<Right>", lambda _event: self._select_next_clip())
        self.root.bind("<Control-Left>", lambda _event: self._select_previous_clip())
        self.root.bind("<Control-Right>", lambda _event: self._select_next_clip())
        self.root.bind("<KeyPress-p>", lambda _event: self._play_current_clip())
        self.root.bind("<KeyPress-r>", lambda _event: self._replay_current_clip())
        self.root.bind("<space>", lambda _event: self._toggle_playback())
        self.root.bind("<KeyPress-s>", lambda _event: self._stop_audio())
        self.root.bind("<KeyPress-bracketleft>", lambda _event: self._shift_noise_label(-1))
        self.root.bind("<KeyPress-bracketright>", lambda _event: self._shift_noise_label(1))

    def _show_edit_context_menu(self, event: tk.Event[tk.Widget]) -> str:
        widget = event.widget
        menu = tk.Menu(
            self.root,
            tearoff=0,
            bg=self.PANEL_ALT,
            fg=self.TEXT,
            activebackground=self.BORDER,
            activeforeground=self.TEXT,
        )
        menu.add_command(label="Cut", command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_command(label="Copy", command=lambda: widget.event_generate("<<Copy>>"))
        menu.add_command(label="Paste", command=lambda: widget.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Select All", command=lambda: self._select_all_widget_text(widget))
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _select_all_widget_text(self, widget: tk.Widget) -> None:
        try:
            widget.focus_set()
            if isinstance(widget, tk.Text):
                widget.tag_add("sel", "1.0", "end-1c")
                widget.mark_set("insert", "1.0")
            else:
                widget.selection_range(0, "end")  # type: ignore[attr-defined]
                widget.icursor("end")  # type: ignore[attr-defined]
        except Exception:
            return

    def _should_ignore_hotkey(self) -> bool:
        focus_widget = self.root.focus_get()
        if focus_widget is None:
            return False
        widget_class = focus_widget.winfo_class()
        return widget_class in {"Entry", "TEntry", "Text", "TCombobox", "Spinbox"}

    def _browse_source(self) -> None:
        path = filedialog.askdirectory(title="Choose source media folder")
        if path:
            self.source_var.set(path)

    def _browse_staging(self) -> None:
        path = filedialog.askdirectory(title="Choose local staging folder", initialdir=str(self.data_root))
        if path:
            self.staging_var.set(path)

    def _browse_dataset(self) -> None:
        path = filedialog.askdirectory(title="Choose dataset output folder", initialdir=str(self.data_root))
        if path:
            self.dataset_var.set(path)

    def _resolve_data_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (self.data_root / candidate).resolve()

    def _parse_limit(self) -> int:
        raw = self.limit_files_var.get().strip() or "0"
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError("Limit Files must be an integer.") from exc
        if value < 0:
            raise ValueError("Limit Files cannot be negative.")
        return value

    def _parse_max_seconds(self) -> float:
        raw = self.max_seconds_var.get().strip() or "12.0"
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError("Max Seconds must be a number.") from exc
        if value <= 0:
            raise ValueError("Max Seconds must be greater than zero.")
        return value

    def _exclude_patterns(self) -> list[str]:
        values = [part.strip() for part in self.exclude_var.get().split(",")]
        extras = [value for value in values if value]
        patterns: list[str] = []
        for value in DEFAULT_EXCLUDE_PATTERNS + tuple(extras):
            if value not in patterns:
                patterns.append(value)
        return patterns

    def _build_stage_args(self, prepare: bool) -> list[str]:
        source = self.source_var.get().strip()
        staging = self._resolve_data_path(self.staging_var.get().strip())
        dataset = self._resolve_data_path(self.dataset_var.get().strip())
        if not source or not staging or not dataset:
            raise ValueError("Source, staging, and dataset paths are required.")
        limit_files = self._parse_limit()
        max_seconds = self._parse_max_seconds()
        args = [source, "--staging-dir", str(staging), "--dataset-out-dir", str(dataset)]
        if self.overwrite_var.get():
            args.append("--overwrite")
        if limit_files > 0:
            args.extend(["--limit-files", str(limit_files)])
        for pattern in self._exclude_patterns():
            args.extend(["--exclude-pattern", pattern])
        if prepare:
            args.append("--prepare")
            args.append("--prepare-args")
            args.extend(["--language", self.language_var.get().strip()])
            if self.model_var.get().strip():
                args.extend(["--model", self.model_var.get().strip()])
            args.extend(["--max-seconds", str(max_seconds)])
            if self.stream_lang_var.get().strip():
                args.extend(["--audio-stream-lang", self.stream_lang_var.get().strip()])
            for pattern in self._exclude_patterns():
                args.extend(["--exclude-pattern", pattern])
            if limit_files > 0:
                args.extend(["--limit-files", str(limit_files)])
            if self.vocals_only_var.get():
                args.append("--vocals-only")
        return args

    def _run_stage_only(self) -> None:
        self._start_worker(self._build_stage_args(prepare=False), "Staging media locally.")

    def _run_stage_and_prepare(self) -> None:
        self._start_worker(self._build_stage_args(prepare=True), "Staging media and extracting candidate speech clips.")

    def _start_worker(self, args: list[str], status_message: str) -> None:
        self._launch_worker(stage_and_prepare_main, args, status_message)

    def _launch_worker(self, main_fn: object, args: list[str], status_message: str) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("Busy", "A pipeline job is already running.")
            return
        self.pipeline_cancel_event = threading.Event()
        set_stage_cancel_event(self.pipeline_cancel_event)
        set_prepare_cancel_event(self.pipeline_cancel_event)
        self.status_var.set(status_message)
        self._append_log(f"[system] starting job: {' '.join(args)}", "system")
        self.worker = threading.Thread(target=self._worker_main, args=(main_fn, args), daemon=True)
        self.worker.start()

    def _request_pipeline_stop(self) -> None:
        if not self.worker or not self.worker.is_alive() or self.pipeline_cancel_event is None:
            messagebox.showinfo("No Active Job", "There is no active staging or preparation job to stop.")
            return
        self.pipeline_cancel_event.set()
        self.status_var.set("Stop requested. The current pipeline job will halt at the next safe checkpoint.")
        self._append_log("[system] pipeline stop requested", "warn")

    def _worker_main(self, main_fn: object, args: list[str]) -> None:
        writer = QueueWriter(self.event_queue)
        exit_code = 0
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                exit_code = main_fn(args)  # type: ignore[operator]
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            exit_code = code
            if exc.code is not None and not isinstance(exc.code, int):
                writer.write(f"Pipeline error: {exc.code}\n")
            writer.write(f"Pipeline exited with code {code}\n")
        except Exception as exc:  # noqa: BLE001
            exit_code = 1
            writer.write(f"Pipeline failed: {exc}\n")
        finally:
            set_stage_cancel_event(None)
            set_prepare_cancel_event(None)
            writer.flush()
            self.event_queue.put(("done", exit_code))

    def _poll_queue(self) -> None:
        try:
            while True:
                event, payload = self.event_queue.get_nowait()
                if event == "log":
                    message = str(payload)
                    tag = "system" if message.startswith("[system]") else None
                    if "candidate speech" in message:
                        tag = "success"
                    elif "failed" in message.lower() or "error" in message.lower():
                        tag = "warn"
                    self._append_log(message, tag)
                elif event == "done":
                    exit_code = int(payload)
                    if exit_code == 0:
                        self.status_var.set("Pipeline finished. Load the manifest and tag which candidate clips are actually Shana.")
                        self._append_log("[system] job finished successfully", "system")
                        self._load_manifest_from_dataset(silent=True)
                    elif exit_code == 130:
                        self.status_var.set("Pipeline stopped. Partial staged files or extracted clips may exist.")
                        self._append_log("[system] job cancelled by user", "warn")
                    else:
                        self.status_var.set(f"Pipeline failed with exit code {exit_code}. Review the log.")
                        self._append_log(f"[system] job failed with exit code {exit_code}", "warn")
                    self.pipeline_cancel_event = None
                elif event == "transcribe_result":
                    transcript = str(payload)
                    if self.transcribe_text is not None:
                        self.transcribe_text.delete("1.0", "end")
                        self.transcribe_text.insert("1.0", transcript)
                    self.transcribe_status_var.set(f"Done. {len(transcript.splitlines())} lines transcribed.")
                    self._append_log("[system] transcription complete", "success")
                elif event == "transcribe_error":
                    self.transcribe_status_var.set(f"Transcription failed: {payload}")
                    self._append_log(f"[system] transcription failed: {payload}", "warn")
                elif event == "playback_done":
                    if self.current_record is not None and self.playback_target_clip_id == self.current_record.clip_id:
                        self.playback_position_var.set(self.current_record.duration_seconds)
                        self._update_playback_label(self.current_record.duration_seconds, self.current_record.duration_seconds)
                    self.status_var.set(f"Playback finished: {payload}")
                    self._append_log(f"[system] playback finished: {payload}", "system")
                elif event == "playback_error":
                    self.status_var.set("Audio playback failed. Review the log.")
                    self._append_log(f"[system] playback failed: {payload}", "warn")
        except queue.Empty:
            pass
        self._refresh_playback_position()
        self.root.after(100, self._poll_queue)

    def _append_log(self, message: str, tag: str | None = None) -> None:
        self.log_text.insert("end", message + "\n", tag or "")
        self.log_text.see("end")

    def _manifest_path(self) -> Path:
        return self._resolve_data_path(self.dataset_var.get().strip()) / "manifest.jsonl"

    def _labels_path(self) -> Path:
        return self._resolve_data_path(self.dataset_var.get().strip()) / "labels.json"

    def _load_manifest_from_dataset(self, silent: bool = False) -> None:
        manifest_path = self._manifest_path()
        if not manifest_path.exists():
            if not silent:
                messagebox.showwarning("Missing Manifest", f"Manifest not found:\n{manifest_path}")
            return
        self._stop_audio(log_message=False)
        self.current_manifest_path = manifest_path
        self.records = self._read_manifest(manifest_path)
        self.labels = self._read_labels(self._labels_path())
        self.signature_cache = {}
        self.similarity_scores = {}
        self.audio_hash_cache = {}
        self.exact_duplicates = {}
        self.near_duplicates = {}
        self._waveform_sample_cache = {}
        self._refresh_review_tree()
        self.review_status_var.set(f"{len(self.records)} clips loaded")
        self.status_var.set("Manifest loaded. Review candidate speech clips and mark which ones are Shana.")
        self._append_log(f"[system] loaded manifest: {manifest_path}", "system")

    def _read_manifest(self, manifest_path: Path) -> list[ReviewRecord]:
        records: list[ReviewRecord] = []
        with manifest_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                payload = json.loads(line)
                records.append(
                    ReviewRecord(
                        clip_id=str(payload["clip_id"]),
                        source_path=str(payload["source_path"]),
                        clip_path=str(payload["clip_path"]),
                        text=str(payload["text"]),
                        episode_id=str(payload["episode_id"]),
                        duration_seconds=float(payload["duration_seconds"]),
                        start_seconds=float(payload["start_seconds"]),
                        end_seconds=float(payload["end_seconds"]),
                        language=str(payload["language"]),
                        avg_logprob=self._optional_float(payload.get("avg_logprob")),
                        no_speech_prob=self._optional_float(payload.get("no_speech_prob")),
                        compression_ratio=self._optional_float(payload.get("compression_ratio")),
                    )
                )
        return records

    def _read_labels(self, labels_path: Path) -> dict[str, dict[str, Any]]:
        if not labels_path.exists():
            return {}
        with labels_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return {}
        labels = {str(key): value for key, value in payload.items() if isinstance(value, dict)}
        migrated = 0
        for value in labels.values():
            if value.get("label") == "Shana-noisy":
                value["label"] = "Shana-heavy-noise"
                migrated += 1
        if migrated:
            self._append_log(
                f"[system] migrated {migrated} legacy 'Shana-noisy' labels to 'Shana-heavy-noise'",
                "warn",
            )
            self._write_labels(labels_path, labels)
        return labels

    def _refresh_review_tree(self) -> None:
        selected_filter = self.review_filter_var.get()
        for item in self.review_tree.get_children():
            self.review_tree.delete(item)

        visible = 0
        counts = {
            "Shana": 0,
            "Shana-light-noise": 0,
            "Shana-heavy-noise": 0,
            "Not Shana": 0,
            "Reject": 0,
            "Unlabeled": 0,
        }
        ordered_records = sorted(
            self.records,
            key=lambda record: self.similarity_scores.get(record.clip_id, float("-inf")),
            reverse=True,
        )
        for record in ordered_records:
            label = self.labels.get(record.clip_id, {}).get("label")
            if label in counts:
                counts[str(label)] += 1
            else:
                counts["Unlabeled"] += 1

            if selected_filter == "Unlabeled" and label:
                continue
            if selected_filter in {"Shana", "Shana-light-noise", "Shana-heavy-noise", "Not Shana", "Reject"} and label != selected_filter:
                continue

            visible += 1
            score = self.similarity_scores.get(record.clip_id)
            duplicate_text = self._duplicate_status_text(record.clip_id)
            no_speech = record.no_speech_prob
            logprob = record.avg_logprob
            if no_speech is not None and no_speech > 0.4:
                quality_tag = "quality_bad"
            elif (no_speech is not None and no_speech > 0.2) or (logprob is not None and logprob < -0.5):
                quality_tag = "quality_warn"
            else:
                quality_tag = "quality_ok"
            self.review_tree.insert(
                "",
                "end",
                iid=record.clip_id,
                values=(
                    record.episode_id,
                    f"{record.duration_seconds:.2f}",
                    f"{score:.3f}" if score is not None else "",
                    duplicate_text,
                    label or "",
                ),
                text=record.clip_id,
                tags=(quality_tag,),
            )

        self.review_status_var.set(
            "visible="
            f"{visible} shana={counts['Shana']} light={counts['Shana-light-noise']} heavy={counts['Shana-heavy-noise']} "
            f"not={counts['Not Shana']} reject={counts['Reject']} unlabeled={counts['Unlabeled']}"
        )

    def _on_review_select(self, _event: object) -> None:
        selection = self.review_tree.selection()
        if not selection:
            return
        clip_id = selection[0]
        record = next((record for record in self.records if record.clip_id == clip_id), None)
        if record is None:
            return
        clip_changed = self.current_record is None or self.current_record.clip_id != record.clip_id
        if clip_changed:
            self._stop_audio(log_message=False)
        self.current_record = record
        self.trim_start_var.set("0.0")
        self.trim_end_var.set(f"{record.duration_seconds:.2f}")
        self.playback_position_var.set(0.0)
        self._set_timeline_max(record.duration_seconds)
        self._update_playback_label(0.0, record.duration_seconds)
        self.playback_target_clip_id = record.clip_id
        label = self.labels.get(record.clip_id, {}).get("label", "Unlabeled")
        meta = (
            f"Clip: {record.clip_id}\n"
            f"Episode: {record.episode_id}\n"
            f"Time: {record.start_seconds:.2f}s -> {record.end_seconds:.2f}s ({record.duration_seconds:.2f}s)\n"
            f"Language: {record.language}\n"
            f"Current Label: {label}\n"
            f"Similarity Score: {self.similarity_scores.get(record.clip_id, 'n/a')}\n"
            f"Duplicate Status: {self._duplicate_status_text(record.clip_id)}\n"
            f"No-speech Prob: {record.no_speech_prob if record.no_speech_prob is not None else 'n/a'}\n"
            f"Avg LogProb: {record.avg_logprob if record.avg_logprob is not None else 'n/a'}\n"
            f"Source: {record.source_path}\n"
            f"Clip: {record.clip_path}"
        )
        self.detail_meta.configure(text=meta)
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", record.text)
        if clip_changed:
            clip_path = Path(record.clip_path)
            if clip_path.exists():
                self._draw_waveform(clip_path)
            else:
                self._clear_waveform()
            if self.auto_play_var.get():
                self.root.after(50, self._replay_current_clip)

    def _set_timeline_max(self, duration_seconds: float) -> None:
        if self.timeline_scale is not None:
            self.timeline_scale.configure(to=max(duration_seconds, 0.01))

    def _format_seconds(self, seconds: float) -> str:
        total = max(seconds, 0.0)
        minutes = int(total // 60)
        remainder = total - (minutes * 60)
        return f"{minutes:02d}:{remainder:05.2f}"

    def _update_playback_label(self, position_seconds: float, total_seconds: float) -> None:
        self.playback_time_var.set(
            f"{self._format_seconds(position_seconds)} / {self._format_seconds(total_seconds)}"
        )

    def _refresh_playback_position(self) -> None:
        if self.current_record is None:
            self._update_playback_label(0.0, 0.0)
            return
        process = self.playback_process
        if (
            process is not None
            and process.poll() is None
            and self.playback_target_clip_id == self.current_record.clip_id
            and not self.timeline_dragging
        ):
            elapsed = time.monotonic() - self.playback_started_at
            position = min(self.playback_start_offset + elapsed, self.current_record.duration_seconds)
            self.playback_position_var.set(position)
            self._update_playback_label(position, self.current_record.duration_seconds)
            self._update_waveform_cursor(position, self.current_record.duration_seconds)
            return
        if not self.timeline_dragging:
            position = min(max(self.playback_position_var.get(), 0.0), self.current_record.duration_seconds)
            self._update_playback_label(position, self.current_record.duration_seconds)
            self._update_waveform_cursor(position, self.current_record.duration_seconds)

    def _on_timeline_change(self, value: str) -> None:
        if self.current_record is None:
            self._update_playback_label(0.0, 0.0)
            return
        try:
            position = float(value)
        except ValueError:
            position = 0.0
        position = min(max(position, 0.0), self.current_record.duration_seconds)
        self._update_playback_label(position, self.current_record.duration_seconds)

    def _on_timeline_press(self, _event: tk.Event[tk.Widget]) -> None:
        self.timeline_dragging = True

    def _on_timeline_release(self, _event: tk.Event[tk.Widget]) -> None:
        self.timeline_dragging = False
        if self.current_record is None:
            return
        position = min(max(self.playback_position_var.get(), 0.0), self.current_record.duration_seconds)
        self.playback_position_var.set(position)
        self._update_playback_label(position, self.current_record.duration_seconds)
        if self.playback_process is not None and self.playback_process.poll() is None:
            self._play_from_position(position)

    def _visible_clip_ids(self) -> list[str]:
        return list(self.review_tree.get_children())

    def _select_adjacent_clip(self, step: int) -> None:
        visible = self._visible_clip_ids()
        if not visible:
            return
        selection = self.review_tree.selection()
        current_id = selection[0] if selection else None
        if current_id in visible:
            next_index = max(0, min(visible.index(current_id) + step, len(visible) - 1))
        else:
            next_index = 0 if step >= 0 else len(visible) - 1
        target = visible[next_index]
        self.review_tree.selection_set(target)
        self.review_tree.focus(target)
        self.review_tree.see(target)
        self._on_review_select(None)

    def _select_previous_clip(self) -> None:
        self._select_adjacent_clip(-1)

    def _select_next_clip(self) -> None:
        self._select_adjacent_clip(1)

    def _set_label(self, label: str | None) -> None:
        if self.current_record is None:
            messagebox.showwarning("No Selection", "Select a clip first.")
            return
        if label is None:
            self.labels.pop(self.current_record.clip_id, None)
            action = "cleared"
        else:
            self.labels[self.current_record.clip_id] = {
                "label": label,
                "clip_path": self.current_record.clip_path,
                "source_path": self.current_record.source_path,
            }
            action = f"marked {label}"
        self._write_labels(self._labels_path(), self.labels)
        self._append_log(f"[system] {self.current_record.clip_id} {action}", "system")
        if label == "Shana" or label is None:
            self.similarity_scores = {}
        self._refresh_review_tree()
        self._on_review_select(None)
        if self.auto_advance_var.get() and label is not None:
            self._select_next_clip()

    def _set_label_with_options(self, label: str | None) -> str | None:
        if self._should_ignore_hotkey():
            return None
        self._set_label(label)
        return "break"

    def _toggle_playback(self) -> str | None:
        if self._should_ignore_hotkey():
            return None
        process = self.playback_process
        if process is not None and process.poll() is None:
            self._stop_audio()
        else:
            self._play_current_clip()
        return "break"

    def _shift_noise_label(self, step: int) -> str | None:
        if self._should_ignore_hotkey():
            return None
        if self.current_record is None:
            return "break"
        current_label = self.labels.get(self.current_record.clip_id, {}).get("label")
        order = ["Shana", "Shana-light-noise", "Shana-heavy-noise"]
        if current_label not in order:
            return "break"
        current_index = order.index(str(current_label))
        next_index = max(0, min(current_index + step, len(order) - 1))
        if next_index != current_index:
            self._set_label(order[next_index])
        return "break"

    def _signature_for_clip_id(self, clip_id: str) -> np.ndarray | None:
        """Compute a signature for a clip_id whose WAV may exist outside the current manifest."""
        cached = self.signature_cache.get(clip_id)
        if cached is not None:
            return cached
        # Reconstruct path: clips/{episode_id}/{clip_id}.wav
        # episode_id is clip_id with the trailing _NNNN index stripped.
        episode_id = clip_id[:-5] if len(clip_id) > 5 and clip_id[-5] == "_" and clip_id[-4:].isdigit() else clip_id
        dataset_dir = self._resolve_data_path(self.dataset_var.get().strip())
        clip_path = dataset_dir / "clips" / episode_id / f"{clip_id}.wav"
        if not clip_path.exists():
            return None
        try:
            signature = self._compute_clip_signature(clip_path)
            self.signature_cache[clip_id] = signature
            return signature
        except Exception:  # noqa: BLE001
            return None

    def _orphan_seed_vectors(self) -> tuple[list[np.ndarray], list[np.ndarray]]:
        """Return signature vectors for labeled clips that exist on disk but are not in the current manifest."""
        manifest_ids = {record.clip_id for record in self.records}
        shana_vecs: list[np.ndarray] = []
        not_shana_vecs: list[np.ndarray] = []
        for clip_id, entry in self.labels.items():
            if clip_id in manifest_ids:
                continue
            label = entry.get("label", "")
            if label not in ("Shana", "Shana-light-noise", "Shana-heavy-noise", "Not Shana"):
                continue
            sig = self._signature_for_clip_id(clip_id)
            if sig is None:
                continue
            if label.startswith("Shana"):
                shana_vecs.append(sig)
            else:
                not_shana_vecs.append(sig)
        return shana_vecs, not_shana_vecs

    def _seed_archive_path(self) -> Path:
        dataset_dir = self._resolve_data_path(self.dataset_var.get().strip())
        return dataset_dir / "shana_seed_archive.json"

    def _load_seed_archive(self) -> tuple[list[np.ndarray], list[np.ndarray]]:
        path = self._seed_archive_path()
        if not path.exists():
            return [], []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            shana = [np.array(v, dtype=np.float32) for v in data.get("shana", [])]
            not_shana = [np.array(v, dtype=np.float32) for v in data.get("not_shana", [])]
            return shana, not_shana
        except Exception:  # noqa: BLE001
            return [], []

    def _save_seed_archive(self, shana_vecs: list[np.ndarray], not_shana_vecs: list[np.ndarray]) -> None:
        path = self._seed_archive_path()
        try:
            data = {
                "shana": [v.tolist() for v in shana_vecs],
                "not_shana": [v.tolist() for v in not_shana_vecs],
            }
            path.write_text(json.dumps(data), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def _rank_similarity(self) -> None:
        shana_records = [record for record in self.records if self.labels.get(record.clip_id, {}).get("label", "").startswith("Shana")]
        archive_shana, archive_not_shana = self._load_seed_archive()
        orphan_shana, orphan_not_shana = self._orphan_seed_vectors()

        if not shana_records and not archive_shana and not orphan_shana:
            messagebox.showwarning("No Seeds", "Mark a few confirmed Shana clips first, then run ranking.")
            return

        seed_vectors: list[np.ndarray] = list(archive_shana) + orphan_shana
        failures = 0
        for record in shana_records:
            signature = self._signature_for_record(record)
            if signature is None:
                failures += 1
                continue
            seed_vectors.append(signature)
        if not seed_vectors:
            messagebox.showwarning("No Signatures", "Failed to compute signatures for the current Shana-labeled clips.")
            return

        shana_centroid = np.mean(np.stack(seed_vectors, axis=0), axis=0)
        shana_centroid_norm = np.linalg.norm(shana_centroid)
        if shana_centroid_norm == 0:
            messagebox.showwarning("Invalid Seeds", "The current Shana seed clips produced an empty similarity centroid.")
            return

        # Build anti-centroid from "Not Shana" clips plus archive.
        # Reject clips are excluded — they may be noise/overlap rather than a real speaker voice.
        not_shana_records = [record for record in self.records if self.labels.get(record.clip_id, {}).get("label") == "Not Shana"]
        anti_vectors: list[np.ndarray] = list(archive_not_shana) + orphan_not_shana
        for record in not_shana_records:
            signature = self._signature_for_record(record)
            if signature is not None:
                anti_vectors.append(signature)
        anti_centroid: np.ndarray | None = None
        anti_centroid_norm = 0.0
        if anti_vectors:
            anti_centroid = np.mean(np.stack(anti_vectors, axis=0), axis=0)
            anti_centroid_norm = float(np.linalg.norm(anti_centroid))
            if anti_centroid_norm == 0:
                anti_centroid = None

        scored = 0
        scores: dict[str, float] = {}
        for record in self.records:
            signature = self._signature_for_record(record)
            if signature is None:
                continue
            sig_norm = float(np.linalg.norm(signature))
            if sig_norm == 0:
                continue
            shana_sim = float(np.dot(signature, shana_centroid) / (sig_norm * shana_centroid_norm))
            if anti_centroid is not None:
                not_shana_sim = float(np.dot(signature, anti_centroid) / (sig_norm * anti_centroid_norm))
                score = shana_sim - not_shana_sim
            else:
                score = shana_sim
            scores[record.clip_id] = score
            scored += 1

        self.similarity_scores = scores
        self._refresh_review_tree()

        # Persist signatures so future runs can use them as seeds even after re-extraction.
        self._save_seed_archive(seed_vectors, anti_vectors)

        extra_notes: list[str] = []
        if archive_shana or archive_not_shana:
            extra_notes.append(f"{len(archive_shana)}+{len(archive_not_shana)} archived")
        if orphan_shana or orphan_not_shana:
            extra_notes.append(f"{len(orphan_shana)}+{len(orphan_not_shana)} from prior runs on disk")
        extra = f" ({', '.join(extra_notes)})" if extra_notes else ""
        total_anti = len(not_shana_records) + len(archive_not_shana) + len(orphan_not_shana)
        mode = (
            f"{len(seed_vectors)} Shana seeds + {total_anti} Not-Shana anti-seeds{extra}"
            if total_anti
            else f"{len(seed_vectors)} Shana seeds only{extra}"
        )
        self.status_var.set(
            f"Ranking updated ({mode}). Higher scores are more likely Shana. This is heuristic triage, not true speaker identification."
        )
        self._append_log(
            f"[system] ranked {scored} clips using {mode}; signature failures={failures}",
            "system",
        )
        self._on_review_select(None)

    def _detect_duplicates(self) -> None:
        self.exact_duplicates = {}
        self.near_duplicates = {}
        hash_to_primary: dict[str, str] = {}
        signatures: list[tuple[ReviewRecord, np.ndarray]] = []

        for record in self.records:
            clip_path = Path(record.clip_path)
            if not clip_path.exists():
                continue
            audio_hash = self._audio_hash_for_record(record)
            if audio_hash is not None:
                primary = hash_to_primary.get(audio_hash)
                if primary is None:
                    hash_to_primary[audio_hash] = record.clip_id
                else:
                    self.exact_duplicates[record.clip_id] = primary

            signature = self._signature_for_record(record)
            if signature is not None:
                signatures.append((record, signature))

        for index, (record, signature) in enumerate(signatures):
            if record.clip_id in self.exact_duplicates:
                continue
            best_match: tuple[str, float] | None = None
            for other_record, other_signature in signatures[:index]:
                if other_record.clip_id in self.exact_duplicates:
                    continue
                score = float(np.dot(signature, other_signature) / max(np.linalg.norm(signature) * np.linalg.norm(other_signature), 1e-9))
                if score >= 0.995 and abs(record.duration_seconds - other_record.duration_seconds) <= 0.35:
                    if best_match is None or score > best_match[1]:
                        best_match = (other_record.clip_id, score)
            if best_match is not None:
                self.near_duplicates[record.clip_id] = best_match

        self._refresh_review_tree()
        self.status_var.set("Duplicate scan finished. Exact and near-duplicate hints are now shown in the review queue.")
        self._append_log(
            f"[system] duplicate scan finished exact={len(self.exact_duplicates)} near={len(self.near_duplicates)}",
            "system",
        )
        self._on_review_select(None)

    def _audio_hash_for_record(self, record: ReviewRecord) -> str | None:
        cached = self.audio_hash_cache.get(record.clip_id)
        if cached is not None:
            return cached
        clip_path = Path(record.clip_path)
        if not clip_path.exists():
            return None
        digest = hashlib.sha256(clip_path.read_bytes()).hexdigest()
        self.audio_hash_cache[record.clip_id] = digest
        return digest

    def _duplicate_status_text(self, clip_id: str) -> str:
        exact = self.exact_duplicates.get(clip_id)
        if exact is not None:
            return f"exact:{exact[-4:]}"
        near = self.near_duplicates.get(clip_id)
        if near is not None:
            return f"near:{near[0][-4:]}"
        return ""

    def _signature_for_record(self, record: ReviewRecord) -> np.ndarray | None:
        cached = self.signature_cache.get(record.clip_id)
        if cached is not None:
            return cached
        clip_path = Path(record.clip_path)
        if not clip_path.exists():
            self._append_log(f"[system] missing clip for signature: {clip_path}", "warn")
            return None
        try:
            signature = self._compute_clip_signature(clip_path)
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"[system] signature failed for {record.clip_id}: {exc}", "warn")
            return None
        self.signature_cache[record.clip_id] = signature
        return signature

    def _compute_clip_signature(self, clip_path: Path) -> np.ndarray:
        with wave.open(str(clip_path), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            frame_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            audio_bytes = wav_file.readframes(frame_count)

        if sample_width != 2:
            raise ValueError(f"expected 16-bit PCM wav, got sample width {sample_width}")
        waveform = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
        if channels > 1:
            waveform = waveform.reshape(-1, channels).mean(axis=1)
        if waveform.size == 0:
            raise ValueError("empty waveform")

        waveform /= max(np.max(np.abs(waveform)), 1.0)
        frame_size = min(1024, waveform.size)
        hop_size = max(frame_size // 2, 1)
        if waveform.size < frame_size:
            waveform = np.pad(waveform, (0, frame_size - waveform.size))

        frames = []
        for start in range(0, waveform.size - frame_size + 1, hop_size):
            frame = waveform[start : start + frame_size]
            frames.append(frame * np.hanning(frame_size))
        frame_matrix = np.stack(frames, axis=0)

        spectra = np.abs(np.fft.rfft(frame_matrix, axis=1))
        mean_spectrum = np.mean(spectra, axis=0)
        if np.max(mean_spectrum) > 0:
            mean_spectrum /= np.max(mean_spectrum)

        band_edges = np.linspace(0, mean_spectrum.size, num=9, dtype=int)
        band_energies = []
        for left, right in zip(band_edges[:-1], band_edges[1:]):
            segment = mean_spectrum[left:right]
            band_energies.append(float(np.mean(segment)) if segment.size else 0.0)

        zero_crossings = float(np.mean(np.abs(np.diff(np.signbit(waveform[: min(waveform.size, frame_rate)])))))
        rms = float(np.sqrt(np.mean(np.square(waveform))))
        duration = waveform.size / max(frame_rate, 1)
        duration_feature = min(duration / 8.0, 1.0)

        freqs = np.fft.rfftfreq(frame_size, d=1.0 / max(frame_rate, 1))
        spectral_sum = np.sum(mean_spectrum)
        spectral_centroid = float(np.sum(freqs * mean_spectrum) / spectral_sum) if spectral_sum > 0 else 0.0
        centroid_feature = spectral_centroid / (frame_rate / 2.0 if frame_rate else 1.0)

        signature = np.array(band_energies + [zero_crossings, rms, duration_feature, centroid_feature], dtype=np.float32)
        norm = float(np.linalg.norm(signature))
        if norm > 0:
            signature /= norm
        return signature

    def _play_current_clip(self) -> None:
        if self.current_record is None:
            messagebox.showwarning("No Selection", "Select a clip first.")
            return
        self._play_from_position(self.playback_position_var.get())

    def _replay_current_clip(self) -> None:
        if self.current_record is None:
            messagebox.showwarning("No Selection", "Select a clip first.")
            return
        self.playback_position_var.set(0.0)
        self._update_playback_label(0.0, self.current_record.duration_seconds)
        self._play_from_position(0.0)

    def _play_from_position(self, offset_seconds: float) -> None:
        if self.current_record is None:
            return
        clip_path = Path(self.current_record.clip_path)
        if not clip_path.exists():
            messagebox.showwarning("Missing Clip", f"Clip file not found:\n{clip_path}")
            return
        offset = min(max(float(offset_seconds), 0.0), self.current_record.duration_seconds)
        self._stop_audio(log_message=False)
        self.playback_position_var.set(offset)
        self.playback_start_offset = offset
        self.playback_started_at = time.monotonic()
        self.playback_target_clip_id = self.current_record.clip_id
        self._update_playback_label(offset, self.current_record.duration_seconds)
        self.status_var.set(f"Playing {self.current_record.clip_id} from {offset:.2f}s.")
        self._append_log(f"[system] playing clip {self.current_record.clip_id} from {offset:.2f}s", "system")
        self.playback_thread = threading.Thread(target=self._play_clip_worker, args=(clip_path, offset), daemon=True)
        self.playback_thread.start()

    def _create_trimmed_clip(self) -> None:
        if self.current_record is None:
            messagebox.showwarning("No Selection", "Select a clip first.")
            return

        try:
            trim_start = float((self.trim_start_var.get().strip() or "0").strip())
            trim_end_raw = self.trim_end_var.get().strip()
            trim_end = float(trim_end_raw) if trim_end_raw else self.current_record.duration_seconds
        except ValueError:
            messagebox.showwarning("Invalid Trim", "Trim Start and Trim End must be numbers.")
            return

        if trim_start < 0 or trim_end <= trim_start or trim_end > self.current_record.duration_seconds + 1e-6:
            messagebox.showwarning(
                "Invalid Trim",
                f"Use 0 <= start < end <= {self.current_record.duration_seconds:.2f} seconds.",
            )
            return

        source_clip = Path(self.current_record.clip_path)
        if not source_clip.exists():
            messagebox.showwarning("Missing Clip", f"Clip file not found:\n{source_clip}")
            return

        dataset_root = self._resolve_data_path(self.dataset_var.get().strip())
        derived_dir = dataset_root / "clips" / "derived"
        derived_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"trim_{int(round(trim_start * 1000)):06d}_{int(round(trim_end * 1000)):06d}"
        new_clip_id = f"{self.current_record.clip_id}_{suffix}"
        target_clip = derived_dir / f"{new_clip_id}.wav"

        command = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{trim_start:.3f}",
            "-to",
            f"{trim_end:.3f}",
            "-i",
            str(source_clip),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-sample_fmt",
            "s16",
            str(target_clip),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            messagebox.showwarning(
                "Trim Failed",
                completed.stderr.strip() or completed.stdout.strip() or "ffmpeg trim failed.",
            )
            return

        derived_record = ReviewRecord(
            clip_id=new_clip_id,
            source_path=self.current_record.source_path,
            clip_path=str(target_clip),
            text=f"[trim {trim_start:.2f}-{trim_end:.2f}] {self.current_record.text}",
            episode_id=f"{self.current_record.episode_id}_derived",
            duration_seconds=round(trim_end - trim_start, 3),
            start_seconds=round(self.current_record.start_seconds + trim_start, 3),
            end_seconds=round(self.current_record.start_seconds + trim_end, 3),
            language=self.current_record.language,
            avg_logprob=self.current_record.avg_logprob,
            no_speech_prob=self.current_record.no_speech_prob,
            compression_ratio=self.current_record.compression_ratio,
        )
        self._append_record_to_manifest(derived_record)
        self.records.append(derived_record)
        self.audio_hash_cache.pop(derived_record.clip_id, None)
        self.signature_cache.pop(derived_record.clip_id, None)
        self.similarity_scores.pop(derived_record.clip_id, None)
        self.exact_duplicates.pop(derived_record.clip_id, None)
        self.near_duplicates.pop(derived_record.clip_id, None)
        self._refresh_review_tree()
        self.review_tree.selection_set(derived_record.clip_id)
        self.review_tree.focus(derived_record.clip_id)
        self._on_review_select(None)
        self.status_var.set("Trimmed clip created. Review and label the derived clip instead of discarding the mixed original.")
        self._append_log(f"[system] created trimmed clip {derived_record.clip_id}", "system")

    def _play_clip_worker(self, clip_path: Path, offset_seconds: float) -> None:
        try:
            command = [
                "ffplay",
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "error",
                "-ss",
                f"{offset_seconds:.3f}",
                str(clip_path),
            ]
            self.playback_process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            _stdout, stderr = self.playback_process.communicate()
            return_code = self.playback_process.returncode
            self.playback_process = None
            if return_code == 0:
                self.event_queue.put(("playback_done", clip_path.name))
            else:
                self.event_queue.put(("playback_error", stderr.strip() or f"ffplay exited with code {return_code}"))
        except Exception as exc:  # noqa: BLE001
            self.event_queue.put(("playback_error", str(exc)))

    def _stop_audio(self, log_message: bool = True) -> None:
        process = self.playback_process
        if process is not None and process.poll() is None:
            process.terminate()
            self.playback_process = None
        self.playback_started_at = 0.0
        if log_message:
            self.status_var.set("Audio stopped.")
            self._append_log("[system] audio stopped", "system")

    def _run_prepare_only(self) -> None:
        try:
            staging = self._resolve_data_path(self.staging_var.get().strip())
            dataset = self._resolve_data_path(self.dataset_var.get().strip())
            limit_files = self._parse_limit()
            max_seconds = self._parse_max_seconds()
        except ValueError as exc:
            messagebox.showwarning("Invalid Input", str(exc))
            return
        if not staging.exists():
            messagebox.showwarning("Missing Staging", f"Staging directory not found:\n{staging}")
            return
        args = [
            str(staging),
            "--out-dir",
            str(dataset),
            "--language",
            self.language_var.get().strip(),
            "--max-seconds",
            str(max_seconds),
        ]
        model = self.model_var.get().strip()
        if model:
            args += ["--model", model]
        stream_lang = self.stream_lang_var.get().strip()
        if stream_lang:
            args += ["--audio-stream-lang", stream_lang]
        for pattern in self._exclude_patterns():
            args += ["--exclude-pattern", pattern]
        if limit_files > 0:
            args += ["--limit-files", str(limit_files)]
        if self.vocals_only_var.get():
            args.append("--vocals-only")
        self._launch_worker(prepare_main, args, "Extracting candidate speech from staged files.")

    def _waveform_samples(self, clip_path: Path, num_samples: int) -> list[float]:
        cache_key = f"{clip_path}:{num_samples}"
        cached = self._waveform_sample_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            with wave.open(str(clip_path), "rb") as wav_file:
                n_channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                n_frames = wav_file.getnframes()
                raw = wav_file.readframes(n_frames)
            if sample_width != 2:
                return []
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            if n_channels > 1:
                data = data.reshape(-1, n_channels).mean(axis=1)
            if data.size == 0:
                return []
            peak = float(np.max(np.abs(data)))
            if peak > 0:
                data /= peak
            chunk_size = max(data.size // num_samples, 1)
            samples: list[float] = []
            for i in range(num_samples):
                start = i * chunk_size
                end = min(start + chunk_size, data.size)
                if start >= data.size:
                    break
                samples.append(float(np.max(np.abs(data[start:end]))))
            self._waveform_sample_cache[cache_key] = samples
            return samples
        except Exception:  # noqa: BLE001
            return []

    def _draw_waveform(self, clip_path: Path) -> None:
        canvas = self.waveform_canvas
        if canvas is None:
            return
        canvas.delete("waveform")
        canvas.delete("cursor")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 4:
            canvas.after(60, lambda: self._draw_waveform(clip_path))
            return
        samples = self._waveform_samples(clip_path, width)
        if not samples:
            canvas.create_text(
                width // 2,
                height // 2,
                text="no audio data",
                fill=self.MUTED,
                font=("Segoe UI", 9),
                tags=("waveform",),
            )
            return
        mid_y = height / 2.0
        bar_width = max(width / len(samples), 1.0)
        for i, amp in enumerate(samples):
            x = i * bar_width
            bar_h = max(amp * (mid_y - 2.0), 1.0)
            canvas.create_line(
                x,
                mid_y - bar_h,
                x,
                mid_y + bar_h,
                fill=self.ACCENT,
                width=max(int(bar_width), 1),
                tags=("waveform",),
            )

    def _clear_waveform(self) -> None:
        if self.waveform_canvas is not None:
            self.waveform_canvas.delete("all")

    def _update_waveform_cursor(self, position: float, duration: float) -> None:
        canvas = self.waveform_canvas
        if canvas is None:
            return
        canvas.delete("cursor")
        if duration <= 0:
            return
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 4:
            return
        x = max(0.0, min(position / duration, 1.0)) * width
        canvas.create_line(x, 0, x, height, fill=self.ACCENT_ALT, width=2, tags=("cursor",))

    def _bulk_label_selected(self) -> None:
        selected = list(self.review_tree.selection())
        if not selected:
            messagebox.showwarning("No Selection", "Select clips in the queue first (click, Shift+click, or Ctrl+click for multiple).")
            return
        label = self.bulk_label_var.get()
        if not messagebox.askyesno("Bulk Label", f"Label {len(selected)} selected clip(s) as '{label}'?"):
            return
        for clip_id in selected:
            record = next((r for r in self.records if r.clip_id == clip_id), None)
            if record is None:
                continue
            self.labels[clip_id] = {"label": label, "clip_path": record.clip_path, "source_path": record.source_path}
        self._write_labels(self._labels_path(), self.labels)
        if label == "Shana":
            self.similarity_scores = {}
        self._refresh_review_tree()
        self._append_log(f"[system] bulk labeled {len(selected)} clips as {label}", "system")

    def _bulk_label_visible(self) -> None:
        visible = self._visible_clip_ids()
        if not visible:
            messagebox.showwarning("No Visible Clips", "No clips are visible in the current filtered view.")
            return
        label = self.bulk_label_var.get()
        if not messagebox.askyesno("Bulk Label", f"Label all {len(visible)} visible clip(s) as '{label}'?"):
            return
        for clip_id in visible:
            record = next((r for r in self.records if r.clip_id == clip_id), None)
            if record is None:
                continue
            self.labels[clip_id] = {"label": label, "clip_path": record.clip_path, "source_path": record.source_path}
        self._write_labels(self._labels_path(), self.labels)
        if label == "Shana":
            self.similarity_scores = {}
        self._refresh_review_tree()
        self._append_log(f"[system] bulk labeled {len(visible)} visible clips as {label}", "system")

    def _write_labels(self, labels_path: Path, labels: dict[str, dict[str, Any]]) -> None:
        labels_path.parent.mkdir(parents=True, exist_ok=True)
        with labels_path.open("w", encoding="utf-8") as handle:
            json.dump(labels, handle, indent=2, ensure_ascii=False)

    def _append_record_to_manifest(self, record: ReviewRecord) -> None:
        manifest_path = self._manifest_path()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "clip_id": record.clip_id,
            "source_path": record.source_path,
            "clip_path": record.clip_path,
            "episode_id": record.episode_id,
            "start_seconds": record.start_seconds,
            "end_seconds": record.end_seconds,
            "duration_seconds": record.duration_seconds,
            "text": record.text,
            "language": record.language,
            "avg_logprob": record.avg_logprob,
            "no_speech_prob": record.no_speech_prob,
            "compression_ratio": record.compression_ratio,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        with manifest_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _export_training_subsets(self) -> None:
        dataset_root = self._resolve_data_path(self.dataset_var.get().strip())
        export_root = dataset_root / "exports"
        clean_dir = export_root / "shana_clean"
        light_noise_dir = export_root / "shana_light_noise"
        heavy_noise_dir = export_root / "shana_heavy_noise"
        manifest_dir = export_root / "manifests"
        clean_dir.mkdir(parents=True, exist_ok=True)
        light_noise_dir.mkdir(parents=True, exist_ok=True)
        heavy_noise_dir.mkdir(parents=True, exist_ok=True)
        manifest_dir.mkdir(parents=True, exist_ok=True)

        clean_rows: list[dict[str, Any]] = []
        light_noise_rows: list[dict[str, Any]] = []
        heavy_noise_rows: list[dict[str, Any]] = []
        copied_clean = 0
        copied_light_noise = 0
        copied_heavy_noise = 0

        for record in self.records:
            label = self.labels.get(record.clip_id, {}).get("label")
            if label not in {"Shana", "Shana-light-noise", "Shana-heavy-noise"}:
                continue
            source = Path(record.clip_path)
            if not source.exists():
                self._append_log(f"[system] export skipped missing clip: {source}", "warn")
                continue
            if label == "Shana":
                target_dir = clean_dir
            elif label == "Shana-light-noise":
                target_dir = light_noise_dir
            else:
                target_dir = heavy_noise_dir
            target = target_dir / source.name
            target.write_bytes(source.read_bytes())
            row = {
                "clip_id": record.clip_id,
                "clip_path": str(target),
                "source_clip_path": record.clip_path,
                "source_episode_path": record.source_path,
                "episode_id": record.episode_id,
                "text": record.text,
                "language": record.language,
                "start_seconds": record.start_seconds,
                "end_seconds": record.end_seconds,
                "duration_seconds": record.duration_seconds,
                "label": label,
                "similarity_score": self.similarity_scores.get(record.clip_id),
                "duplicate_status": self._duplicate_status_text(record.clip_id),
                "exact_duplicate_of": self.exact_duplicates.get(record.clip_id),
                "near_duplicate_of": self.near_duplicates.get(record.clip_id, (None, None))[0],
            }
            if label == "Shana":
                clean_rows.append(row)
                copied_clean += 1
            elif label == "Shana-light-noise":
                light_noise_rows.append(row)
                copied_light_noise += 1
            else:
                heavy_noise_rows.append(row)
                copied_heavy_noise += 1

        clean_manifest = manifest_dir / "shana_clean.jsonl"
        light_noise_manifest = manifest_dir / "shana_light_noise.jsonl"
        heavy_noise_manifest = manifest_dir / "shana_heavy_noise.jsonl"
        self._write_jsonl(clean_manifest, clean_rows)
        self._write_jsonl(light_noise_manifest, light_noise_rows)
        self._write_jsonl(heavy_noise_manifest, heavy_noise_rows)

        self.export_status_var.set(
            f"clean={copied_clean} light={copied_light_noise} heavy={copied_heavy_noise}"
        )
        self.status_var.set("Exported clean, light-noise, and heavy-noise Shana subsets.")
        self._append_log(
            "[system] exported training subsets "
            f"clean={copied_clean} light={copied_light_noise} heavy={copied_heavy_noise} root={export_root}",
            "system",
        )

    def _write_jsonl(self, path: Path, rows: list[dict[str, Any]]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _optional_float(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _build_transcribe_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        form = ttk.Frame(parent, style="Card.TFrame")
        form.grid(row=0, column=0, sticky="nsew", pady=(0, 14))
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="File Transcription", style="CardTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 8)
        )

        path_label = ttk.Label(form, text="Media File", style="Muted.TLabel")
        path_label.grid(row=1, column=0, sticky="w", padx=14, pady=6)
        path_entry = ttk.Entry(form, textvariable=self.transcribe_path_var)
        path_entry.grid(row=1, column=1, sticky="ew", padx=8, pady=6)
        browse_btn = ttk.Button(form, text="Browse", style="Ghost.TButton", command=self._browse_transcribe_file)
        browse_btn.grid(row=1, column=2, sticky="ew", padx=(0, 14), pady=6)

        lang_label = ttk.Label(form, text="Language", style="Muted.TLabel")
        lang_label.grid(row=2, column=0, sticky="w", padx=14, pady=6)
        lang_combo = ttk.Combobox(form, textvariable=self.transcribe_lang_var, values=("ja", "en", "auto"), state="readonly")
        lang_combo.grid(row=2, column=1, sticky="ew", padx=8, pady=6)

        model_label = ttk.Label(form, text="STT Model", style="Muted.TLabel")
        model_label.grid(row=3, column=0, sticky="w", padx=14, pady=6)
        model_combo = ttk.Combobox(
            form, textvariable=self.transcribe_model_var,
            values=("base", "small", "medium", "large-v3", "base.en", "small.en"),
        )
        model_combo.grid(row=3, column=1, sticky="ew", padx=8, pady=6)

        stream_label = ttk.Label(form, text="Audio Stream Lang", style="Muted.TLabel")
        stream_label.grid(row=4, column=0, sticky="w", padx=14, pady=6)
        stream_combo = ttk.Combobox(
            form, textvariable=self.transcribe_stream_lang_var,
            values=("jpn", "eng", "ja", "en"), state="readonly",
        )
        stream_combo.grid(row=4, column=1, sticky="ew", padx=8, pady=6)

        ts_check = ttk.Checkbutton(form, text="Show timestamps", variable=self.transcribe_timestamps_var)
        ts_check.grid(row=5, column=1, sticky="w", padx=8, pady=(6, 4))

        btn_panel = ttk.Frame(form, style="Panel.TFrame")
        btn_panel.grid(row=6, column=0, columnspan=3, sticky="ew", padx=14, pady=(4, 8))
        for col in range(4):
            btn_panel.columnconfigure(col, weight=1)
        ttk.Button(btn_panel, text="Transcribe", style="Accent.TButton", command=self._run_transcribe).grid(
            row=0, column=0, sticky="ew", padx=(0, 8), pady=10
        )
        ttk.Button(btn_panel, text="Stop", style="Ghost.TButton", command=self._stop_transcribe).grid(
            row=0, column=1, sticky="ew", padx=(0, 8), pady=10
        )
        ttk.Button(btn_panel, text="Copy Transcript", style="Ghost.TButton", command=self._copy_transcript).grid(
            row=0, column=2, sticky="ew", padx=(0, 8), pady=10
        )
        ttk.Button(btn_panel, text="Save Transcript...", style="Ghost.TButton", command=self._save_transcript).grid(
            row=0, column=3, sticky="ew", pady=10
        )

        ttk.Label(form, textvariable=self.transcribe_status_var, style="Muted.TLabel", wraplength=860, justify="left").grid(
            row=7, column=0, columnspan=3, sticky="w", padx=14, pady=(0, 12)
        )

        self._attach_tooltip(path_label, "Path to any audio or video file. MKV, MP4, WAV, MP3, and other common formats are supported.")
        self._attach_tooltip(path_entry, "Paste the full path to an episode or clip, or use Browse to pick a file.")
        self._attach_tooltip(browse_btn, "Open a file picker to select a media file.")
        self._attach_tooltip(lang_label, "Language hint for Whisper. Use 'ja' for Japanese, 'en' for English, or 'auto' to let the model detect.")
        self._attach_tooltip(lang_combo, "Language hint for Whisper. 'auto' is slower and sometimes less accurate on short files.")
        self._attach_tooltip(model_label, "faster-whisper model to use. 'small' is a good balance of speed and accuracy. 'large-v3' is most accurate but slow on CPU.")
        self._attach_tooltip(model_combo, "faster-whisper model to use. Avoid '.en' models when transcribing Japanese audio.")
        self._attach_tooltip(stream_label, "For multi-track containers, prefer this audio language. Use 'jpn' for the original track.")
        self._attach_tooltip(stream_combo, "Preferred audio stream language. The app will pick the first stream tagged with this language.")
        self._attach_tooltip(ts_check, "Prefix each line with [start --> end] timestamps so you can locate where each line appears in the file.")

        result_frame = ttk.Frame(parent, style="Card.TFrame")
        result_frame.grid(row=1, column=0, sticky="nsew")
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(1, weight=1)
        ttk.Label(result_frame, text="Transcript", style="CardTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=14, pady=(12, 6)
        )
        self.transcribe_text = tk.Text(
            result_frame,
            bg=self.PANEL_ALT,
            fg=self.TEXT,
            insertbackground=self.TEXT,
            wrap="word",
            relief="flat",
            font=("Consolas", 10),
            highlightthickness=1,
            highlightbackground=self.BORDER,
            padx=10,
            pady=10,
        )
        text_scrollbar = ttk.Scrollbar(result_frame, orient="vertical", command=self.transcribe_text.yview)
        self.transcribe_text.configure(yscrollcommand=text_scrollbar.set)
        self.transcribe_text.grid(row=1, column=0, sticky="nsew", padx=(14, 0), pady=(0, 14))
        text_scrollbar.grid(row=1, column=1, sticky="ns", padx=(0, 14), pady=(0, 14))

    def _browse_transcribe_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose a media file to transcribe",
            filetypes=[
                ("Media files", "*.mkv *.mp4 *.wav *.mp3 *.flac *.ogg *.m4a *.aac *.opus *.mka *.webm *.mov"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.transcribe_path_var.set(path)

    def _run_transcribe(self) -> None:
        path_str = self.transcribe_path_var.get().strip()
        if not path_str:
            messagebox.showwarning("No File", "Enter or browse to a media file to transcribe.")
            return
        input_path = Path(path_str).expanduser().resolve()
        if not input_path.exists():
            messagebox.showwarning("File Not Found", f"File not found:\n{input_path}")
            return
        if self.transcribe_worker and self.transcribe_worker.is_alive():
            messagebox.showwarning("Busy", "A transcription is already running.")
            return
        self.transcribe_status_var.set(f"Transcribing {input_path.name}…")
        if self.transcribe_text is not None:
            self.transcribe_text.delete("1.0", "end")
        self._append_log(f"[system] transcribing: {input_path}", "system")
        self.transcribe_worker = threading.Thread(
            target=self._transcribe_worker_main,
            args=(input_path,),
            daemon=True,
        )
        self.transcribe_worker.start()

    def _stop_transcribe(self) -> None:
        if self.transcribe_worker and self.transcribe_worker.is_alive():
            self.transcribe_status_var.set("Stop requested — will finish the current file then stop.")
            self._append_log("[system] transcription stop requested (will finish current file)", "warn")
        else:
            messagebox.showinfo("No Active Transcription", "No transcription is currently running.")

    def _transcribe_worker_main(self, input_path: Path) -> None:
        import argparse
        try:
            from .run_transcribe import transcribe_file, format_transcript
        except ImportError:
            from gamma.run_transcribe import transcribe_file, format_transcript
        try:
            from .config import settings as _settings
        except ImportError:
            from gamma.config import settings as _settings

        writer = QueueWriter(self.event_queue)
        try:
            with redirect_stdout(writer), redirect_stderr(writer):
                args = argparse.Namespace(
                    input_path=str(input_path),
                    language=self.transcribe_lang_var.get().strip(),
                    model=self.transcribe_model_var.get().strip() or "small",
                    device=_settings.stt_device,
                    compute_type=_settings.stt_compute_type,
                    beam_size=5,
                    audio_stream_index=None,
                    audio_stream_lang=self.transcribe_stream_lang_var.get().strip() or None,
                    ffmpeg_bin="ffmpeg",
                    ffprobe_bin="ffprobe",
                )
                segments = transcribe_file(args)
                transcript = format_transcript(segments, timestamps=self.transcribe_timestamps_var.get())
            writer.flush()
            self.event_queue.put(("transcribe_result", transcript))
        except SystemExit as exc:
            writer.flush()
            msg = str(exc.code) if exc.code is not None else "aborted"
            self.event_queue.put(("transcribe_error", msg))
        except Exception as exc:  # noqa: BLE001
            writer.flush()
            self.event_queue.put(("transcribe_error", str(exc)))

    def _copy_transcript(self) -> None:
        if self.transcribe_text is None:
            return
        text = self.transcribe_text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("Empty", "No transcript to copy.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.transcribe_status_var.set("Transcript copied to clipboard.")

    def _save_transcript(self) -> None:
        if self.transcribe_text is None:
            return
        text = self.transcribe_text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("Empty", "No transcript to save.")
            return
        source_name = Path(self.transcribe_path_var.get().strip()).stem if self.transcribe_path_var.get().strip() else "transcript"
        initial_file = f"{source_name}.txt"
        path = filedialog.asksaveasfilename(
            title="Save transcript",
            initialfile=initial_file,
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        Path(path).write_text(text + "\n", encoding="utf-8")
        self.transcribe_status_var.set(f"Saved to {Path(path).name}")
        self._append_log(f"[system] transcript saved: {path}", "system")

    def _on_close(self) -> None:
        self._stop_audio(log_message=False)
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    app = TTSDataPrepApp(root)
    root.minsize(1180, 760)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
