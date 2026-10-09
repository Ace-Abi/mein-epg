#!/usr/bin/env python3
"""Erstellt eine an private tvg-IDs angepasste öffentliche EPG-XMLTV-Datei.
Keine IPTV-Zugangsdaten erforderlich. Python 3, nur Standardbibliothek.
"""
import copy
import gzip
import io
import json
import re
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = [
    ('EPGShare Türkei', 'https://epgshare01.online/epgshare01/epg_ripper_TR1.xml.gz'),
    ('EPGShare Deutschland', 'https://epgshare01.online/epgshare01/epg_ripper_DE1.xml.gz'),
    ('EPGShare Österreich', 'https://epgshare01.online/epgshare01/epg_ripper_AT1.xml.gz'),
    ('IPTV-EPG Türkei', 'https://iptv-epg.org/files/epg-tr.xml.gz'),
    ('IPTV-EPG Deutschland', 'https://iptv-epg.org/files/epg-de.xml.gz'),
    ('IPTV-org Türkei TV+', 'https://iptv-org.github.io/epg/guides/tr/tvplus.com.tr.epg.xml'),
    ('IPTV-org Türkei Digiturk', 'https://iptv-org.github.io/epg/guides/tr/digiturk.com.tr.epg.xml'),
    ('IPTV-org Türkei D-Smart', 'https://iptv-org.github.io/epg/guides/tr/dsmart.com.tr.epg.xml'),
    ('IPTV-org Deutschland', 'https://iptv-org.github.io/epg/guides/de/hd-plus.de.epg.xml'),
    ('IPTV-org Österreich', 'https://iptv-org.github.io/epg/guides/at/tvheute.at.epg.xml'),
]

def norm(s):
    s = unicodedata.normalize('NFKD', s.casefold())
    return ''.join(c for c in s if c.isalnum() and not unicodedata.combining(c))


def load_xml(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 EPGHelper/1.0'})
    with urllib.request.urlopen(req, timeout=45) as response:
        contents = response.read(150_000_001)
    if len(contents) > 150_000_000:
        raise ValueError('EPG-Quelle zu groß')
    if contents[:2] == b'\x1f\x8b':
        contents = gzip.decompress(contents)
    if len(contents) > 400_000_000:
        raise ValueError('Entpacktes EPG zu groß')
    return ET.fromstring(contents)


def main():
    targets = json.loads((HERE/'sender_ids.json').read_text(encoding='utf-8'))
    aliases = json.loads((HERE/'aliases.json').read_text(encoding='utf-8'))
    target_map = {item['id']: item['name'] for item in targets}
    unresolved = set(target_map)
    mapped = {}  # target -> (source name, original ID)
    collected_channels = {}
    collected_programmes = defaultdict(list)
    status = []

    for source_name, url in SOURCES:
        if not unresolved:
            break
        try:
            tree = load_xml(url)
        except Exception as exc:
            status.append(f'NICHT ERREICHBAR: {source_name}: {exc}')
            print(status[-1], file=sys.stderr)
            continue
        channels = {ch.get('id'): ch for ch in tree.findall('channel') if ch.get('id')}
        if not channels:
            status.append(f'OHNE KANÄLE: {source_name}')
            continue
        lower = defaultdict(list)
        normalized = defaultdict(list)
        for ch_id in channels:
            lower[ch_id.casefold()].append(ch_id)
            normalized[norm(ch_id)].append(ch_id)

        newly = {}
        for dest in sorted(unresolved):
            matched = None
            # Zuerst konkrete ID. Falls sie fehlt, ausgewählte Synonyme verwenden.
            candidates = [dest] + aliases.get(dest, [])
            for candidate in candidates:
                if candidate in channels:
                    matched = candidate
                    break
                bycase = lower.get(candidate.casefold(), [])
                if len(bycase) == 1:
                    matched = bycase[0]
                    break
                bynorm = normalized.get(norm(candidate), [])
                if len(bynorm) == 1:
                    matched = bynorm[0]
                    break
            if matched is not None:
                newly[dest] = matched

        if newly:
            source_ids = defaultdict(list)
            for dest, orig in newly.items():
                source_ids[orig].append(dest)
                cloned = copy.deepcopy(channels[orig])
                cloned.set('id', dest)
                collected_channels[dest] = cloned
                mapped[dest] = (source_name, orig)
            for programme in tree.findall('programme'):
                cid = programme.get('channel')
                if cid in source_ids:
                    for dest in source_ids[cid]:
                        clone = copy.deepcopy(programme)
                        clone.set('channel', dest)
                        collected_programmes[dest].append(clone)
            unresolved.difference_update(newly)
        status.append(f'{source_name}: {len(newly)} neue Sender-IDs zugeordnet')
        print(status[-1])
        tree.clear()

    root = ET.Element('tv', {
        'generator-info-name': 'Persoenliches EPG-ID-Mapping (oeffentliche XMLTV-Quellen)',
        'date': datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S +0000'),
    })
    for dest in sorted(collected_channels):
        root.append(collected_channels[dest])
    for dest in sorted(collected_programmes):
        for programme in collected_programmes[dest]:
            root.append(programme)
    epg_bytes = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    if not collected_programmes:
        raise RuntimeError('Keine Sendungen gefunden. Vorhandene EPG-Datei wird nicht überschrieben.')
    (HERE/'epg.xml').write_bytes(epg_bytes)
    (HERE/'epg.xml.gz').write_bytes(gzip.compress(epg_bytes, compresslevel=6))
    report = [
        f'Stand UTC: {datetime.now(timezone.utc):%Y-%m-%d %H:%M}',
        f'Insgesamt: {len(targets)} unterschiedliche Sender-IDs',
        f'EPG gefunden: {len(mapped)} Sender-IDs',
        f'Nicht gefunden: {len(unresolved)} Sender-IDs',
        f'Programme: {sum(map(len, collected_programmes.values()))}',
        '', 'QUELLEN', *status, '', 'ZUGEORDNET (eigene ID <- Quell-ID)',
    ]
    report += [f'{dest} <- {orig} [{src}]' for dest,(src,orig) in sorted(mapped.items())]
    report += ['', 'OHNE ZUORDNUNG']
    report += [f'{dest} | {target_map[dest]}' for dest in sorted(unresolved)]
    (HERE/'zuordnung.txt').write_text('\n'.join(report)+'\n', encoding='utf-8')
    print(f'FERTIG: {len(mapped)} von {len(targets)} IDs; {sum(map(len,collected_programmes.values()))} Sendungen.')

if __name__ == '__main__':
    main()
