---
name: parody
description: Make a song parody (new lyrics sung in the original voice, optional karaoke video, optional radio play) with the paid `parody` job on workflows.h4ks.com. THE DEFAULT for any "parody", "change the lyrics of", "make X sing about Y" request.
---

# Song parody

The `parody` job on workflows.h4ks.com takes a song, swaps its lyrics line by line and sings the
new lines in the original voice. It costs the user credits and runs on the homelab GPU.

**Go straight to the job.** The job downloads the song and looks up its lyrics itself. Pass the
user's song link exactly as given.

## The lyrics come from the user or the job

The job has its own lyric writer that fits every line to the song's timing, so your part is to
carry the user's words to it **verbatim**. Pick the one case that matches:

- **An idea only** ("make it about a cat who wants dinner"): put the idea in `prompt`.
- **Some lyrics** (example lines, a pasted chorus, a linked text, a draft): quote them verbatim in
  `prompt` and add what the user wants done with them, in their words: "use these lines exactly
  where they fit and write the rest in the same style", or "take these as inspiration".
- **The whole song's lyrics, ready to sing**: pass them verbatim in `parody_lyrics`. The job
  swaps the song line by line, so it refuses lyrics whose line count differs from the original;
  it then fails at once at no cost. On that error, submit once more with the same text quoted in
  `prompt` as in the case above.

Set `amount` (`a few words`, `most lines`, `every line`) from the user's words whenever the job
writes lines ("replace every X" means `every line`). The writing, finishing and fitting of lines
is the job's work, so the text you send is always the user's own.

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

For a failure other than the line count above, tell the user the reason from the error.
