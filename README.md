# UBOAT Save FPS Fix

A small, offline Python tool for one recognised UBOAT save-state problem: extra active captain actors outside the real crew, repeatedly searching for a World NavMesh after loading at sea.

- Source repository: https://github.com/10Legend01/uboat-save-fps-fix
- Multilingual guide: https://10legend01.github.io/uboat-save-fps-fix/
- Release v0.1.0: https://github.com/10Legend01/uboat-save-fps-fix/releases/tag/v0.1.0

**Version:** 0.1.0 · **Python:** 3.10+ · **Dependencies:** standard library only · **License:** MIT

## Read the guide

- [Interactive player guide and case study](index.html): open this file locally in a browser. English is the default; Russian, German, Polish, and French are included. The English text also works without JavaScript.
- [Developer report](DEVELOPER_REPORT_EN.txt): runtime evidence, object ownership, repair boundaries, and unresolved questions.
- [Start-here instructions](README_FIRST.txt) and [validation summary](VALIDATION.txt).
- [English Steam post draft](STEAM_POST_EN.txt), [Russian draft](STEAM_POST_RU.txt), and [official publication-format sources](STEAM_DISTRIBUTION_SOURCES.txt).

After GitHub Pages is enabled for this repository, `index.html` is also the site entry point. A repository's HTML file view may show the source; opening it locally or using the published Pages address displays the guide.

## Scope

This is a targeted save-copy repair, not a general FPS optimiser or a game mod. Low FPS alone does not establish that your save has this problem.

The tool reads each save's own type schemas and validates the recognised actor pattern, crew membership, real skipper reference, and relevant state before editing. It does not assume the example campaign's name, character name, boat, byte offsets, crew count, or clone count. Unrecognised or ambiguous structures are refused.

For recognised unwanted actors, the repair changes only their owner GameObjects' `activeSelf` bytes from true to false. It retains objects and references. Other decompressed bytes, including real crew and the campaign achievement-lockout flag, are preserved. Recompression can change the final file size.

The program does not run UBOAT, install code into it, change settings, or upload files.

## Quick start

Download the source files or extract a release package, then open a terminal in the folder containing [uboat_save_fps_fix.py](uboat_save_fps_fix.py). Replace the example filenames with your save paths.

First inspect the save without writing a repaired copy:

```console
python uboat_save_fps_fix.py "campaign.save" --dry-run
```

If the signature is recognised and the checks pass, write a separate file:

```console
python uboat_save_fps_fix.py "campaign.save" --output "campaign.fixed.save" --json-report "repair.json"
```

Use an existing output folder and new filenames. Existing files are refused, and the original save is retained. `--json-report` is optional. If `--output` is omitted, the tool chooses a new timestamped name next to the input. No repaired save is needed when the recognised problem is absent.

On Windows, `py -3` can replace `python` if the Python launcher is installed. To see all supported options:

```console
python uboat_save_fps_fix.py --help
```

Fully close UBOAT before the first repaired load, especially if a known affected original was already loaded. Start the game and load the new copy. Keep the original until you are satisfied with the result. Normal hot reloads of the repaired lineage worked in the reported case; restarting after every clean load is not a requirement established by these tests.

After a successful test, make a new in-game save and check the result through further save/load cycles. If you return to an old affected save, restart before returning to the repaired branch so that surviving objects from the old session do not confuse the comparison.

### Python API

Run this from the tool's folder, or make its module importable. Replace the paths with your files in an existing directory:

```python
from uboat_save_fps_fix import inspect_save, repair_save

source = r"C:\path\to\campaign.save"
report = inspect_save(source)
print(report["clones_active"], report["achievement_lockout"])

if report["clones_active"]:
    result = repair_save(
        source,
        r"C:\path\to\campaign.fixed.save",
        json_report=r"C:\path\to\repair.json",
    )
    print(result["status"])
```

`inspect_save` only reads. The repair call creates a new save and, when requested, a separate JSON report. Treat a refused or unsupported file as a reason for further diagnosis; do not bypass the checks or edit offsets by hand.

## Tested case and limits

The investigated Windows campaign began on **2024.1 Full Release Patch 2** and was tested on **2026.1 Patch 21**. That history alone does not prove the original cause.

- Before repair, the player reported about **15–25 FPS** after loading at sea. Pausing did not remove the slowdown.
- Two affected saves contained **32 and 64** active extra World-layer captain actors, outside the actual **27-member crew** and real skipper reference.
- **143/200** main-thread samples were in Unity NavMesh polygon queries. All **17** recovered managed caller chains led through recursive `NavMeshNavigator.SnapToNavMesh` to `NavMesh.SamplePosition`, including the **6000 m** recovery search.
- Deactivating only the recognised extras restored normal FPS according to the player. There is no controlled numerical after-repair benchmark.
- Three later user-created saves each retained **32 inactive extras, zero active extras, and 27 active real crew**. No growth or reactivation was found in those tests.

The first creator and trigger of the extra actors remain unknown. Incomplete cleanup across loads is a hypothesis consistent with the observed ownership, not a proven initial creation site. Counts of 32 then 64 do **not** prove that every load creates or doubles copies.

[VALIDATION.json](VALIDATION.json) records the shipped coverage: 30 automated tool checks, real-save cases and synthetic refusal cases, plus offline browser checks for five languages at desktop and mobile sizes. The tool was tested with Python 3.12.14. This does not validate every game version, platform, campaign, or mod.

## Achievements

The original `isPlaythroughLockedOutOfAchievements` value is preserved exactly, including `true`. This tool does not unlock a restricted campaign or remove an existing lockout.

All 76 `.save` files inspected in the reported case had this flag set to false. That was an audit of 76 files, not a test of 76 achievements. Steam and UBOAT still decide whether achievement conditions are met; delivery of Steam achievements is not guaranteed.

## Privacy and rollback

No personal saves, player names, Steam IDs, local installation paths, runtime memory captures, game binaries, or decompiled game source are included. The example paths above are placeholders. Keep your personal save files private unless you deliberately choose to share them.

Tool-generated diagnostic reports can contain your file paths and character information. Review and redact them before posting. Start a bug report with the game version, symptom, and a reviewed dry-run summary; a personal save upload is not required by default.

To roll back, fully close the game and load your retained original. Progress made only in the repaired branch will not appear in an older original.

## Repository and Pages layout

The static site is intentionally served from the repository root:

```text
index.html                         Offline guide and Pages entry point
report_content.json                Five complete translations
uboat_save_fps_fix.py               Tool and Python API
DEVELOPER_REPORT_EN.txt             Evidence and investigation limits
README_FIRST.txt                   Player instructions
STEAM_POST_EN.txt / STEAM_POST_RU.txt  Editable forum drafts
STEAM_DISTRIBUTION_SOURCES.txt      Official publication-format sources
VALIDATION.txt / VALIDATION.json    Test coverage
SHA256SUMS.txt                      Checksums of the publication files
LICENSE.txt                        MIT license
.nojekyll                          Serve the static root without Jekyll
```

All site downloads use relative links, so the same files work locally and under a project Pages site. The repository, guide and release are linked above and in the forum drafts. A release ZIP can be attached to a GitHub Release; no ZIP or personal fixture needs to be committed to this source tree.

The included `.gitattributes` preserves release-file bytes across Git checkouts so `SHA256SUMS.txt` stays meaningful. It does not mark the files as binary or prevent text diffs. The manifest covers the publication files except itself. The Python tool remains byte-identical to the original v0.1.0 release; publication documentation and repository metadata do not change the tool.

## License and affiliation

MIT; see [LICENSE.txt](LICENSE.txt). The existing contributor copyright notice is retained.

This is an independent community utility, not an official Deep Water Studio or Valve release. UBOAT and Steam belong to their respective owners.
