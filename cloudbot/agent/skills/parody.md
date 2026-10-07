---
name: parody
description: Make a song parody (new lyrics sung in the original voice, optional karaoke video, optional radio play) with the paid `parody` job on workflows.h4ks.com. THE DEFAULT for any "parody", "change the lyrics of", "make X sing about Y" request.
---

# Song parody

The `parody` job on workflows.h4ks.com takes a song, swaps its lyrics line by line and sings the
new lines in the original voice. It costs the user credits and runs on the homelab GPU.

**Go straight to the job.** The job downloads the song and looks up its lyrics itself. Pass the
user's song link exactly as given.

## Never write the lyrics yourself

The job has its own lyric writer that fits every new line to the song's timing. You only pass
the idea. Leave `parody_lyrics` and `lyrics` out and put everything in `prompt`:
- the theme or joke, in the user's words;
- any example lines, pasted text or linked text the user gave, quoted verbatim, with "keep these
  lines and write the rest in the same style";
- `amount`: `a few words`, `most lines` or `every line` from the user's words ("replace every X"
  means `every line`).

Do not finish, extend, clean up or count lyrics, even when the user asks you to "complete the
rest": the job does that. Fill `parody_lyrics` only when the user explicitly says to use their
lyrics as the exact final lyrics; then pass their text unchanged and nothing else.

## Fields

- `url`: the song link, required.
- `title`: a short title for the result.
- `radio`: true when the user wants it played on h4ks radio.
- `video`: true for a karaoke video of the parody, with `look` (neon, classic, sunset, ocean,
  mono), `highlight` (sweep, word, glow) and `background` (bars, waves, spectrum). Leave those
  three out unless the user names a style.

## Submit

`workflows_submit_job(type="parody", params={...})`. Tell the user the job link and its price, or,
when it returns a form link, give them that link to open, log in and submit. The bot announces
the result in the channel itself. Never poll or wait for it.

When the job fails because given lyrics do not match the song line by line, submit once more
with `parody_lyrics` left out and the user's lyrics quoted in `prompt`. For any other error, tell
the user the reason.
