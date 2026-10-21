#!/usr/bin/env python3
"""Offline, fail-closed Doctor and Safe Setup for Compose-based self hosting."""
from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_REQUIRED = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\s*:\?")
IMAGE_REF = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")


def run_command(argv: list[str], *, cwd: Path | None = None) -> tuple[int, str, str]:
    """Run local inspection commands only; callers decide how output is reported."""
    try:
        result = subprocess.run(
            argv, cwd=cwd, text=True, capture_output=True, check=False, timeout=15
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
        return 127, "", ""
    return result.returncode, result.stdout, result.stderr


def _rooted_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("profile is unavailable or invalid JSON") from None
    required = {
        "name", "target_os", "target_architecture", "env_file", "env_example",
        "compose_file", "preflight_file", "data_root_env", "expected_source_env",
        "host_ip_env", "external_network_cidrs_env", "network_envs", "image_envs",
        "secret_files", "config_files", "data_directories",
    }
    if not isinstance(value, dict) or not required.issubset(value):
        raise ValueError("profile is missing required fields")
    for entry in value["data_directories"]:
        relative = Path(entry.get("path", ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("profile contains an unsafe relative data path")
    return value


def _load_preflight(path: Path):
    try:
        spec = importlib.util.spec_from_file_location("selected_profile_preflight", path)
        if spec is None or spec.loader is None:
            raise ImportError
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _mode_is_private(mode: int) -> bool:
    return (mode & 0o077) == 0 and (mode & 0o111) == 0


def _read_env(path: Path, preflight) -> tuple[dict[str, str], str | None, bool]:
    """Return values in memory only; never include values or paths in diagnostics."""
    if _has_symlink_component(path):
        return {}, "environment file path contains a symlink", False
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {}, "environment file is missing", False
    except OSError:
        return {}, "environment file metadata is unavailable", False
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return {}, "environment file must be a regular non-symlink file", False
    if not _mode_is_private(stat.S_IMODE(metadata.st_mode)):
        return {}, "environment file permissions are not private", False
    if metadata.st_uid not in {os.geteuid(), 0}:
        return {}, "environment file owner is unexpected", False
    if preflight is None:
        return {}, "profile static preflight module is unavailable", False
    try:
        values = preflight.read_env_file(path)
    except Exception:
        return {}, "environment file could not be parsed; values were not displayed", False
    return values, None, True


def _required_variables(profile: dict, preflight, compose_path: Path) -> set[str]:
    names = set(profile.get("required_env", []))
    if preflight is not None:
        names.update(getattr(preflight, "REQUIRED", ()))
    try:
        text = compose_path.read_text(encoding="utf-8")
        names.update(COMPOSE_REQUIRED.findall(text))
    except OSError:
        pass
    names.add(profile["data_root_env"])
    names.add(profile["expected_source_env"])
    return names


def _is_empty(value: str | None) -> bool:
    return value is None or not value.strip()


def _load_mount(path: Path) -> dict | None:
    code, stdout, _ = run_command([
        "findmnt", "--json", "--target", str(path), "--output", "SOURCE,TARGET,FSTYPE"
    ])
    if code != 0:
        return None
    try:
        filesystems = json.loads(stdout).get("filesystems", [])
        item = filesystems[0]
        return {key.lower(): item.get(key.lower()) or item.get(key.upper()) for key in ("source", "target", "fstype")}
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def _canonical_source(value: str) -> str:
    value = value.strip()
    return os.path.realpath(value) if value.startswith("/") else value


def _device_id(path: Path) -> int | None:
    try:
        return path.stat().st_dev
    except OSError:
        return None


def _storage_state(profile: dict, values: dict[str, str], checks: list[dict]) -> tuple[bool, Path | None]:
    root_value = values.get(profile["data_root_env"], "").strip()
    if not root_value:
        checks.append({"status": "BLOCKED", "name": "persistent data mount", "detail": "data-root input is missing"})
        return False, None
    root = Path(root_value)
    if not root.is_absolute():
        checks.append({"status": "BLOCKED", "name": "persistent data mount", "detail": "data root must be an absolute path"})
        return False, None
    if _has_symlink_component(root):
        checks.append({"status": "BLOCKED", "name": "persistent data mount", "detail": "configured data root path contains a symlink"})
        return False, root
    try:
        metadata = root.lstat()
    except OSError:
        checks.append({"status": "BLOCKED", "name": "persistent data mount", "detail": "configured data root does not exist"})
        return False, root
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        checks.append({"status": "BLOCKED", "name": "persistent data mount", "detail": "configured data root is not a real directory"})
        return False, root
    mount = _load_mount(root)
    if not mount or not mount.get("source") or not mount.get("target"):
        checks.append({"status": "BLOCKED", "name": "persistent data mount", "detail": "mount identity could not be verified; an empty directory is insufficient"})
        return False, root
    try:
        root_resolved = root.resolve(strict=True)
        target_resolved = Path(str(mount["target"])).resolve(strict=True)
    except OSError:
        checks.append({"status": "BLOCKED", "name": "persistent data mount", "detail": "mount target could not be resolved"})
        return False, root
    if root_resolved != target_resolved:
        checks.append({"status": "BLOCKED", "name": "persistent data mount", "detail": "data root is not itself the mounted target"})
        return False, root
    if profile.get("require_separate_device", True):
        root_dev, data_dev = _device_id(Path("/")), _device_id(root)
        if root_dev is None or data_dev is None or root_dev == data_dev:
            checks.append({"status": "BLOCKED", "name": "persistent data mount", "detail": "data root is not verified on a separate filesystem"})
            return False, root
    expected = values.get(profile["expected_source_env"], "").strip()
    if not expected:
        checks.append({"status": "BLOCKED", "name": "persistent data mount", "detail": "expected persistent-volume identity is missing"})
        return False, root
    if _canonical_source(str(mount["source"])) != _canonical_source(expected):
        checks.append({"status": "BLOCKED", "name": "persistent data mount", "detail": "mounted source does not match the operator-supplied identity"})
        return False, root
    checks.append({"status": "PASS", "name": "persistent data mount", "detail": "mountpoint and operator-supplied source identity match"})
    return True, root


def _has_symlink_component(path: Path) -> bool:
    current = path.absolute()
    for component in reversed((current, *current.parents)):
        try:
            if stat.S_ISLNK(component.lstat().st_mode):
                return True
        except FileNotFoundError:
            continue
        except OSError:
            return True
    return False


def _inspect_dir(path: Path) -> tuple[str, os.stat_result | None]:
    if _has_symlink_component(path):
        return "symlink", None
    try:
        item = path.lstat()
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unavailable", None
    if stat.S_ISLNK(item.st_mode):
        return "symlink", item
    if not stat.S_ISDIR(item.st_mode):
        return "not_directory", item
    return "directory", item


def _check_data_directories(profile: dict, root: Path | None, storage_ok: bool, checks: list[dict]) -> None:
    if root is None or not storage_ok:
        for entry in profile["data_directories"]:
            checks.append({"status": "BLOCKED", "name": "data directory " + entry["label"], "detail": "not inspected because persistent mount identity is unverified"})
        return
    for entry in profile["data_directories"]:
        path = root / entry["path"]
        state, item = _inspect_dir(path)
        name = "data directory " + entry["label"]
        if state == "missing":
            checks.append({"status": "BLOCKED", "name": name, "detail": "required directory is absent; setup may plan it after mount verification"})
        elif state != "directory" or item is None:
            checks.append({"status": "BLOCKED", "name": name, "detail": "path is unsafe or unavailable; it will not be changed automatically"})
        else:
            mode = stat.S_IMODE(item.st_mode)
            expected_mode = int(entry["mode"], 8)
            expected_uid = entry.get("uid")
            expected_gid = entry.get("gid")
            owner_matches = (expected_uid is None or item.st_uid == expected_uid) and (expected_gid is None or item.st_gid == expected_gid)
            if not owner_matches or mode != expected_mode:
                checks.append({"status": "BLOCKED", "name": name, "detail": "owner, group, or mode differs from the profile; existing data will not be changed"})
            else:
                checks.append({"status": "PASS", "name": name, "detail": "directory owner and mode match the profile"})


def _stat_file(path: Path) -> tuple[str, os.stat_result | None]:
    if _has_symlink_component(path):
        return "symlink", None
    try:
        item = path.lstat()
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unavailable", None
    if stat.S_ISLNK(item.st_mode):
        return "symlink", item
    if not stat.S_ISREG(item.st_mode):
        return "not_regular", item
    return "regular", item


def _secret_mode_ok(item: os.stat_result, entry: dict) -> bool:
    mode = stat.S_IMODE(item.st_mode)
    consumer_uids = entry.get("consumer_uids", [])
    if len(consumer_uids) == 1 and mode == 0o400 and item.st_uid == consumer_uids[0]:
        return True
    shared_gid = entry.get("shared_gid")
    return bool(entry.get("allow_shared_group")) and item.st_uid == 0 and item.st_gid == shared_gid and mode == 0o440


def _readable_by(item: os.stat_result, uid: int, gids: list[int]) -> bool:
    mode = stat.S_IMODE(item.st_mode)
    if item.st_uid == uid:
        return bool(mode & stat.S_IRUSR)
    if item.st_gid in gids:
        return bool(mode & stat.S_IRGRP)
    return bool(mode & stat.S_IROTH)


def _check_config_and_secrets(profile: dict, values: dict[str, str], checks: list[dict]) -> bool:
    all_ok = True
    for entry in profile["secret_files"]:
        value = values.get(entry["env"], "").strip()
        name = "secret-file metadata " + entry["env"]
        if not value:
            checks.append({"status": "BLOCKED", "name": name, "detail": "secret file path is missing; no secret contents are read"})
            all_ok = False
            continue
        state, item = _stat_file(Path(value))
        if state != "regular" or item is None:
            checks.append({"status": "BLOCKED", "name": name, "detail": "path must be a regular non-symlink file; contents are not read"})
            all_ok = False
        elif not _secret_mode_ok(item, entry):
            checks.append({"status": "BLOCKED", "name": name, "detail": "file ownership or mode does not match the runtime policy"})
            all_ok = False
        else:
            checks.append({"status": "PASS", "name": name, "detail": "regular file and runtime ownership/mode match; contents not read"})
    for entry in profile["config_files"]:
        value = values.get(entry["env"], "").strip()
        name = "configuration-file metadata " + entry["env"]
        if not value:
            checks.append({"status": "BLOCKED", "name": name, "detail": "configuration path is missing; contents are not read"})
            all_ok = False
            continue
        state, item = _stat_file(Path(value))
        if state != "regular" or item is None:
            checks.append({"status": "BLOCKED", "name": name, "detail": "path must be a regular non-symlink file; contents are not read"})
            all_ok = False
            continue
        no_writes = not (stat.S_IMODE(item.st_mode) & 0o022)
        if not no_writes or not _readable_by(item, entry["reader_uid"], entry["reader_gids"]):
            checks.append({"status": "BLOCKED", "name": name, "detail": "configuration owner, mode, or readability is unsafe"})
            all_ok = False
        else:
            checks.append({"status": "PASS", "name": name, "detail": "regular read-only configuration is accessible to the service; contents not read"})
    return all_ok


def _get_local_networks() -> tuple[set, bool]:
    code, stdout, _ = run_command(["ip", "-j", "address", "show"])
    if code != 0:
        return set(), False
    networks = set()
    try:
        for interface in json.loads(stdout):
            for address in interface.get("addr_info", []):
                local = address.get("local")
                prefix = address.get("prefixlen")
                if local is not None and prefix is not None:
                    networks.add(ipaddress.ip_network(f"{local}/{prefix}", strict=False))
    except (ValueError, TypeError):
        return set(), False
    code, stdout, _ = run_command(["ip", "-j", "route", "show"])
    if code != 0:
        return networks, False
    try:
        for route in json.loads(stdout):
            destination = route.get("dst")
            if not destination or destination == "default":
                continue
            try:
                networks.add(ipaddress.ip_network(destination, strict=False))
            except ValueError:
                continue
    except (ValueError, TypeError):
        return networks, False
    return networks, True


def _check_networks(profile: dict, values: dict[str, str], extra_network_cidrs: list[str], checks: list[dict]) -> None:
    docker_networks = []
    for name in profile["network_envs"]:
        value = values.get(name, "").strip()
        if not value:
            continue
        try:
            docker_networks.append((name, ipaddress.ip_network(value, strict=True)))
        except ValueError:
            checks.append({"status": "BLOCKED", "name": "Docker network CIDR " + name, "detail": "configured CIDR is invalid"})
    for index, (name, network) in enumerate(docker_networks):
        for other_name, other in docker_networks[index + 1:]:
            if network.overlaps(other):
                checks.append({"status": "BLOCKED", "name": "Docker network overlap", "detail": name + " conflicts with " + other_name})
    supplied_values = [value.strip() for value in values.get(profile["external_network_cidrs_env"], "").split(",") if value.strip()]
    supplied_values.extend(extra_network_cidrs)
    supplied_networks = []
    for value in supplied_values:
        try:
            supplied_networks.append(ipaddress.ip_network(value, strict=True))
        except ValueError:
            checks.append({"status": "BLOCKED", "name": "operator-supplied external network CIDR", "detail": "CIDR input is invalid"})
    host_networks, routes_known = _get_local_networks()
    if not routes_known:
        checks.append({"status": "UNKNOWN", "name": "host route overlap", "detail": "local route inventory is unavailable"})
    else:
        conflicts = [(name, network) for name, network in docker_networks if any(network.overlaps(route) for route in host_networks)]
        if conflicts:
            checks.append({"status": "BLOCKED", "name": "host route overlap", "detail": "one or more Docker networks overlap a local interface or route"})
        else:
            checks.append({"status": "PASS", "name": "host route overlap", "detail": "no conflict with observed local interface/route CIDRs"})
    if not supplied_networks:
        checks.append({"status": "UNKNOWN", "name": "external network overlap", "detail": "no operator-supplied external CIDR; not inferred from local routes"})
    else:
        conflicts = [(name, network) for name, network in docker_networks if any(network.overlaps(external) for external in supplied_networks)]
        if conflicts:
            checks.append({"status": "BLOCKED", "name": "external network overlap", "detail": "a Docker network overlaps an operator-supplied external CIDR"})
        else:
            checks.append({"status": "PASS", "name": "external network overlap", "detail": "no conflict with supplied external CIDRs"})


def _check_host_ip(profile: dict, values: dict[str, str], checks: list[dict]) -> None:
    value = values.get(profile["host_ip_env"], "").strip()
    if not value:
        checks.append({"status": "BLOCKED", "name": "private Relay bind address", "detail": "configured host address is missing"})
        return
    try:
        target = ipaddress.ip_address(value)
    except ValueError:
        checks.append({"status": "BLOCKED", "name": "private Relay bind address", "detail": "configured host address is invalid"})
        return
    code, stdout, _ = run_command(["ip", "-j", "address", "show"])
    if code != 0:
        checks.append({"status": "UNKNOWN", "name": "private Relay bind address", "detail": "local interfaces could not be inspected"})
        return
    try:
        found = any(
            ipaddress.ip_address(info["local"]) == target
            for interface in json.loads(stdout)
            for info in interface.get("addr_info", [])
            if info.get("local")
        )
    except (ValueError, KeyError, TypeError):
        checks.append({"status": "UNKNOWN", "name": "private Relay bind address", "detail": "local interface output could not be interpreted"})
        return
    if found:
        checks.append({"status": "PASS", "name": "private Relay bind address", "detail": "configured private address exists on a local interface; address redacted"})
    else:
        checks.append({"status": "BLOCKED", "name": "private Relay bind address", "detail": "configured address is not assigned to a local interface; address redacted"})


def _check_docker(profile: dict, values: dict[str, str], root: Path, env_file: Path, env_complete: bool, static_ok: bool, files_ok: bool, checks: list[dict]) -> None:
    docker = shutil.which("docker")
    if not docker:
        checks.append({"status": "BLOCKED", "name": "Docker Engine CLI", "detail": "Docker is unavailable; Doctor does not install it"})
        checks.append({"status": "BLOCKED", "name": "Docker Compose", "detail": "Compose validation cannot run without Docker Compose"})
        checks.append({"status": "UNKNOWN", "name": "existing container status", "detail": "Docker status could not be queried"})
        checks.append({"status": "UNKNOWN", "name": "static Compose config", "detail": "not run because Docker Compose is unavailable"})
        return
    code, _, _ = run_command([docker, "--version"], cwd=root)
    checks.append({"status": "PASS" if code == 0 else "BLOCKED", "name": "Docker Engine CLI", "detail": "local version command succeeded" if code == 0 else "local Docker command failed"})
    compose_code, _, _ = run_command([docker, "compose", "version"], cwd=root)
    compose_ok = compose_code == 0
    checks.append({"status": "PASS" if compose_ok else "BLOCKED", "name": "Docker Compose", "detail": "local version command succeeded" if compose_ok else "Compose plugin is unavailable"})
    code, stdout, _ = run_command([docker, "ps", "--all", "--format", "{{.State}}"], cwd=root)
    if code != 0:
        checks.append({"status": "UNKNOWN", "name": "existing container status", "detail": "read-only Docker listing failed; output redacted"})
    else:
        states = [line.strip() for line in stdout.splitlines() if line.strip()]
        checks.append({"status": "PASS", "name": "existing container status", "detail": f"read-only listing succeeded; {len(states)} containers observed, names omitted"})
    if not compose_ok:
        checks.append({"status": "BLOCKED", "name": "static Compose config", "detail": "Compose validation is unavailable"})
    elif not env_complete or not static_ok or not files_ok:
        checks.append({"status": "UNKNOWN", "name": "static Compose config", "detail": "not run until environment and file checks are complete"})
    else:
        compose_file = _rooted_path(root, profile["compose_file"])
        code, _, _ = run_command([docker, "compose", "--env-file", str(env_file), "-f", str(compose_file), "config", "--quiet"], cwd=root)
        checks.append({"status": "PASS" if code == 0 else "BLOCKED", "name": "static Compose config", "detail": "offline config validation succeeded; no services were started" if code == 0 else "offline config validation failed; details redacted"})


def inspect(
    profile_path: Path,
    env_override: Path | None = None,
    extra_network_cidrs: list[str] | None = None,
    *,
    root: Path | None = None,
) -> dict:
    root = root or ROOT
    profile_path = _rooted_path(root, str(profile_path))
    profile = _load_json(profile_path)
    env_file = _rooted_path(root, str(env_override or profile["env_file"]))
    preflight_path = _rooted_path(root, profile["preflight_file"])
    compose_path = _rooted_path(root, profile["compose_file"])
    preflight = _load_preflight(preflight_path)
    checks: list[dict] = []
    if preflight is None:
        checks.append({"status": "UNKNOWN", "name": "profile static preflight reuse", "detail": "existing preflight module could not be loaded"})
    values, env_error, env_file_ok = _read_env(env_file, preflight)
    checks.append({"status": "PASS" if env_error is None else "BLOCKED", "name": "environment file", "detail": "private regular file read without displaying values" if env_error is None else env_error})
    required = _required_variables(profile, preflight, compose_path)
    missing = sorted(name for name in required if _is_empty(values.get(name)))
    env_complete = env_file_ok and not missing
    if missing:
        checks.append({"status": "BLOCKED", "name": "environment completeness", "detail": "missing values for: " + ", ".join(missing)})
    elif env_file_ok:
        checks.append({"status": "PASS", "name": "environment completeness", "detail": "required values are present; values were not displayed"})
    else:
        checks.append({"status": "BLOCKED", "name": "environment completeness", "detail": "cannot validate values because the environment file is unavailable"})
    static_ok = False
    if preflight is not None and env_complete:
        try:
            preflight.validate(values)
            checks.append({"status": "PASS", "name": "existing profile static preflight", "detail": "existing preflight validation passed; no cloud calls were made"})
            static_ok = True
        except Exception as exc:
            checks.append({"status": "BLOCKED", "name": "existing profile static preflight", "detail": str(exc)})
    elif preflight is not None:
        checks.append({"status": "UNKNOWN", "name": "existing profile static preflight", "detail": "not run because required environment values are incomplete"})
    for name in profile["image_envs"]:
        value = values.get(name, "").strip()
        if not value:
            checks.append({"status": "BLOCKED", "name": "image digest syntax " + name, "detail": "image reference is missing"})
        elif IMAGE_REF.fullmatch(value):
            checks.append({"status": "PASS", "name": "image digest syntax " + name, "detail": "immutable digest syntax is valid; provenance/platform remain UNKNOWN"})
        else:
            checks.append({"status": "BLOCKED", "name": "image digest syntax " + name, "detail": "image reference is not pinned by a sha256 digest"})
    checks.append({"status": "UNKNOWN", "name": "image provenance and platform", "detail": "not verified; Doctor performs no registry calls or image pulls"})
    target_os = profile["target_os"].lower()
    if platform.system().lower() == target_os:
        checks.append({"status": "PASS", "name": "host operating system", "detail": "host OS matches selected profile"})
    else:
        checks.append({"status": "BLOCKED", "name": "host operating system", "detail": "host OS does not match selected profile"})
    machine = platform.machine().lower()
    expected_arch = profile["target_architecture"].lower()
    aliases = {"arm64": "aarch64", "x86_64": "amd64"}
    if aliases.get(machine, machine) == aliases.get(expected_arch, expected_arch):
        checks.append({"status": "PASS", "name": "host architecture", "detail": "host architecture matches selected profile"})
    else:
        checks.append({"status": "BLOCKED", "name": "host architecture", "detail": "host architecture does not match selected profile"})
    storage_ok, data_root = _storage_state(profile, values, checks)
    _check_data_directories(profile, data_root, storage_ok, checks)
    files_ok = _check_config_and_secrets(profile, values, checks)
    _check_host_ip(profile, values, checks)
    _check_networks(profile, values, extra_network_cidrs or [], checks)
    _check_docker(profile, values, root, env_file, env_complete, static_ok, files_ok, checks)
    return {"profile": profile, "root": root, "env_file": env_file, "values": values, "data_root": data_root, "storage_ok": storage_ok, "checks": checks}


def _can_create(uid: int, gid: int) -> bool:
    if os.geteuid() == 0:
        return True
    return os.geteuid() == uid and gid in {os.getegid(), *os.getgroups()}


def _template_is_safe(path: Path, profile: dict, preflight) -> bool:
    state, item = _stat_file(path)
    if state != "regular" or item is None:
        return False
    try:
        values = preflight.read_env_file(path) if preflight is not None else {}
    except Exception:
        return False
    return all(not values.get(name, "").strip() for name in profile.get("secret_template_envs", []))


def setup_plan(context: dict) -> tuple[list[dict], list[str]]:
    profile = context["profile"]
    root = context["root"]
    env_file = context["env_file"]
    operations: list[dict] = []
    blockers: list[str] = []
    env_state, env_stat = _stat_file(env_file)
    if env_state == "missing":
        template = _rooted_path(root, profile["env_example"])
        preflight = _load_preflight(_rooted_path(root, profile["preflight_file"]))
        if _template_is_safe(template, profile, preflight):
            operations.append({"kind": "copy_env", "target": env_file})
        else:
            blockers.append("non-secret environment template is missing or unsafe")
    elif (
        env_state == "regular" and env_stat is not None
        and _mode_is_private(stat.S_IMODE(env_stat.st_mode))
        and env_stat.st_uid in {os.geteuid(), 0}
        and os.access(env_file, os.R_OK)
    ):
        operations.append({"kind": "preserve_env", "target": env_file})
    else:
        blockers.append("existing environment target is unsafe; it will not be overwritten")
    if not context["storage_ok"] or context["data_root"] is None:
        blockers.append("data directories are not planned until the mount identity is verified")
        return operations, blockers
    base = context["data_root"]
    data_blockers_before = len(blockers)
    for entry in profile["data_directories"]:
        target = base / entry["path"]
        state, item = _inspect_dir(target)
        label = entry["label"]
        if state == "directory" and item is not None:
            mode = stat.S_IMODE(item.st_mode)
            if item.st_uid == entry["uid"] and item.st_gid == entry["gid"] and mode == int(entry["mode"], 8):
                operations.append({"kind": "preserve_dir", "target": target, "label": label})
            else:
                blockers.append(label + " already exists with mismatched ownership/mode; it will not be changed")
        elif state == "missing":
            uid = entry.get("uid")
            gid = entry.get("gid")
            uid = os.geteuid() if uid is None else uid
            gid = os.getegid() if gid is None else gid
            if _can_create(uid, gid):
                operations.append({"kind": "create_dir", "target": target, "label": label, "uid": uid, "gid": gid, "mode": int(entry["mode"], 8)})
            else:
                blockers.append(label + " needs a caller with the specified UID/GID; no sudo will be invoked")
        else:
            blockers.append(label + " is a symlink, non-directory, or unavailable; it will not be changed")
    if len(blockers) > data_blockers_before:
        operations = [item for item in operations if item["kind"] != "create_dir"]
    return operations, blockers


def _print_setup_plan(operations: list[dict], blockers: list[str]) -> None:
    print("Safe Setup plan (dry-run; no changes made):")
    for operation in operations:
        if operation["kind"] == "copy_env":
            print("  PLAN copy the non-secret .env.example template to .env with mode 0600; never overwrite")
        elif operation["kind"] == "create_dir":
            print(f"  PLAN create <data root>/{operation['label']} with profile ownership/mode")
        elif operation["kind"] == "preserve_dir":
            print(f"  PASS preserve existing {operation['label']} directory unchanged")
        elif operation["kind"] == "preserve_env":
            print("  PASS preserve existing .env unchanged")
    for blocker in blockers:
        print("  BLOCKED " + blocker)
    if not operations and not blockers:
        print("  No local changes are required.")


def _apply_operations(context: dict, operations: list[dict]) -> list[str]:
    applied: list[str] = []
    for operation in operations:
        kind = operation["kind"]
        target: Path = operation["target"]
        if kind in {"preserve_env", "preserve_dir"}:
            continue
        if kind == "copy_env":
            template = _rooted_path(context["root"], context["profile"]["env_example"])
            try:
                source = template.read_bytes()
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(source)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(target, 0o600)
            except FileExistsError:
                raise RuntimeError(".env appeared during setup; existing file was preserved") from None
            except OSError:
                raise RuntimeError("could not create non-secret .env template") from None
            applied.append("created .env template")
            continue
        if kind == "create_dir":
            try:
                target.mkdir(mode=operation["mode"], parents=False, exist_ok=False)
                if os.geteuid() == 0:
                    os.chown(target, operation["uid"], operation["gid"])
                elif os.geteuid() != operation["uid"] or operation["gid"] not in {os.getegid(), *os.getgroups()}:
                    raise PermissionError
                os.chmod(target, operation["mode"])
            except FileExistsError:
                raise RuntimeError(operation["label"] + " appeared during setup; it was not modified") from None
            except OSError:
                raise RuntimeError(operation["label"] + " could not be created with profile ownership/mode") from None
            applied.append("created " + operation["label"])
    return applied


def _next_safe_step(check: dict) -> str:
    name = check["name"]
    if name in {"persistent data mount"}:
        return "verify the mounted source against owner-approved volume evidence; do not create data directories yet."
    if name == "environment completeness":
        return "fill the named missing fields in the private .env, then rerun Doctor; never paste values into chat."
    if name == "environment file":
        return "review the private .env file type, owner and mode; Doctor will not display its values."
    if name.startswith("secret-file metadata"):
        return "review the named file's type and runtime ownership/mode; do not print or regenerate its contents."
    if name.startswith("configuration-file metadata"):
        return "review the config file's type and read permissions without printing its contents."
    if name == "private Relay bind address":
        return "confirm the private bind address is assigned to a local interface; no network changes were made."
    if name == "external network overlap":
        return "supply owner-approved external CIDRs; Doctor does not query cloud APIs or infer network ranges."
    if name == "host route overlap":
        return "review the reported route conflict or provide local route evidence; no routes were changed."
    if name.startswith("image digest syntax"):
        return "pin the image reference to a sha256 digest; actual provenance/platform remains a separate review."
    if name == "image provenance and platform":
        return "verify image provenance and platform from approved evidence; Doctor makes no registry calls."
    if name in {"Docker Engine CLI", "Docker Compose"}:
        return "review Docker/Compose installation as a separate approved step; Doctor did not install or start anything."
    if name == "static Compose config":
        return "resolve the local Compose input errors and rerun Doctor; config validation does not start services."
    if name == "existing container status":
        return "make Docker status inspectable, then rerun Doctor; it will only list current state."
    if name == "host architecture" or name == "host operating system":
        return "use a host matching the selected profile; no compatibility outside this profile is claimed."
    if name.startswith("data directory"):
        return "review owner/mode for this empty-state directory; existing data and permissions are never rewritten."
    return "inspect this local gate and rerun Doctor; no host, cloud or provider changes were made."


def render_doctor(context: dict) -> int:
    checks = context["checks"]
    print(f"Self-Hosting Doctor — profile {context['profile']['name']}")
    for check in checks:
        print(f"{check['status']:<7} {check['name']}: {check['detail']}")
    next_check = next((item for item in checks if item["status"] == "BLOCKED"), None)
    if next_check is None:
        next_check = next((item for item in checks if item["status"] == "UNKNOWN"), None)
    if next_check is None:
        print("Next safe step: review separate owner-approved E3 gates; Doctor is not deployment acceptance.")
    else:
        print("Next safe step: " + _next_safe_step(next_check))
    print("Decision: NOT deployment GO; real image provenance, cloud control-plane state, backup/restore, ingress and live acceptance are separate gates.")
    return 1 if any(check["status"] == "BLOCKED" for check in checks) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path, help="profile JSON; target-specific values stay in the profile/input")
    parser.add_argument("--env-file", type=Path, help="override the profile env file")
    parser.add_argument("--external-network-cidr", action="append", default=[], help="operator-supplied external CIDR; may be repeated")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="read-only checks")
    setup_parser = subparsers.add_parser("setup", help="safe local setup plan; dry-run by default")
    setup_parser.add_argument("--apply", action="store_true", help="ask for interactive confirmation before safe local changes")
    args = parser.parse_args(argv)
    try:
        context = inspect(args.profile, args.env_file, args.external_network_cidr)
    except ValueError as exc:
        print("BLOCKED profile: " + str(exc), file=sys.stderr)
        return 2
    if args.command == "doctor":
        return render_doctor(context)
    operations, blockers = setup_plan(context)
    _print_setup_plan(operations, blockers)
    if not args.apply:
        return 1 if blockers else 0
    writable = [item for item in operations if item["kind"] not in {"preserve_env", "preserve_dir"}]
    if not writable:
        print("Apply refused: there are no verified safe changes to make.")
        return 2 if blockers else 0
    if blockers:
        print("Only the listed safe operations may be applied; blocked data operations remain untouched.")
    if not sys.stdin.isatty():
        print("Apply refused: an interactive terminal is required.")
        return 2
    try:
        answer = input("Type APPLY SAFE to apply only the listed changes: ")
    except (EOFError, OSError):
        print("Apply refused: confirmation was unavailable.")
        return 2
    if answer != "APPLY SAFE":
        print("Apply cancelled; no changes made.")
        return 1
    # Recompute before writing to avoid acting on stale mount/path observations.
    refreshed = inspect(args.profile, args.env_file, args.external_network_cidr)
    refreshed_operations, refreshed_blockers = setup_plan(refreshed)
    if refreshed_blockers != blockers or [(item["kind"], str(item["target"])) for item in refreshed_operations] != [(item["kind"], str(item["target"])) for item in operations]:
        print("Apply refused: inputs changed after the plan; review a fresh dry-run.")
        return 2
    try:
        applied = _apply_operations(refreshed, refreshed_operations)
    except RuntimeError as exc:
        print("Apply stopped safely: " + str(exc))
        return 2
    for item in applied:
        print("APPLIED " + item)
    if blockers:
        print("Remaining blockers are unchanged; no deployment readiness is claimed.")
    else:
        print("Safe Setup finished; no services or containers were started and no credentials were created.")
    return 2 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
