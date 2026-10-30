"""Validate a local secrets.h without printing credential values."""
import argparse
import re
from pathlib import Path


def validate(text):
    if "/*" in text or "*/" in text or "\\\n" in text or re.search(r"^\s*#\s*(?:if|ifdef|ifndef|elif|else|endif|undef|include)\b", text, re.M):
        return False
    fields = {}
    for name in ("RELAY_HOST", "RELAY_TLS_PORT", "KAI_PSK_IDENTITY", "KAI_PSK_HEX", "KAI_DEVICE_TOKEN"):
        matches = re.findall(r"^\s*#\s*define\s+" + name + r"\s+([^\r\n]+)$", text, re.M)
        if len(matches) != 1:
            return False
        fields[name] = re.sub(r"\s*//.*$", "", matches[0]).strip()
    def string(name):
        match = re.fullmatch(r'"([^"\\]*)"', fields[name])
        return match.group(1) if match else ""
    host, identity, key, token = (string(x) for x in ("RELAY_HOST", "KAI_PSK_IDENTITY", "KAI_PSK_HEX", "KAI_DEVICE_TOKEN"))
    labels = host.split(".")
    hostname = len(host) <= 253 and len(labels) >= 2 and all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels) and not all(label.isdigit() for label in labels) and labels[-1].lower() not in ("local", "example", "invalid", "test")
    def real(value):
        v = value.lower()
        return bool(value) and value == value.strip() and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) and not any(x in v for x in ("change-me", "changeme", "placeholder", "example", "your-", "your_", "replace_me", "xxxx")) and not set(value) <= {"0"}
    return bool(hostname and host == "jellii.k100.uk" and fields["RELAY_TLS_PORT"] == "443" and real(identity) and real(token) and re.fullmatch(r"[a-fA-F0-9]{64}", key) and len(set(key.lower())) > 1 and not any(key.lower() == pattern * (64 // len(pattern)) for pattern in ("0123456789abcdef", "123456789abcdef0", "deadbeef")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("secrets", type=Path)
    args = parser.parse_args()
    try:
        ok = validate(args.secrets.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        ok = False
    print("Preflash configuration: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
