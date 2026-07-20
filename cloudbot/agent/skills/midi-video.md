---
name: midi-video
description: Record a video (with sound) of a song playing in the kinesthesia piano-roll — falling notes over a keyboard — and share the mp4. Use when someone wants a video/recording of a MIDI song playing.
---

# Record a MIDI song as a video

Make an mp4 of a song playing in the kinesthesia player (piano keys + falling notes, with
audio). You do the MIDI prep here, then hand the finished page to the video agent
(`create_video`) to record — it has the browser-recording tools; you just tell it the url, how
long, and how to start it.

1. **Find it.** `kinesthesia_search_midi(q="<song>")` → pick the best match; keep its `.mid`
   url and `source`.

2. **Check length.** `kinesthesia_midi_info(url="<.mid url>")` → read `duration` (seconds).
   The playback length is `duration / speed` (default speed 1). The recorder caps at 600s, so if
   `duration / speed` > 570 tell the user it's too long and stop. (Its `tracks` list lets you
   feature one part in step 3 — optional.)

3. **Build the page.** `kinesthesia_player_link(url="<.mid url>", name="<song>",
   source="<source>", mode="watch", focus=true)` → the `/watch` url. `focus=true` strips the
   page to just the keys and falling notes — the recordable view (watch only). Add `tracks=[n]`,
   `speed`, or `transpose` only if the user asked.

4. **Hand it to the video agent.** Call `create_video` with a brief telling it to make ONE
   `video_record_website` call with all of these arguments (no separate start/stop):
   - `url`: the `/watch` url,
   - `duration_seconds`: `ceil(duration / speed) + 8` (the whole song plus a tail buffer),
   - `script: [{action:{type:"key",key:"Space"}}]` — Space fires at capture start, so the song
     plays from the first frame with no dead intro,
   - `settle_ms: 3000` — waits after load so the intro tooltips fade before capture,
   - `wait: true` — the call blocks and returns the finished mp4 directly, so it can't be stopped
     early or truncated.

   Example brief: *"Make one video_record_website call: url=<watch url>, duration_seconds=<N>,
   script=[{action:{type:'key',key:'Space'}}], settle_ms=3000, wait=true. It's a music player —
   that fires Space at the start and blocks until the full song is recorded. Return the mp4."*

`create_video` runs in the background and posts the finished mp4 itself, so you don't wait or
reply with a link — it handles the sharing.

What matters:
- Your job is the url, the track, and the duration; the video agent does the recording.
- `focus=true` + `mode="watch"` — the only recordable view.
- `duration_seconds` = `ceil(duration / speed) + 8`, and under 600s — check before handing off.
- One `video_record_website` call with `script` (Space at start), `settle_ms: 3000`, and
  `wait: true`. `wait` blocks for the whole song and returns the finished mp4 — never a separate
  `video_record_stop`, which cuts it short.
