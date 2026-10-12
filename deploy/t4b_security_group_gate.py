"""Controller-side, read-only T4b Jellii security-group gate.

Checks the actual running service task ENI, not a proposed/template SG. This gate
has no mutating AWS calls and emits only sanitized check names/status.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import sys
from typing import Any, Mapping, Sequence


class GateFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GateReport:
    passed: bool
    checks: Mapping[str, str]
    error: str | None = None


def _task_eni_id(task: Mapping[str, Any], *, expected_service: str, expected_family: str,
                 actual_family: str) -> str:
    if task.get("lastStatus") != "RUNNING":
        raise GateFailure("jellii_task_not_running")
    if task.get("group") != f"service:{expected_service}":
        raise GateFailure("jellii_service_mismatch")
    if actual_family != expected_family:
        raise GateFailure("jellii_task_family_mismatch")
    eni_ids: list[str] = []
    for attachment in task.get("attachments") or []:
        if attachment.get("type") != "ElasticNetworkInterface" or attachment.get("status") != "ATTACHED":
            continue
        for detail in attachment.get("details") or []:
            if detail.get("name") == "networkInterfaceId" and isinstance(detail.get("value"), str):
                eni_ids.append(detail["value"])
    if len(eni_ids) != 1:
        raise GateFailure("jellii_task_eni_unavailable")
    return eni_ids[0]


def _allows_tcp_port(rule: Mapping[str, Any], port: int) -> bool:
    protocol = str(rule.get("IpProtocol", "")).lower()
    if protocol == "-1":
        return True
    if protocol not in {"6", "tcp"}:
        return False
    start, end = rule.get("FromPort"), rule.get("ToPort")
    if type(start) is not int or type(end) is not int:
        # Missing/ill-formed TCP port bounds cannot be treated as safe.
        return True
    return start <= port <= end


def _is_exact_relay_8443_rule(rule: Mapping[str, Any], relay_sg_id: str) -> bool:
    protocol = str(rule.get("IpProtocol", "")).lower()
    source = rule.get("ReferencedGroupInfo") or {}
    source_group_id = source.get("GroupId") if isinstance(source, Mapping) else None
    has_other_source = any(
        rule.get(name)
        for name in ("CidrIpv4", "CidrIpv6", "PrefixListId")
    )
    return (
        protocol in {"6", "tcp"}
        and rule.get("FromPort") == 8443
        and rule.get("ToPort") == 8443
        and source_group_id == relay_sg_id
        and not has_other_source
    )


def _validate_ingress_rules(rules: Sequence[Mapping[str, Any]], relay_sg_id: str) -> None:
    ingress: list[Mapping[str, Any]] = []
    for rule in rules:
        if "IsEgress" not in rule or type(rule["IsEgress"]) is not bool:
            raise GateFailure("security_group_rule_shape_unknown")
        if rule["IsEgress"] is False:
            ingress.append(rule)

    if any(_allows_tcp_port(rule, 8080) for rule in ingress):
        raise GateFailure("jellii_tcp_8080_ingress_present")

    grants_8443 = [rule for rule in ingress if _allows_tcp_port(rule, 8443)]
    if not grants_8443 or any(not _is_exact_relay_8443_rule(rule, relay_sg_id) for rule in grants_8443):
        raise GateFailure("jellii_tcp_8443_source_not_exclusive")


def verify_t4b_security_groups(
    ecs: Any,
    ec2: Any,
    *,
    cluster: str,
    task_arn: str,
    expected_service: str,
    expected_family: str,
    expected_relay_sg_id: str,
) -> GateReport:
    """Read-only check of the running Jellii task's effective attached SG ingress."""
    checks: dict[str, str] = {}
    try:
        response = ecs.describe_tasks(cluster=cluster, tasks=[task_arn])
        tasks = response.get("tasks") or []
        if response.get("failures") or len(tasks) != 1:
            raise GateFailure("jellii_task_lookup_failed")
        task = tasks[0]
        task_definition_arn = task.get("taskDefinitionArn")
        if not isinstance(task_definition_arn, str) or not task_definition_arn:
            raise GateFailure("jellii_task_definition_unavailable")
        definition = ecs.describe_task_definition(taskDefinition=task_definition_arn).get("taskDefinition") or {}
        eni_id = _task_eni_id(task, expected_service=expected_service,
                              expected_family=expected_family, actual_family=definition.get("family", ""))
        checks["jellii_task_identity"] = "pass"

        eni_response = ec2.describe_network_interfaces(NetworkInterfaceIds=[eni_id])
        enis = eni_response.get("NetworkInterfaces") or []
        if len(enis) != 1 or enis[0].get("NetworkInterfaceId") != eni_id or enis[0].get("Status") != "in-use":
            raise GateFailure("jellii_task_eni_lookup_failed")
        eni = enis[0]
        attached_groups = eni.get("Groups")
        if not isinstance(attached_groups, list) or not attached_groups or any(
            not isinstance(group, Mapping) or not isinstance(group.get("GroupId"), str)
            for group in attached_groups
        ):
            raise GateFailure("jellii_task_security_groups_invalid")
        attached = sorted({group["GroupId"] for group in attached_groups})
        if not attached or expected_relay_sg_id in attached:
            raise GateFailure("jellii_task_security_groups_invalid")

        all_group_ids = sorted(set(attached) | {expected_relay_sg_id})
        groups_response = ec2.describe_security_groups(GroupIds=all_group_ids)
        groups = groups_response.get("SecurityGroups") or []
        group_by_id = {group.get("GroupId"): group for group in groups}
        if set(group_by_id) != set(all_group_ids):
            raise GateFailure("security_group_lookup_incomplete")
        vpcs = {group.get("VpcId") for group in groups}
        if len(vpcs) != 1 or None in vpcs or eni.get("VpcId") not in vpcs:
            raise GateFailure("security_group_vpc_mismatch")
        checks["jellii_task_security_groups"] = "pass"

        rules: list[Mapping[str, Any]] = []
        next_token = None
        while True:
            kwargs: dict[str, Any] = {"Filters": [{"Name": "group-id", "Values": attached}]}
            if next_token:
                kwargs["NextToken"] = next_token
            page = ec2.describe_security_group_rules(**kwargs)
            page_rules = page.get("SecurityGroupRules")
            if not isinstance(page_rules, list):
                raise GateFailure("security_group_rules_unavailable")
            if any(
                not isinstance(rule, Mapping) or rule.get("GroupId") not in attached
                for rule in page_rules
            ):
                raise GateFailure("security_group_rules_unavailable")
            rules.extend(page_rules)
            next_token = page.get("NextToken")
            if not next_token:
                break
        _validate_ingress_rules(rules, expected_relay_sg_id)
        checks["tcp_8080_ingress_absent"] = "pass"
        checks["tcp_8443_only_from_expected_relay_sg"] = "pass"
        return GateReport(True, checks)
    except GateFailure as error:
        return GateReport(False, checks, error.code)
    except Exception:
        return GateReport(False, checks, "security_group_read_failed")


def _safe_print(report: GateReport) -> None:
    payload = {"status": "PASS" if report.passed else "FAIL", "checks": dict(report.checks)}
    if report.error:
        payload["error"] = report.error
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only T4b Jellii SG gate; no AWS mutations.")
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--task-arn", required=True)
    parser.add_argument("--expected-service", required=True)
    parser.add_argument("--expected-family", required=True)
    parser.add_argument("--expected-relay-sg-id", required=True)
    parser.add_argument("--region", required=True)
    args = parser.parse_args(argv)
    try:
        import boto3
        from botocore.config import Config

        config = Config(
            connect_timeout=3,
            read_timeout=5,
            retries={"total_max_attempts": 2, "mode": "standard"},
            ignore_configured_endpoint_urls=True,
        )
        ecs = boto3.client("ecs", region_name=args.region, config=config)
        ec2 = boto3.client("ec2", region_name=args.region, config=config)
        report = verify_t4b_security_groups(
            ecs, ec2, cluster=args.cluster, task_arn=args.task_arn,
            expected_service=args.expected_service, expected_family=args.expected_family,
            expected_relay_sg_id=args.expected_relay_sg_id,
        )
    except Exception:
        report = GateReport(False, {}, "security_group_read_failed")
    _safe_print(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
