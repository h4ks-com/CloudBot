---
name: song-to-midi
description: Turn a recording into a multi-track MIDI with the paid `midi` job on workflows.h4ks.com. THE DEFAULT for any "make this song a MIDI" request. Use song-to-midi-gpu instead only when the user names Kaggle, or when the `midi` job type is missing.
---

# Clone a real song into MIDI

The `midi` job on workflows.h4ks.com takes an actual recording and produces a multi-track
MIDI: the instruments are identified, each is transcribed, and the tracks are named and
assigned. It runs on the homelab RTX 3090, one job at a time, and costs the user credits.

**Check the library first.** `kinesthesia_search_midi(q="<song>")`: if there is a good
match, use it. It is instant and free, and a human-made MIDI beats a transcription. Only
continue here when there is no match, or the user explicitly asked for the real recording.

**Never hand-build a riff as a substitute.** If the user asked for a song and this skill
applies, either transcribe it or say plainly that you cannot. Placing notes by ear and
presenting that as the song is misleading, however good the riff sounds.

**Songs longer than 6 minutes are refused.** Check the duration first and tell the user if
it is too long.

## Steps

1. **Find the source.** If the user gave a URL, use it. Otherwise `ytdl_media_info(url=...)`
   on a likely YouTube result, and check `duration` is under 360 seconds.

2. **Get a direct audio link.**
   `ytdl_download_media(url="<video url>", mode="audio", format="mp3")` returns a public
   link. The job downloads the file itself, so pass that direct mp3 link.

3. **Submit it.** `workflows_submit(type="midi", params={"url": "<the mp3 link>"})`.
   Tell the user it is submitted, its price in credits and the run page. The bot announces
   the result in the channel itself: the MIDI file and a kinesthesia link that plays it.
   Never poll or wait for it.

If the submit fails for lack of credits, say so and give the wallet link from the error.
Never run this and `song-to-midi-gpu` for the same song.

## Reporting the result

Be honest about quality. Transcription of a dense mix is imperfect: some notes will be
wrong, and there is no expression or dynamics. It is a real transcription of the real
recording, which is the point, but it is not a human-made arrangement.

## When this is the wrong tool

- **A simple, well-known riff the user wants exactly right**: building it with
  `kinesthesia_add_notes` is faster and cleaner. Say that is what you are doing and why.
- **The song is already in the library**: search first, always.
- **Over 6 minutes**: refused. Offer to transcribe a shorter version if one exists.
