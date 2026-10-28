"""Exercise the exact narrowly scoped outer-diff whitespace check."""
import subprocess
import tempfile
from pathlib import Path


def git(root, *args, check=True):
    return subprocess.run(['git', '-C', str(root), *args], check=check, capture_output=True)


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    git(root, 'init', '-q')
    git(root, 'config', 'user.email', 'test@example.invalid')
    git(root, 'config', 'user.name', 'CI test')
    (root / 'ordinary.txt').write_text('clean\n', encoding='utf8')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'base')
    base = git(root, 'rev-parse', 'HEAD').stdout.decode().strip()
    path = root / 'patches/amusant-sticks3-base-v0.12.0.patch'
    path.parent.mkdir()
    path.write_text('diff --git a/x b/x\n \n', encoding='utf8')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'patch context')
    assert git(root, 'diff', '--check', base, 'HEAD', '--', check=False).returncode != 0
    args = ('diff', '--check', base, 'HEAD', '--', '.', ':(exclude)patches/amusant-sticks3-base-v0.12.0.patch')
    assert git(root, *args, check=False).returncode == 0
    (root / 'ordinary.txt').write_text('bad \n', encoding='utf8')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'ordinary whitespace')
    assert git(root, *args, check=False).returncode != 0
print('PASS: embedded patch context excluded; ordinary whitespace still rejected')
