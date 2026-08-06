---
name: song-to-midi
description: Turn a recording into a multi-track MIDI on the self-hosted midifier service. Use ONLY when the user names the service, the MCP or midifier, or when Kaggle has no GPU quota left. It holds the homelab GPU for the whole job and is several times slower than song-to-midi-gpu, which is the default.
---

# Clone a real song into MIDI, on the midifier service

Takes an actual recording and produces a multi-track MIDI in kinesthesia: the instruments
are identified, each is transcribed, and the tracks are named and assigned.

**`song-to-midi-gpu` is the default path.** Come here only when the user asked for the
service, the MCP or midifier by name, or when `kaggle_quota` shows no GPU left. This one
occupies the homelab card for the whole job, and one job runs at a time.

**Never run both.** Starting a job here and then switching to Kaggle transcribes the same
song twice and keeps the card busy for a result nobody reads. If you do abandon a job,
cancel it.

**Check the library first.** `kinesthesia_search_midi(q="<song>")` — if there is a good
match, use it. It is instant, and a human-made MIDI beats a transcription. Only continue
here when there is no match, or the user explicitly asked for the real recording.

**Never hand-build a riff as a substitute.** If the user asked for a song and this skill
applies, either transcribe it or say plainly that you cannot. Placing notes by ear and
presenting that as the song is misleading, however good the riff sounds.

## The wait

Transcription takes **several times the length of the song** and **one job runs at a time**,
so expect many minutes, longer if something is queued ahead. Say so up front, then sleep
through it with `wait` rather than checking over and over — every check costs a model call,
sleeping costs one per wait.

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

4. **Check the estimate, and decide whether it fits.**
   `midifier_transcription_status(job_id="<id>")` answers at once with `state`, `stage`,
   `queue_ahead`, `eta_seconds` and `segments_done`/`segments_total`.

   **If `eta_seconds` is over 1200, do not wait.** You have roughly 40 minutes for the whole
   run, and a job longer than about 20 minutes will outlast it — you would sleep through your
   own budget and deliver nothing. Give the user the estimate and the job id, tell them to ask
   again later, and stop there. The transcription keeps running without you.

   Re-check this every time you come back: a queue ahead of you, or a denser song than the
   first estimate assumed, can push it past the limit mid-way. When it does, hand off then.

5. **Otherwise sleep until it is done.**

   - still running → `wait(seconds=<eta_seconds, at most 300>, reason="transcription")`,
     then check again
   - `succeeded` → you get `midi_url`, `tracks` and `dropped_instruments`
   - `failed` → read `error` and say what it said; do not retry blindly, the same input
     usually fails the same way

   Always sleep between checks. Checking without waiting burns a model call for nothing.

6. **Import it.** `kinesthesia_import_project(url="<midi_url>", name="<song>")`.
   This gives the MIDI a permanent home in the library and returns the project.

7. **Hand back the links.** `kinesthesia_player_link(...)` with the project, once per mode
   the user would want: `watch` to listen, `learn` to practise, `multiplayer` to play with
   someone. Include the raw MIDI URL too.

**If the run is cut short**, give the user the job id. The transcription keeps running on the
service, so asking again later collects it — starting over costs the same many minutes and
produces an identical file, because decoding is deterministic.

## Reporting the result

Give the player links, and say what was actually found — the track list is the interesting
part, since it shows which instruments were heard. If `dropped_instruments` is non-empty,
mention it briefly: those are lanes the service folded into another because they were one
part the model had renamed partway through. Normal, and a sign the result is tidier.

Be honest about quality. Transcription of a dense mix is imperfect: some notes will be
wrong, and there is no expression or dynamics. It is a real transcription of the real
recording, which is the point, but it is not a human-made arrangement.

## When this is the wrong tool

- **A simple, well-known riff the user wants exactly right** — building it with
  `kinesthesia_add_notes` is faster and cleaner. Say that is what you are doing and why.
- **The song is already in the library** — search first, always.
- **Over 6 minutes** — refused. Offer to transcribe a shorter version if one exists.
