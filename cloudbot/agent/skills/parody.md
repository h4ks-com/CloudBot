---
name: parody
description: Make a song parody (new lyrics sung in the original voice, optional karaoke video, optional radio play) with the paid `parody` job on workflows.h4ks.com. THE DEFAULT for any "parody", "change the lyrics of", "make X sing about Y" request.
---

# Song parody

The `parody` job on workflows.h4ks.com takes a song, swaps its lyrics line by line and sings the
new lines in the original voice. It costs the user credits and runs on the homelab GPU.

**Go straight to the job.** The job downloads the song and looks up its lyrics itself. Pass the
user's song link exactly as given.

## Pick one way to give the new lyrics

**A. The job writes them (the default).** Leave `parody_lyrics` out and put the idea in `prompt`.
Use this whenever the user gives a theme, a joke, or a few example lines "as inspiration": quote
those lines inside `prompt` and say to keep them and continue in that style. Set `amount` to
`a few words`, `most lines` or `every line` from the user's words ("replace every X" means
`every line`).

**B. You write every line.** Only when the user hands you complete lyrics or insists on exact
wording. The job replaces the song line by line, so it refuses the run unless:
- `lyrics` holds the original lyrics, one sung line per line. Fetch them from
  `https://lrclib.net/api/search?track_name=<song>&artist_name=<artist>` (`plainLyrics`) and
  keep their line breaks exactly.
- `parody_lyrics` has exactly one new line for each sung line of `lyrics`, in the same order.
  Section headers like `[Chorus]` and lines only in parentheses are not counted. Repeat a line
  unchanged to keep it. Count both before submitting.

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

When the job fails, its error says why (for example a line count that does not match). Fix that
one thing and submit once more, or tell the user what to change.
