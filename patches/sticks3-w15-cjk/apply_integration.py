#!/usr/bin/env python3
"""Adapt the unchanged CJK patch to the W1.5-enhanced face.cpp safely.

Run from the Amusant checkout; pass the cloned/patched Kai directory.
Do not use fuzzy patching: bail out if expected patch/context changes.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

HEADER = 'diff --git a/firmware/src/face.cpp b/firmware/src/face.cpp'
NEXT = 'diff --git a/firmware/src/jellii_text_utf8.h b/firmware/src/jellii_text_utf8.h'
FIRST_HUNK = '''@@ -1,4 +1,5 @@
 #include "face.h"
+#include "jellii_text_utf8.h"
 
 #include <vector>
 
'''
INCLUDE_OLD = '#include "face.h"\n\n#include <string.h>\n#include <vector>\n'
INCLUDE_NEW = '#include "face.h"\n#include "jellii_text_utf8.h"\n\n#include <string.h>\n#include <vector>\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kai', type=Path, required=True)
    parser.add_argument('--patches', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    kai = args.kai.resolve()
    p = args.patches.resolve() / 'kai-v0.12.0-cjk-display' / 'kai-v0.12.0-cjk-display.patch'
    source = (kai / 'firmware/src/face.cpp')
    original = source.read_text(encoding='utf-8')
    patch = p.read_text(encoding='utf-8')
    if not patch.startswith(HEADER + '\n') or patch.count(FIRST_HUNK) != 1 or patch.count(NEXT) != 1:
        raise SystemExit('Unexpected CJK patch structure: refuse automatic adaptation')
    if original.count(INCLUDE_OLD) != 1 or 'void drawProvisioning()' not in original:
        raise SystemExit('Unexpected W1.5 face.cpp context: refuse automatic adaptation')
    if '#include "jellii_text_utf8.h"' in original:
        raise SystemExit('CJK appears already applied: refuse duplicate integration')

    # Keep the two merged source patches unmodified. Only the now-obsolete
    # include hunk is adapted to coexist with W1.5's additional includes.
    modified = original.replace(INCLUDE_OLD, INCLUDE_NEW, 1)
    adapted = patch.replace(FIRST_HUNK, '', 1)
    source.write_text(modified, encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='w15-cjk-') as folder:
        patchfile = Path(folder) / 'adapted.patch'
        patchfile.write_text(adapted, encoding='utf-8')
        subprocess.run(['git', '-C', str(kai), 'apply', '--check', str(patchfile)], check=True)
        subprocess.run(['git', '-C', str(kai), 'apply', str(patchfile)], check=True)

    complete = source.read_text(encoding='utf-8')
    for symbol in ('drawProvisioning()', 'fonts::efontTW_14', 'jellii_text::wrap(',
                   'jellii_text::fitting_prefix(', 'jellii_text::prefix('):
        if symbol not in complete:
            raise SystemExit('Integrated display verification failed: ' + symbol)
    subprocess.run(['git', '-C', str(kai), 'diff', '--check'], check=True)
    print('W1.5 BLE + CJK integration applied; both display paths present')


if __name__ == '__main__':
    main()
