---
name: song-to-midi
description: Turn a real recording into a playable multi-track MIDI in kinesthesia — download the audio, transcribe it, import it, hand back the player links. Use when someone wants a song "as MIDI", to learn/play a real track, or a MIDI of something not already in the library.
---

# Clone a real song into MIDI

Takes an actual recording and produces a multi-track MIDI in kinesthesia: the instruments
are identified, each is transcribed, and the tracks are named and assigned. Use this when
someone wants a real song rather than a hand-built approximation.

**Check the library first.** `kinesthesia_search_midi(q="<song>")` — if there is a good
match, use it. It is instant, and a human-made MIDI beats a transcription. Only continue
here when there is no match, or the user explicitly asked for the real recording.

**Never hand-build a riff as a substitute.** If the user asked for a song and this skill
applies, either transcribe it or say plainly that you cannot. Placing notes by ear and
presenting that as the song is misleading, however good the riff sounds.

## Before you start: tell the user the wait

Transcription runs at **roughly three times the length of the song** and **one job runs at
a time**, so a 3-minute song takes about 10 minutes and longer if something is queued
ahead. Say so up front, then go and do it — do not silently disappear for ten minutes, and
do not abandon the job because it is slow.

**Songs longer than 6 minutes are refused.** Check the duration before downloading and
tell the user if it is too long, rather than discovering it at the transcription step.

## Steps

1. **Find the source.** If the user gave a URL, use it. Otherwise `ytdl_media_info(url=...)`
   on a likely YouTube result, and check `duration` is under 360 seconds.

2. **Download the audio.**
   `ytdl_download_media(url="<video url>", mode="audio", format="mp3")`
   Returns a public link. Audio is what you want here — video wastes time and bandwidth.

3. **Start the transcription.**
   `midifier_transcribe_audio(url="<the mp3 link from step 2>")`
   Returns `{"job_id": "...", "state": "queued"}`. The link must be publicly reachable;
   the service refuses private addresses, so pass the URL ytdl gave you unchanged.

4. **Wait for it.**
   `midifier_transcription_status(job_id="<id>")`
   This one waits for you: it holds the call open until something changes, then answers.
   So when it returns, just call it again — do NOT sit in a tight loop firing it back to
   back, and do not spend a turn on anything else in between.
   While it runs you get `state`, `stage`, `queue_ahead` and `eta_seconds`, so you can
   tell the user how long is left. When `state` is `succeeded` you get `midi_url`, a
   `tracks` list and `dropped_instruments`.
   If `state` is `failed`, read `error` and tell the user what it said. Do not retry
   blindly: the same input usually fails the same way.

5. **Import it into kinesthesia.**
   `kinesthesia_import_project(url="<midi_url>", name="<song>")`
   This gives the MIDI a permanent home in the library and returns the project.

6. **Hand back the links.** `kinesthesia_player_link(...)` with the project, once per mode
   the user would want: `watch` to listen, `learn` to practise, `multiplayer` to play with
   someone. Include the raw MIDI URL too.

## Reporting the result

Give the player links, and say what was actually found — the track list is the interesting
part, since it shows which instruments were heard. If `dropped_instruments` is non-empty,
mention it briefly: the service detected an instrument the model invented and re-ran
without it, which is normal and a sign the result is cleaner, not worse.

Be honest about quality. Transcription of a dense mix is imperfect: some notes will be
wrong, and there is no expression or dynamics. It is a real transcription of the real
recording, which is the point, but it is not a human-made arrangement.

## When this is the wrong tool

- **A simple, well-known riff the user wants exactly right** — building it with
  `kinesthesia_add_notes` is faster and cleaner. Say that is what you are doing and why.
- **The song is already in the library** — search first, always.
- **Over 6 minutes** — refused. Offer to transcribe a shorter version if one exists.
