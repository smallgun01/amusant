#!/usr/bin/env python3
"""Ubuntu ARM64 host preparation only. Default: read-only plan; never SSH/sudo."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import urllib.request

sys.dont_write_bytecode = True  # Dynamic preflight import must keep planning read-only.

ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "deploy/oci-a1/preflight.py"
EXAMPLE = ROOT / "deploy/oci-a1/.env.example"
PROJECT = "amusant-oci-a1"
PACKAGES = ("docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin")
CONFLICTS = ("docker.io", "podman-docker", "containerd", "runc")
DIRECTORIES = (("relay", 10001, 10001, 0o700), ("hermes", 10000, 10000, 0o700),
               ("results", 0, 0, 0o755), ("results/facade", 10003, 10003, 0o700))
DOCKER = ("docker", "--host", "unix:///var/run/docker.sock")
KEY_URL = "https://download.docker.com/linux/ubuntu/gpg"
KEY = Path("/etc/apt/keyrings/docker.asc")
SOURCE = Path("/etc/apt/sources.list.d/docker.sources")
MIN_FREE_MIB = 1024  # Bootstrap floor only; not a runtime/image capacity claim.
SAFE_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C",
            "HOME": "/root", "DEBIAN_FRONTEND": "noninteractive"}


class Blocked(RuntimeError):
    """Safe reason code only: never forward command output or private inputs."""


def command(*args: str, ok=(0,), timeout=30) -> str:
    executable = next((str(Path(p) / args[0]) for p in SAFE_ENV["PATH"].split(":")
                       if (Path(p) / args[0]).is_file()), None)
    if executable is None:
        raise Blocked("REQUIRED_COMMAND_UNAVAILABLE")
    try:
        result = subprocess.run([executable, *args[1:]], env=SAFE_ENV, text=True,
                                capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        raise Blocked("COMMAND_UNAVAILABLE_OR_TIMEOUT") from None
    if result.returncode not in ok:
        raise Blocked("COMMAND_FAILED_OR_PERMISSION_DENIED")
    return result.stdout


def json_command(*args: str):
    try:
        return json.loads(command(*args))
    except (TypeError, ValueError):
        raise Blocked("INVALID_HOST_COMMAND_OUTPUT") from None


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def no_symlinks(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise Blocked("UNSAFE_PATH")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise Blocked("SYMLINK_PATH_REJECTED")


def trusted(path: Path, *, private=False) -> None:
    """Apply inputs/code must be root-owned, without writable ancestors."""
    no_symlinks(path)
    for part in (path, *path.parents):
        info = part.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise Blocked("ROOT_OWNED_REVIEWED_PATH_REQUIRED")
    if private and stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise Blocked("REVIEWED_PLAN_MODE_0600_REQUIRED")


def load_preflight():
    spec = importlib.util.spec_from_file_location("oci_a1_preflight", PREFLIGHT)
    if not spec or not spec.loader:
        raise Blocked("EXISTING_PREFLIGHT_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_text(codename: str) -> str:
    if codename not in ("jammy", "noble"):
        raise Blocked("UNREVIEWED_UBUNTU_RELEASE")
    return ("Types: deb\nURIs: https://download.docker.com/linux/ubuntu\n"
            f"Suites: {codename}\nComponents: stable\nArchitectures: arm64\n"
            "Signed-By: /etc/apt/keyrings/docker.asc\n")


def mounted(path: Path, *, exact=False) -> dict:
    try:
        result = json_command("findmnt", "--json", "--mountpoint" if exact else "--target",
                              str(path), "--output", "TARGET,FSTYPE,UUID,OPTIONS,MAJ:MIN")
    except Blocked:
        raise Blocked("EXPECTED_DATA_MOUNT_UNAVAILABLE" if exact else "ROOT_MOUNT_IDENTITY_UNAVAILABLE") from None
    entries = result.get("filesystems", [])
    if len(entries) != 1:
        raise Blocked("MOUNT_IDENTITY_UNAVAILABLE")
    return entries[0]


def backing_disks(tree: list, device: str, parents=()) -> set[str]:
    found: set[str] = set()
    for node in tree:
        ancestors = (*parents, node["maj:min"]) if node["type"] == "disk" else parents
        if node["maj:min"] == device:
            found.update(ancestors)
        found.update(backing_disks(node.get("children", []), device, ancestors))
    return found


def dir_metadata(path: Path):
    no_symlinks(path)
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        raise Blocked("DATA_PATH_NOT_DIRECTORY")
    return {"uid": info.st_uid, "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode),
            "device": info.st_dev, "inode": info.st_ino}


def observe(inputs: dict) -> dict:
    """Read host metadata only. Application .env/config/secret contents are not logged."""
    os_release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
                      if "=" in line and not line.startswith("#"))
    os_info = {key: os_release.get(key, "").strip('"') for key in ("ID", "VERSION_ID", "VERSION_CODENAME")}
    os_info["arch"] = command("uname", "-m").strip()
    data = Path(inputs["data_dir"])
    no_symlinks(data)
    root_mount, data_mount = mounted(Path("/")), mounted(data, exact=True)
    disks = json_command("lsblk", "--json", "--output", "NAME,TYPE,MAJ:MIN")["blockdevices"]
    root_disks = backing_disks(disks, root_mount["maj:min"])
    data_disks = backing_disks(disks, data_mount["maj:min"])
    package_output = command("dpkg-query", "-W", "-f=${binary:Package}\t${db:Status-Status}\t${Version}\n")
    packages = {}
    for line in package_output.splitlines():
        name, status, version = line.split("\t")
        if status == "installed":
            packages[name.split(":", 1)[0]] = version
    docker = {"cli": False, "compose": "", "server": "", "networks": [], "containers": []}
    # Package state and binary presence must agree. A denied daemon is never "absent".
    if Path("/usr/bin/docker").is_file():
        docker["cli"] = True
        command(*DOCKER, "--version")
        docker["compose"] = command(*DOCKER, "compose", "version", "--short", ok=(0, 1)).strip()
        docker["active"] = command("systemctl", "is-active", "docker", ok=(0, 3)).strip() == "active"
        docker["enabled"] = command("systemctl", "is-enabled", "docker", ok=(0, 1)).strip() == "enabled"
        if docker["active"]:
            try:
                docker["server"] = json_command(*DOCKER, "info", "--format", "{{json .ServerVersion}}")
            except Blocked:
                raise Blocked("DOCKER_DAEMON_PERMISSION_OR_HEALTH_UNVERIFIED") from None
            ids = command(*DOCKER, "network", "ls", "--quiet").split()
            if ids:
                networks = json_command(*DOCKER, "network", "inspect", *ids)
                docker["networks"] = [{"id": n["Id"], "driver": n["Driver"], "labels": n.get("Labels") or {},
                                       "options": n.get("Options") or {},
                                       "subnets": [c.get("Subnet", "") for c in (n.get("IPAM", {}).get("Config") or [])]}
                                      for n in networks]
            docker["containers"] = command(*DOCKER, "container", "ls", "--all", "--quiet").split()
    else:
        docker.update(active=False, enabled=False)
        if "docker-ce" in packages or Path("/var/lib/docker").exists():
            raise Blocked("EXISTING_DOCKER_STATE_REQUIRES_REVIEW")
    if not docker["active"] and Path("/var/lib/docker").exists():
        # A stopped daemon cannot tell us which unrelated workloads would auto-restart.
        if any(Path("/var/lib/docker").iterdir()):
            raise Blocked("STOPPED_DOCKER_STATE_REQUIRES_SEPARATE_REVIEW")
    addresses = json_command("ip", "-json", "-4", "address", "show")
    routes = json_command("ip", "-json", "-4", "route", "show", "table", "all")
    preflight = load_preflight()
    env_path = Path(inputs["env_file"]) if inputs["env_file"] else EXAMPLE
    no_symlinks(env_path)
    values = preflight.read_env_file(env_path)
    static_status = "DEFERRED_B3_INPUTS_INCOMPLETE"
    if all(values.get(key) for key in preflight.REQUIRED):
        preflight.validate(values)
        static_status = "STATIC_INPUTS_VALID_ONLY"
    networks = {key: values[key] for key in preflight.NETWORK_NAMES}
    # Reuse existing preflight's network predicate; do not build a second validator.
    for key in networks:
        preflight._network(values, key)
    source_state = {str(p): digest(p) if p.exists() else None for p in (KEY, SOURCE)}
    for p in (KEY, SOURCE):
        no_symlinks(p)
        if p.exists():
            trusted(p)
    extra_sources = []
    for pattern in ("*.list", "*.sources"):
        for p in Path("/etc/apt/sources.list.d").glob(pattern):
            if p != SOURCE and "download.docker.com" in p.read_text():
                extra_sources.append(p.name)
    fs = os.statvfs(data)
    return {"os": os_info, "mount": data_mount, "root_mount": root_mount,
            "root_disks": sorted(root_disks), "data_disks": sorted(data_disks),
            "data_root": dir_metadata(data),
            "directories": {rel: dir_metadata(data / rel) for rel, *_ in DIRECTORIES},
            "packages": {p: packages[p] for p in (*PACKAGES, *CONFLICTS) if p in packages},
            "apt_status": digest(Path("/var/lib/dpkg/status")),
            "docker": docker, "networks": networks, "static_status": static_status,
            "addresses": [{"name": a["ifname"], "ips": sorted(i["local"] for i in a.get("addr_info", []))}
                          for a in addresses],
            "routes": [{k: r.get(k, "") for k in ("dst", "dev", "gateway", "table", "type")} for r in routes],
            "apt_files": source_state, "extra_docker_sources": sorted(extra_sources),
            "free_mib": fs.f_bavail * fs.f_frsize // (1024 * 1024),
            "source_valid": not SOURCE.exists() or SOURCE.read_text() == source_text(os_info["VERSION_CODENAME"]),
            "env_digest": digest(env_path)}


def validate_inputs(inputs: dict) -> None:
    if set(inputs) != {"data_dir", "expected_uuid", "env_file", "packages"}:
        raise Blocked("INVALID_PLAN_INPUTS")
    path = Path(inputs["data_dir"])
    no_symlinks(path)
    if path == Path("/") or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", inputs["expected_uuid"]):
        raise Blocked("EXPLICIT_DATA_MOUNT_AND_UUID_REQUIRED")
    if not isinstance(inputs["packages"], dict):
        raise Blocked("INVALID_PACKAGE_SELECTION")
    for name, version in inputs["packages"].items():
        if name not in PACKAGES or not re.fullmatch(r"[0-9][A-Za-z0-9.+:~_-]*", version):
            raise Blocked("INVALID_PACKAGE_SELECTION")


def plan(inputs: dict, facts: dict) -> dict:
    validate_inputs(inputs)
    reasons, actions = [], []
    os_info = facts["os"]
    if (os_info["ID"], os_info["VERSION_ID"], os_info["VERSION_CODENAME"], os_info["arch"]) not in (
            ("ubuntu", "22.04", "jammy", "aarch64"), ("ubuntu", "24.04", "noble", "aarch64")):
        reasons.append("UNREVIEWED_OS_OR_ARCH")
    mount = facts["mount"]
    if (mount["target"] != inputs["data_dir"] or mount["fstype"] != "ext4"
            or (mount.get("uuid") or "").lower() != inputs["expected_uuid"].lower()
            or "rw" not in mount["options"].split(",")
            or not facts["root_disks"] or not facts["data_disks"]
            or set(facts["root_disks"]) & set(facts["data_disks"])):
        reasons.append("WRONG_OR_NONPERSISTENT_DATA_MOUNT")
    data_root = facts["data_root"]
    if not data_root or data_root["uid"] != 0 or data_root["mode"] & 0o022:
        reasons.append("DATA_ROOT_MUST_BE_ROOT_OWNED_NOT_WRITABLE")
    if facts["free_mib"] < MIN_FREE_MIB:
        reasons.append("INSUFFICIENT_BOOTSTRAP_DISK_SPACE")
    for rel, uid, gid, mode in DIRECTORIES:
        existing = facts["directories"][rel]
        if existing is None:
            actions.append({"op": "mkdir", "relative": rel, "uid": uid, "gid": gid, "mode": mode})
        elif (existing["uid"], existing["gid"], existing["mode"], existing["device"]) != (uid, gid, mode, data_root["device"]):
            reasons.append("EXISTING_DIRECTORY_METADATA_MISMATCH:" + rel)
    docker, packages = facts["docker"], facts["packages"]
    missing = [p for p in PACKAGES if p not in packages]
    if docker["cli"] and docker["compose"]:
        missing = []  # A usable existing install is never upgraded/replaced.
    elif docker["cli"] and "docker-ce" not in packages:
        reasons.append("EXISTING_NON_CE_DOCKER_REQUIRES_REVIEW")
    if missing:
        if any(p in packages for p in CONFLICTS) or facts["extra_docker_sources"] or not facts["source_valid"]:
            reasons.append("EXISTING_PACKAGE_OR_APT_SOURCE_CONFLICT")
        if docker["containers"]:
            reasons.append("PACKAGE_CHANGE_WITH_EXISTING_CONTAINERS_REQUIRES_REVIEW")
        if any(p not in inputs["packages"] for p in missing):
            reasons.append("EXPLICIT_MISSING_PACKAGE_VERSIONS_REQUIRED")
        else:
            actions.insert(0, {"op": "install_docker", "packages": {p: inputs["packages"][p] for p in missing}})
    if not docker["active"] or not docker["enabled"]:
        actions.insert(1 if missing else 0, {"op": "enable_docker"})
    if docker["active"] and not docker["server"]:
        reasons.append("DOCKER_DAEMON_ACCESS_UNVERIFIED")
    if docker["compose"] and not re.fullmatch(r"v?(?:[2-9]|[1-9][0-9]+)\.[0-9]+\.[0-9]+(?:[-+].*)?", docker["compose"]):
        reasons.append("UNSUPPORTED_COMPOSE_VERSION")
    configured = {name: ipaddress.ip_network(cidr) for name, cidr in facts["networks"].items()}
    if any(a.overlaps(b) for i, a in enumerate(configured.values()) for b in list(configured.values())[i+1:]):
        reasons.append("COMPOSE_SUBNETS_OVERLAP")
    own_routes = set()
    for net in docker["networks"]:
        label = net["labels"].get("com.docker.compose.network", "").upper() + "_CIDR"
        own = net["driver"] == "bridge" and net["labels"].get("com.docker.compose.project") == PROJECT
        bridge = net["options"].get("com.docker.network.bridge.name", "br-" + net["id"][:12])
        for cidr in net["subnets"]:
            actual = ipaddress.ip_network(cidr)
            if actual.version != 4:
                continue
            for name, expected in configured.items():
                if actual.overlaps(expected):
                    if own and label == name and actual == expected:
                        own_routes.add((cidr, bridge))
                    else:
                        reasons.append("FOREIGN_DOCKER_NETWORK_CONFLICT")
    for route in facts["routes"]:
        dst = route["dst"]
        if dst in ("", "default", "0.0.0.0/0"):
            continue
        actual = ipaddress.ip_network(dst, strict=False)
        if any(actual.overlaps(net) for net in configured.values()):
            # Own bridge connected/local routes are expected on a repeat run.
            if not any(route["dev"] == bridge and actual.subnet_of(ipaddress.ip_network(cidr))
                       for cidr, bridge in own_routes):
                reasons.append("HOST_ROUTE_CONFLICT")
    docker_bridges = {"docker0", *(n["options"].get("com.docker.network.bridge.name", "br-" + n["id"][:12])
                                  for n in docker["networks"] if n["driver"] == "bridge")}
    rfc1918 = load_preflight().RFC1918_NETWORKS
    if not any(any(ipaddress.ip_address(ip) in private for private in rfc1918)
               for a in facts["addresses"] if a["name"] not in docker_bridges
               for ip in a["ips"]):
        reasons.append("PRIVATE_HOST_ADDRESS_UNAVAILABLE")
    # Free space changes during normal operation; recheck its floor, not exact byte count.
    stable = {key: value for key, value in facts.items() if key != "free_mib"}
    return {"schema": 1, "inputs": inputs, "facts": stable, "actions": actions,
            "blocked": sorted(set(reasons)), "code": {"bootstrap": digest(Path(__file__)), "preflight": digest(PREFLIGHT)},
            "requires_privileged_apply": True}


def create_file(path: Path, content: bytes, mode: int) -> None:
    """Atomic no-clobber publication; preserve all pre-existing files."""
    fd, temporary = tempfile.mkstemp(prefix=".amusant-bootstrap-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        os.unlink(temporary)


def make_directory(data: Path, action: dict) -> None:
    """Root-owned ancestors plus no-follow fd operations; never recursive chown."""
    path = data / action["relative"]
    trusted(path.parent)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.mkdir(path.name, 0o700, dir_fd=parent)
        fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            os.fchmod(fd, action["mode"])
            os.fchown(fd, action["uid"], action["gid"])
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def install_docker(packages: dict, codename: str) -> None:
    if not KEY.parent.exists():
        trusted(KEY.parent.parent)
        KEY.parent.mkdir(mode=0o755)
    trusted(KEY.parent)
    trusted(SOURCE.parent)
    if not KEY.exists():
        with urllib.request.urlopen(KEY_URL, timeout=30) as response:
            body = response.read(65537)
            if (response.geturl() != KEY_URL or len(body) > 65536
                    or not body.startswith(b"-----BEGIN PGP PUBLIC KEY BLOCK-----")
                    or b"-----END PGP PUBLIC KEY BLOCK-----" not in body):
                raise Blocked("DOCKER_SIGNING_KEY_DOWNLOAD_INVALID")
        create_file(KEY, body, 0o644)
    if not SOURCE.exists():
        create_file(SOURCE, source_text(codename).encode(), 0o644)
    command("apt-get", "update", timeout=300)
    selection = [f"{p}={v}" for p, v in sorted(packages.items())]
    flags = ("--no-install-recommends", "--no-remove", "--no-upgrade")
    simulation = command("apt-get", "--simulate", *flags, "install", *selection, timeout=120)
    if any(line.startswith("Remv ") or re.match(r"^Inst \S+ \[", line)
           for line in simulation.splitlines()):
        raise Blocked("APT_WOULD_REMOVE_OR_UPGRADE_EXISTING_PACKAGES")
    command("apt-get", "--yes", *flags, "install", *selection, timeout=900)


def apply(reviewed: dict, plan_file: Path) -> None:
    if os.geteuid() != 0:
        raise Blocked("EXPLICIT_PRIVILEGED_EXECUTION_REQUIRED")
    if not sys.flags.isolated:
        raise Blocked("ISOLATED_PYTHON_REQUIRED_USE_PYTHON3_I")
    for path in (Path(__file__).resolve(), PREFLIGHT, EXAMPLE):
        trusted(path)
    trusted(plan_file, private=True)
    # JSON is data, never a shell program. Recompute the allowed actions from live facts.
    inputs = reviewed["inputs"]
    validate_inputs(inputs)
    trusted(Path(inputs["data_dir"]))
    facts = observe(inputs)
    current = plan(inputs, facts)
    if current != reviewed:
        raise Blocked("PLAN_STALE_OR_TAMPERED_REPLAN_REQUIRED")
    if current["blocked"]:
        raise Blocked("PLAN_HAS_BLOCKERS")
    completed, phase = 0, "postcheck"
    try:
        for action in current["actions"]:
            phase = action["op"]
            # Revalidate disk identity immediately before *every* mutation, including apt.
            if mounted(Path(inputs["data_dir"]), exact=True) != facts["mount"]:
                raise Blocked("DATA_MOUNT_CHANGED_DURING_APPLY")
            if action["op"] == "install_docker":
                install_docker(action["packages"], facts["os"]["VERSION_CODENAME"])
            elif action["op"] == "enable_docker":
                command("systemctl", "enable", "--now", "docker", timeout=120)
            else:
                make_directory(Path(inputs["data_dir"]), action)
            completed += 1
        phase = "postcheck"
        after_facts = observe(inputs)
        for action in current["actions"]:
            if action["op"] == "install_docker" and any(after_facts["packages"].get(p) != v
                                                         for p, v in action["packages"].items()):
                raise Blocked("INSTALLED_PACKAGE_VERSION_MISMATCH")
        after = plan(inputs, after_facts)
        if after["blocked"] or after["actions"]:
            raise Blocked("POSTCHECK_INCOMPLETE_REPLAN_REQUIRED")
    except Exception:
        print(f"Completed host actions: {completed}. Failed phase: {phase} (may be partial). "
              "Preserve state; inspect and replan before retry.", file=sys.stderr)
        raise
    print("HOST_PREPARED; backend not started; B2/B3 and live acceptance remain pending.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--plan", action="store_true")
    modes.add_argument("--apply", action="store_true")
    parser.add_argument("--data-dir")
    parser.add_argument("--expected-fs-uuid")
    parser.add_argument("--env-file", default="")
    parser.add_argument("--package", action="append", default=[], metavar="NAME=VERSION")
    parser.add_argument("--plan-out", type=Path)
    parser.add_argument("--plan-file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.apply:
            if os.geteuid() != 0:
                raise Blocked("EXPLICIT_PRIVILEGED_EXECUTION_REQUIRED")
            if not args.plan_file or args.data_dir or args.expected_fs_uuid or args.package or args.env_file or args.plan_out:
                raise Blocked("APPLY_ACCEPTS_ONLY_REVIEWED_PLAN_FILE")
            trusted(args.plan_file, private=True)
            apply(json.loads(args.plan_file.read_text()), args.plan_file)
            return 0
        if args.plan_file or not args.data_dir or not args.expected_fs_uuid:
            raise Blocked("PLAN_REQUIRES_DATA_DIR_AND_EXPECTED_UUID")
        packages = {}
        for entry in args.package:
            name, separator, version = entry.partition("=")
            if not separator or name in packages:
                raise Blocked("INVALID_OR_DUPLICATE_PACKAGE_SELECTION")
            packages[name] = version
        inputs = {"data_dir": args.data_dir, "expected_uuid": args.expected_fs_uuid,
                  "env_file": args.env_file, "packages": packages}
        validate_inputs(inputs)
        facts = observe(inputs)
        result = plan(inputs, facts)
        if args.plan_out:
            no_symlinks(args.plan_out)
            create_file(args.plan_out, (json.dumps(result, indent=2, sort_keys=True) + "\n").encode(), 0o600)
        print("BLOCKED" if result["blocked"] else "PLAN_READY_FOR_REVIEW")
        print(f"Host actions: {len(result['actions'])}; free data MiB: {facts['free_mib']}; {facts['static_status']}")
        for action in result["actions"]:
            print("Proposed:", action["op"], json.dumps({k: v for k, v in action.items() if k != "op"}, sort_keys=True))
        for reason in result["blocked"]:
            print("Reason:", reason)
        print("No host changes made. Private plan contains host metadata; do not post it to Git/logs.")
        return 2 if result["blocked"] else 0
    except Blocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
    except Exception:
        print("BLOCKED: HOST_INPUT_OR_OPERATION_UNAVAILABLE; preserve state and replan.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
