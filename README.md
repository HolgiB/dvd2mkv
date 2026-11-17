# dvd2mkv

Copy the titles of a DVD ISO to MKV **without re-encoding** — a small,
scriptable MakeMKV replacement for Linux.

`dvd2mkv.py` reads the ISO through ffmpeg's `dvdvideo` demuxer. No loop mount,
no root, no extra libraries, no proprietary tool. Video, all audio tracks and
all VobSub subtitle tracks are stream-copied; chapters and language tags stay
intact.

## Requirements

- `ffmpeg` / `ffprobe` **7.1 or newer** with the `dvdvideo` demuxer
  (`ffmpeg -demuxers | grep dvdvideo`). Tested with ffmpeg 9.0.
- Python 3.8+
- A DVD image in `.iso` format (a plain `VIDEO_TS` copy also works if it is
  wrapped in an ISO)

Nothing else. No `libdvdcss`, no `dvdbackup`, no `mkvmerge`.

> For **physical discs** or an unpacked `VIDEO_TS` folder use the companion
> shell script pattern (`dvdbackup` + `mkvmerge`) — this tool is for images.

## Install

It is a single file:

```bash
curl -O https://raw.githubusercontent.com/HolgiB/dvd2mkv/main/dvd2mkv.py
chmod +x dvd2mkv.py
./dvd2mkv.py --help
```

Or clone the repo:

```bash
git clone https://github.com/HolgiB/dvd2mkv.git
cd dvd2mkv
./dvd2mkv.py --help
```

## Usage

```bash
# every ISO below ./ISOs, output next to it in ./MKV
./dvd2mkv.py --src ./ISOs --dst ./MKV

# see what would happen, write nothing
./dvd2mkv.py --src ./ISOs --dst ./MKV --dry-run

# a single disc, by path regex
./dvd2mkv.py --src /srv/dvds --dst /srv/mkv --only "Goonies"

# episodes: keep everything longer than 10 minutes
./dvd2mkv.py --src /srv/dvds --dst /srv/mkv --min-len 600

# tolerate discs whose IFO duration disagrees with the real playtime
./dvd2mkv.py --src /srv/dvds --dst /srv/mkv --lenient
```

### Options

| Option | Default | Meaning |
|---|---|---|
| `--src DIR` | `./ISOs` | directory searched recursively for `*.iso` |
| `--dst DIR` | `./MKV` | output directory; the source sub-tree is mirrored |
| `--min-len SEC` | `120` | titles shorter than this are skipped (menus, logos, padding) |
| `--only REGEX` | – | only process ISO paths matching the regex |
| `--dry-run` | off | list titles and target names, write nothing |
| `--lenient` | off | accept duration mismatch against the IFO if the result is plausible |
| `--report FILE` | `<dst>/dvd2mkv_<timestamp>.log` | log file |
| `--version` | – | print version |

## Output

- One title → `<ISO name>.mkv`
- Several titles → `<ISO name>_01.mkv`, `_02.mkv`, …
- The relative directory structure below `--src` is preserved.
- Files are written as `.part` and only renamed after verification, so an
  interrupted run never leaves a plausible-looking broken MKV behind.

**Resumable:** an existing MKV larger than 1 MiB is skipped. Just run the same
command again after an abort.

## How it works

1. **Enumerate titles** — `ffprobe -f dvdvideo -trim 0 -title N` is called for
   N = 1, 2, 3 … until ffmpeg reports `Title N not found`. `-trim 0` is needed
   to read the title list without decoding from the start.
2. **Filter** — titles shorter than `--min-len` are dropped. That is the only
   reliable way to separate menus, logos and padding from real content.
3. **Remux** — `ffmpeg -f dvdvideo -title N -i image.iso -map 0:v:0 -map 0:a?
   -map 0:s? -c copy`. No encoding. `pcm_dvd` audio is converted losslessly to
   FLAC because Matroska cannot store it. Track titles such as
   `Deutsch 5.1` or `English Stereo` are generated from the language and
   channel layout tags.
4. **Verify** — stream count and duration (±5 s) are compared against the
   source title; only then is `.part` renamed to `.mkv`.

## Notes and limitations

- **IFO duration ≠ real duration.** Some discs declare padding cells or a
  slightly different playtime in the IFO. The strict check would then reject a
  perfectly good remux — use `--lenient` for those discs. The log still prints
  the deviation.
- **Copy protection.** CSS is not decrypted by ffmpeg. This tool works on
  unencrypted images (your own discs, already-decrypted backups). It does not
  break encryption.
- **One ISO at a time.** DVD reading is I/O-bound; running several in parallel
  mostly makes the drive seek. There is deliberately no `--jobs`.
- **Title numbering is disc-specific.** `Title 1` is not necessarily the main
  film. Use `--dry-run` first and check durations.
- **VobSub stays VobSub.** Subtitles are copied as bitmap subtitles, not
  converted to text.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Title 1 not found` immediately | ISO has no `VIDEO_TS` structure, or ffmpeg is too old |
| `Error opening input` / `dvdvideo` unknown | ffmpeg < 7.1 — upgrade |
| Everything is skipped as "short" | lower `--min-len` |
| All titles fail verification with ±1–2 s | use `--lenient` |
| Output is 0 bytes / only a menu | wrong title number; run with `--dry-run` |

## License

MIT — see [LICENSE](LICENSE).
