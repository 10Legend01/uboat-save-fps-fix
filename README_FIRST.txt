UBOAT SAVE FPS FIX — START HERE

Open index.html in your browser for the complete player guide and developer
report. English is the default; Russian, German, Polish and French are included.
The page works offline. No installation, account or web server is needed to read it.

This utility addresses one identified save-state problem: unwanted, active World
copies of the player's skipper, outside the real crew. It is not a general FPS
optimizer and does not fix every possible performance problem.

Requirements
------------
Python 3.10 or newer. Only the Python standard library is used.
Official Python downloads: https://www.python.org/downloads/

Windows: open Terminal/PowerShell in the extracted folder. If Python is installed
through the standard Windows installer, "py -3" can be used instead of "python".

1. Fully exit UBOAT before testing a repaired save.
2. Check the save first:

   python uboat_save_fps_fix.py "C:\path\to\MySave.save" --dry-run

3. Create a new copy under a new name:

   python uboat_save_fps_fix.py "C:\path\to\MySave.save" --output "C:\path\to\MySave_FPS_FIXED.save"

4. Start UBOAT and load the new save. Keep the original.

You may place the new copy in the same Saves folder as the original. The normal
Windows location is:

   %USERPROFILE%\AppData\LocalLow\Deep Water Studio\UBOAT\Saves

When --output is omitted, the script chooses a new timestamped name next to the
input. Existing files are never overwritten. With --dry-run no repaired save is
written. If the recognized problem is absent, a new save is unnecessary.

Achievement state
-----------------
The original campaign's achievement-lockout flag is preserved exactly, whether
it is false or true. The script does not unlock achievements or remove an
existing lockout. Steam and the game still decide whether an achievement's
conditions have been met.

What the repair changes
-----------------------
Only the activeSelf byte of each positively identified unwanted duplicate is
changed from true to false. Objects are not deleted. Real crew, the real skipper,
quests and other serialized bytes are preserved. The compressed output may have
a different size because the complete DEFLATE stream is recompressed.

Compatibility and limits
------------------------
The utility reads each file's own serialized type schemas; it does not assume
the sample save's byte offsets, character name, boat, crew count or clone count.
Unrecognized or ambiguous structures are refused. This does not constitute
validation of every UBOAT release, platform or mod. See VALIDATION.txt for the
exact test coverage shipped with this package.

For an unexpected refusal, keep your original file and the diagnostic output.
Do not bypass checks or edit offsets by hand. A successful repair must still be
tested in the game; this tool cannot launch or benchmark UBOAT for you.

After a successful in-game test, make a new manual save, restart, load it, and
make another save. A later return to port and departure is a useful additional
check for recurrence. Loading a known bad save can reintroduce active objects
into the running game; restart before returning to the repaired branch.

Sharing
-------
The package intentionally contains no personal save, player name, Steam ID,
local installation path, game DLL, decompiled game source, or Python runtime.
Tool-generated local diagnostics may contain your file paths and character
information. Review them before posting them publicly.

STEAM_POST_EN.txt is a ready-to-edit discussion draft, not a published post.
DEVELOPER_REPORT_EN.txt describes the evidence and the remaining uncertainty.
The HTML contains publication options and official links. Repository, guide and release links are included in the documentation.
The forum posts are editable drafts; this package does not submit them.

Русский: откройте index.html и выберите «Русский». Исходный сейв не изменяется.

License: MIT; see LICENSE.txt. This is a community utility, not an official
Deep Water Studio or Valve release. UBOAT and Steam belong to their owners.
