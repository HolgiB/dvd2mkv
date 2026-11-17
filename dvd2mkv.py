#!/usr/bin/env python3
"""dvd2mkv.py - Copy titles from DVD ISOs to MKV without re-encoding.

Reads ISO images directly through ffmpeg's dvdvideo demuxer - no loop mount,
no root, no MakeMKV.

- video (MPEG-2), every audio track and every VobSub track are stream-copied
- chapters and language tags are preserved
- track titles are generated ("Deutsch 5.1", "English Stereo", ...)
- titles shorter than --min-len (menus, logos, padding) are skipped
- output file is named after the ISO; multiple titles become _01, _02, ...
- resumable: existing, plausibly sized MKVs are skipped

Example:
  dvd2mkv.py --src ./ISOs --dst ./MKV
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

__version__ = "1.0.0"

LANG = {
    'ger': 'Deutsch', 'deu': 'Deutsch', 'eng': 'Englisch', 'fre': 'Franzoesisch',
    'fra': 'Franzoesisch', 'spa': 'Spanisch', 'ita': 'Italienisch', 'tur': 'Tuerkisch',
    'jpn': 'Japanisch', 'rus': 'Russisch', 'dut': 'Niederlaendisch', 'nld': 'Niederlaendisch',
    'pol': 'Polnisch', 'swe': 'Schwedisch', 'dan': 'Daenisch', 'nor': 'Norwegisch',
    'chi': 'Chinesisch', 'zho': 'Chinesisch', 'kor': 'Koreanisch', 'ara': 'Arabisch',
    'hin': 'Hindi', 'por': 'Portugiesisch', 'cze': 'Tschechisch', 'ces': 'Tschechisch',
    'hun': 'Ungarisch', 'gre': 'Griechisch', 'ell': 'Griechisch', 'heb': 'Hebraeisch',
    'und': '', '': '',
}
CHAN = {
    'mono': 'Mono', 1: 'Mono',
    'stereo': 'Stereo', 2: 'Stereo',
    3: '2.1', 4: '4.0', 5: '5.0', 6: '5.1', 7: '6.1', 8: '7.1',
}


def run(cmd, timeout=600, text=True):
    return subprocess.run(cmd, capture_output=True, text=text, timeout=timeout)


def probe_title(iso, title, trim0=True, timeout=300):
    """Probe one title. Returns (info|None, not_found)."""
    cmd = ['ffprobe', '-v', 'error']
    cmd += ['-f', 'dvdvideo', '-trim', '0'] if trim0 else ['-f', 'dvdvideo']
    cmd += ['-title', str(title), '-i', iso,
            '-show_entries',
            'stream=index,codec_type,codec_name,channels,channel_layout,duration'
            ':stream_tags=language,title:format=duration',
            '-of', 'json']
    try:
        p = run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, False
    err = p.stderr or ''
    if p.returncode != 0 or not (p.stdout or '').strip():
        if 'not found' in err:
            return None, True
        return None, False
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return None, False
    try:
        dur = float(data.get('format', {}).get('duration') or 0)
    except (TypeError, ValueError):
        dur = 0.0
    streams = data.get('streams', [])
    if dur <= 0:
        ds = []
        for s in streams:
            try:
                ds.append(float(s.get('duration') or 0))
            except (TypeError, ValueError):
                pass
        dur = max(ds) if ds else 0.0
    return {'title': title, 'duration': dur, 'streams': streams}, False


def track_title(stream, seen_lang):
    code = (stream.get('tags', {}) or {}).get('language', '')
    base = LANG.get(code.lower(), code.upper() if code else '')
    if stream.get('codec_type') == 'audio':
        layout = (stream.get('channel_layout') or '').lower()
        ch = stream.get('channels')
        kind = CHAN.get(layout) or CHAN.get(ch) or ''
        name = ' '.join(x for x in (base, kind) if x)
    else:
        name = base
    if not name:
        name = 'Tonspur' if stream.get('codec_type') == 'audio' else 'Untertitel'
    key = (stream.get('codec_type'), name)
    seen_lang[key] = seen_lang.get(key, 0) + 1
    if seen_lang[key] > 1:
        name = f'{name} #{seen_lang[key]}'
    return name


def enumerate_titles(iso, max_titles=99, log=print):
    """Walk title numbers until ffmpeg reports 'Title N not found'."""
    found = []
    for t in range(1, max_titles + 1):
        info, notfound = probe_title(iso, t, trim0=True)
        if notfound:
            break
        if info is None:
            log(f'    WARN title {t}: unreadable/empty, skipped')
            continue
        found.append(info)
    return found


def rip(iso, title, out_path, meta_title, lenient=False):
    cmd = ['ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'error', '-y',
           '-f', 'dvdvideo', '-title', str(title), '-i', iso,
           '-map', '0:v:0', '-map', '0:a?', '-map', '0:s?',
           '-c', 'copy', '-map_chapters', '0',
           '-metadata', f'title={meta_title}']
    info, _ = probe_title(iso, title, trim0=False)
    if not info:
        return False, 'title not readable'
    seen = {}
    ai = si = 0
    for s in info['streams']:
        ct = s.get('codec_type')
        if ct == 'audio':
            # Matroska cannot store pcm_dvd -> lossless FLAC instead
            if (s.get('codec_name') or '') in ('pcm_dvd', 'pcm_bluray'):
                cmd += ['-c:a:%d' % ai, 'flac']
            cmd += ['-metadata:s:a:%d' % ai, 'title=' + track_title(s, seen)]
            ai += 1
        elif ct == 'subtitle':
            cmd += ['-metadata:s:s:%d' % si, 'title=' + track_title(s, seen)]
            si += 1
    part = out_path + '.part'
    cmd += ['-f', 'matroska', part]
    try:
        p = run(cmd, timeout=6 * 3600)
    except subprocess.TimeoutExpired:
        _rm(part)
        return False, 'timeout'
    if p.returncode != 0:
        err = (p.stderr or '').strip().splitlines()
        _rm(part)
        return False, (err[-1] if err else 'ffmpeg error')
    ok, msg = verify(part, info, lenient=lenient)
    if not ok:
        _rm(part)
        return False, msg
    os.replace(part, out_path)
    return True, msg


def verify(path, src_info, lenient=False):
    try:
        p = run(['ffprobe', '-v', 'error', '-show_entries',
                 'stream=codec_type:format=duration', '-of', 'json', path], timeout=300)
        d = json.loads(p.stdout)
    except Exception as e:  # noqa: BLE001
        return False, f'verify error: {e}'
    dur = float(d.get('format', {}).get('duration') or 0)
    want = src_info['duration']
    n_src = len(src_info['streams'])
    n_out = len(d.get('streams', []))
    if n_out == 0:
        return False, 'output has no streams'
    if lenient:
        # Some discs report an IFO duration that differs from real playtime
        # (padding cells, trim). Accept if video exists and length is plausible.
        if not any(s.get('codec_type') == 'video' for s in d['streams']):
            return False, 'no video stream'
        if dur < 120:
            return False, f'too short ({dur:.0f}s)'
        return True, f'{dur:.0f}s ({dur - want:+.0f}s vs IFO), {n_out} streams'
    if n_src != n_out:
        return False, f'stream count {n_out} != {n_src}'
    if dur < want - 5 or dur > want + 5:
        return False, f'duration {dur:.1f}s != source {want:.1f}s'
    return True, f'{dur:.0f}s, {n_out} streams'


def _rm(p):
    try:
        os.remove(p)
    except OSError:
        pass


def find_isos(src):
    out = []
    for root, _dirs, files in os.walk(src):
        for f in sorted(files):
            if f.lower().endswith('.iso'):
                out.append(os.path.join(root, f))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(
        description='Copy DVD ISO titles to MKV without re-encoding.')
    ap.add_argument('--src', default='./ISOs', help='directory containing .iso files (recursive)')
    ap.add_argument('--dst', default='./MKV', help='output directory (structure is mirrored)')
    ap.add_argument('--min-len', type=float, default=120.0,
                    help='skip titles shorter than this many seconds (default: 120)')
    ap.add_argument('--only', default=None, help='regex filter on the ISO path')
    ap.add_argument('--dry-run', action='store_true', help='list titles, write nothing')
    ap.add_argument('--lenient', action='store_true',
                    help='tolerate duration mismatch vs. IFO (plausibility check only)')
    ap.add_argument('--report', default=None,
                    help='log file (default: <dst>/dvd2mkv_<timestamp>.log)')
    ap.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    args = ap.parse_args()

    if not os.path.isdir(args.src):
        sys.exit(f'error: source directory not found: {args.src}')

    t0 = time.time()
    report = args.report or os.path.join(
        args.dst, time.strftime('dvd2mkv_%Y%m%d_%H%M.log'))
    os.makedirs(os.path.dirname(report) or '.', exist_ok=True)
    os.makedirs(args.dst, exist_ok=True)
    logf = open(report, 'a', encoding='utf-8', buffering=1)

    def log(msg):
        line = f'[{time.strftime("%H:%M:%S")}] {msg}'
        print(line, flush=True)
        logf.write(line + '\n')

    isos = find_isos(args.src)
    if args.only:
        rx = re.compile(args.only)
        isos = [i for i in isos if rx.search(i)]
    log(f'START {len(isos)} ISOs  src={args.src}  dst={args.dst}  '
        f'min_len={args.min_len:.0f}s  dry_run={args.dry_run}')

    stats = {'isos': 0, 'titles': 0, 'done': 0, 'skipped_short': 0,
             'skipped_existing': 0, 'failed': 0, 'bytes': 0}
    failures = []

    for n, iso in enumerate(isos, 1):
        rel = os.path.relpath(iso, args.src)
        base = os.path.splitext(os.path.basename(iso))[0]
        outdir = os.path.join(args.dst, os.path.dirname(rel))
        os.makedirs(outdir, exist_ok=True)
        log(f'({n}/{len(isos)}) {rel}')
        try:
            titles = enumerate_titles(iso, log=log)
        except Exception as e:  # noqa: BLE001
            log(f'    ERROR while reading: {e}')
            failures.append((rel, 'enumerate', str(e)))
            stats['failed'] += 1
            continue
        stats['isos'] += 1
        keep = [t for t in titles if t['duration'] >= args.min_len]
        stats['skipped_short'] += len(titles) - len(keep)
        log(f'    {len(titles)} titles, {len(keep)} >= {args.min_len:.0f}s'
            + (f', {len(titles)-len(keep)} short/empty' if len(titles) > len(keep) else ''))
        if not keep:
            log('    nothing to do')
            continue
        multi = len(keep) > 1
        for i, t in enumerate(keep, 1):
            out = os.path.join(outdir, f'{base}_{i:02d}.mkv' if multi else f'{base}.mkv')
            if os.path.exists(out) and os.path.getsize(out) > 1 << 20:
                log(f'    [{t["title"]}] exists, skipped ({os.path.basename(out)})')
                stats['skipped_existing'] += 1
                continue
            stats['titles'] += 1
            if args.dry_run:
                log(f'    [{t["title"]}] {t["duration"]:.0f}s -> {os.path.basename(out)}')
                continue
            log(f'    [{t["title"]}] {t["duration"]:.0f}s -> {os.path.basename(out)} ...')
            ok, msg = rip(iso, t['title'], out, base, lenient=args.lenient)
            if ok:
                sz = os.path.getsize(out)
                stats['done'] += 1
                stats['bytes'] += sz
                log(f'    OK {sz/2**30:.2f} GiB ({msg})')
            else:
                stats['failed'] += 1
                failures.append((rel, t['title'], msg))
                log(f'    FAILED: {msg}')

    log(f'END  {((time.time()-t0)/60):.1f} min  '
        f'ISOs={stats["isos"]} titles={stats["titles"]} ok={stats["done"]} '
        f'existing={stats["skipped_existing"]} short={stats["skipped_short"]} '
        f'failed={stats["failed"]} written={stats["bytes"]/2**30:.1f} GiB')
    for f in failures:
        log(f'  FAILED {f[0]} title {f[1]}: {f[2]}')
    logf.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
