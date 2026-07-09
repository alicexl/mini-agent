#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Transcribe demo6.m4a with faster-whisper (large-v3, CPU int8)."""
import os, time
from datetime import timedelta

os.environ["PATH"] = r"D:\ffmpeg\bin;" + os.environ.get("PATH", "")

from faster_whisper import WhisperModel

AUDIO = r"D:\workspace\demo6.m4a"
OUT   = r"D:\workspace\demo6\demo6_transcript.txt"

print(f"[whisper] loading model large-v3 (CPU int8)...")
t0 = time.time()
model = WhisperModel("large-v3", device="cpu", compute_type="int8")
print(f"[whisper] model loaded in {time.time()-t0:.1f}s")

print(f"[whisper] transcribing {AUDIO}...")
t0 = time.time()
segments, info = model.transcribe(AUDIO, language="zh", vad_filter=True, beam_size=5)
print(f"[whisper] duration: {info.duration:.1f}s ({timedelta(seconds=int(info.duration))})")

with open(OUT, "w", encoding="utf-8") as f:
    for seg in segments:
        line = f"[{seg.start:.1f}-{seg.end:.1f}] {seg.text.strip()}"
        print(line, flush=True)
        f.write(line + "\n")

print(f"\n[whisper] done in {time.time()-t0:.1f}s, output -> {OUT}")
