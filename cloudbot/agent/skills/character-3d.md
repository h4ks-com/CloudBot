---
name: character-3d
description: Make an animated, rigged 3D character (GLB) with the paid `character-3d` job on workflows.h4ks.com, from a picture, a description or both. THE DEFAULT for any "3D character", "animated character", "rigged model" or "make this picture a 3D character" request.
---

# Animated 3D character

The `character-3d` job on workflows.h4ks.com draws the character as a full-body T-pose, builds
the 3D model, rigs it and animates it. A picture is only a reference: the job redraws it into a
full-body character, so a face, an avatar or a photo all work as they are. It costs the user
credits and runs on the homelab GPU.

**Go straight to the job.** Do not describe, research or download the picture, and do not look
up who is in it. The job reads the picture itself. Submit in your first steps.

## Steps

1. **Map the request to the fields.**
   - `image`: the picture URL the user gave, exactly as given. Leave it out when there is none.
   - `prompt`: a short description of a humanoid character with two arms and two legs, taken
     from the user's words ("a history professor in a tweed jacket"). Required when there is no
     image.
   - `basic_moves`: a list picked from Idle, Walk, Run, Sprint, Jump, Crouch Walk, Roll, Punch,
     Sword Attack, Cast Spell, Shoot, Hit, Death, Dance, Sit. "All basic moves" means all 15.
   - `animations`: up to 4 extra moves for AI to animate, one per line, like "wave hello". When
     the user invites you to add some, pick up to 4 that fit the character.

2. **Submit.** `workflows_submit_job(type="character-3d", params={...})`. Tell the user the job
   link and its price, or, when it returns a form link, give them that link to open, log in and
   submit. The bot announces the result in the channel itself. Never poll or wait for it.
