"""Report independently built source variants without asserting binary equality."""
import hashlib
import os
import re
from pathlib import Path

rows = []
for tree, env, log in [('layered', 'sticks3', '/tmp/layered-sticks3.log'),
                       ('consolidated', 'sticks3', '/tmp/consolidated-sticks3.log'),
                       ('consolidated', 'sticks3_ble_diag', '/tmp/consolidated-diag.log')]:
    image = Path('/tmp/kai-compare') / tree / 'firmware/.pio/build' / env / 'firmware.bin'
    data = image.read_bytes()
    if len(data) > 3342336:
        raise SystemExit(f'{tree}/{env} image exceeds application partition')
    text = Path(log).read_text(encoding='utf8', errors='replace')
    values = []
    for label in ('Flash', 'RAM'):
        found = re.findall(rf'(?m)^{label}:\s+\[[^\]]*\]\s+[\d.]+%\s+\(used\s+([\d,]+) bytes from\s+([\d,]+) bytes\)', text)
        if not found:
            raise SystemExit(f'Missing {label} footprint in {log}')
        values.append(int(found[-1][0].replace(',', '')))
    rows.append((tree, env, len(data), *values, hashlib.sha256(data).hexdigest()))
lines = ['| Source | Environment | Image bytes | Flash bytes | RAM bytes | SHA-256 |',
         '| --- | --- | ---: | ---: | ---: | --- |']
for row in rows:
    lines.append('| ' + ' | '.join(map(str, row)) + ' |')
a, b = rows[:2]
lines.append(f'\nCandidate minus layered: image {b[2]-a[2]:+d} B, flash {b[3]-a[3]:+d} B, RAM {b[4]-a[4]:+d} B. Binary hash equality is not assumed.')
report = '\n'.join(lines) + '\n'
print(report)
if os.environ.get('GITHUB_STEP_SUMMARY'):
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf8') as out:
        out.write(report)
