from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import unittest

from deploy.t4b_security_group_gate import GateReport, _safe_print, verify_t4b_security_groups


class FakeEcs:
    def __init__(self, *, service="jellii", family="jellii-prod", status="RUNNING"):
        self.task = {
            "lastStatus": status,
            "group": f"service:{service}",
            "taskDefinitionArn": "arn:aws:ecs:region:account:task-definition/jellii-prod:9",
            "attachments": [{
                "type": "ElasticNetworkInterface",
                "status": "ATTACHED",
                "details": [{"name": "networkInterfaceId", "value": "eni-private"}],
            }],
        }
        self.family = family

    def describe_tasks(self, **kwargs):
        return {"tasks": [self.task], "failures": []}

    def describe_task_definition(self, **kwargs):
        return {"taskDefinition": {"family": self.family}}


class FakeEc2:
    def __init__(self, rules=None, *, vpc="vpc-private"):
        self.rules = list(rules if rules is not None else [{
            "GroupId": "sg-jellii-a",
            "IsEgress": False,
            "IpProtocol": "6",
            "FromPort": 8443,
            "ToPort": 8443,
            "ReferencedGroupInfo": {"GroupId": "sg-relay"},
        }])
        self.vpc = vpc

    def describe_network_interfaces(self, **kwargs):
        return {"NetworkInterfaces": [{
            "NetworkInterfaceId": "eni-private",
            "Status": "in-use",
            "VpcId": self.vpc,
            "Groups": [{"GroupId": "sg-jellii-a"}, {"GroupId": "sg-jellii-b"}],
        }]}

    def describe_security_groups(self, **kwargs):
        return {"SecurityGroups": [
            {"GroupId": group_id, "VpcId": self.vpc}
            for group_id in kwargs["GroupIds"]
        ]}

    def describe_security_group_rules(self, **kwargs):
        return {"SecurityGroupRules": self.rules}


def _verify(ec2=None, ecs=None):
    return verify_t4b_security_groups(
        ecs or FakeEcs(), ec2 or FakeEc2(), cluster="private-cluster",
        task_arn="task-arn", expected_service="jellii", expected_family="jellii-prod",
        expected_relay_sg_id="sg-relay",
    )


class T4bSecurityGroupGateTests(unittest.TestCase):
    def test_passes_only_when_actual_task_sgs_exclude_8080_and_8443_is_relay_only(self):
        report = _verify()
        self.assertTrue(report.passed)
        self.assertEqual(report.checks["tcp_8080_ingress_absent"], "pass")
        self.assertEqual(report.checks["tcp_8443_only_from_expected_relay_sg"], "pass")

    def test_detects_8080_ingress_even_if_relay_egress_could_block_probe(self):
        rules = [
            {"GroupId": "sg-jellii-a", "IsEgress": False, "IpProtocol": "6", "FromPort": 8443, "ToPort": 8443,
             "ReferencedGroupInfo": {"GroupId": "sg-relay"}},
            {"GroupId": "sg-jellii-b", "IsEgress": False, "IpProtocol": "6", "FromPort": 8080, "ToPort": 8080,
             "CidrIpv4": "10.0.0.0/8"},
        ]
        report = _verify(ec2=FakeEc2(rules))
        self.assertFalse(report.passed)
        self.assertEqual(report.error, "jellii_tcp_8080_ingress_present")

    def test_rejects_8443_from_cidr_or_unexpected_sg(self):
        for rule in (
            {"GroupId": "sg-jellii-a", "IsEgress": False, "IpProtocol": "6", "FromPort": 8443, "ToPort": 8443,
             "CidrIpv4": "10.0.0.0/8"},
            {"GroupId": "sg-jellii-a", "IsEgress": False, "IpProtocol": "6", "FromPort": 8443, "ToPort": 8443,
             "ReferencedGroupInfo": {"GroupId": "sg-other"}},
        ):
            with self.subTest(rule=rule):
                report = _verify(ec2=FakeEc2([rule]))
                self.assertFalse(report.passed)
                self.assertEqual(report.error, "jellii_tcp_8443_source_not_exclusive")

    def test_rejects_all_protocol_rule_and_task_identity_mismatch(self):
        broad = {"GroupId": "sg-jellii-a", "IsEgress": False, "IpProtocol": "-1"}
        report = _verify(ec2=FakeEc2([broad]))
        self.assertFalse(report.passed)
        self.assertEqual(report.error, "jellii_tcp_8080_ingress_present")

        report = _verify(ecs=FakeEcs(service="wrong"))
        self.assertFalse(report.passed)
        self.assertEqual(report.error, "jellii_service_mismatch")

    def test_rejects_every_non_8443_ingress_across_attached_groups(self):
        allowed = {
            "GroupId": "sg-jellii-a",
            "IsEgress": False,
            "IpProtocol": "6",
            "FromPort": 8443,
            "ToPort": 8443,
            "ReferencedGroupInfo": {"GroupId": "sg-relay"},
        }
        unexpected_rules = [
            {"GroupId": "sg-jellii-b", "IsEgress": False, "IpProtocol": "6", "FromPort": 22, "ToPort": 22, "CidrIpv4": "0.0.0.0/0"},
            {"GroupId": "sg-jellii-b", "IsEgress": False, "IpProtocol": "6", "FromPort": 9000, "ToPort": 9000, "CidrIpv4": "10.0.0.0/8"},
            {"GroupId": "sg-jellii-b", "IsEgress": False, "IpProtocol": "17", "FromPort": 53, "ToPort": 53, "CidrIpv4": "0.0.0.0/0"},
            {"GroupId": "sg-jellii-b", "IsEgress": False, "IpProtocol": "-1", "CidrIpv4": "10.0.0.0/8"},
            {"GroupId": "sg-jellii-b", "IsEgress": False, "IpProtocol": "6", "FromPort": 9000, "ToPort": 9000, "ReferencedGroupInfo": {"GroupId": "sg-other"}},
            {"GroupId": "sg-jellii-b", "IsEgress": False, "IpProtocol": "6", "FromPort": 9000, "ToPort": 9000, "CidrIpv6": "::/0"},
            {"GroupId": "sg-jellii-b", "IsEgress": False, "IpProtocol": "6", "FromPort": 9000, "ToPort": 9000, "PrefixListId": "pl-private"},
        ]
        for stray in unexpected_rules:
            with self.subTest(stray=stray):
                report = _verify(ec2=FakeEc2([allowed, stray]))
                self.assertFalse(report.passed)
                if stray.get("FromPort") == 8080 or stray.get("IpProtocol") == "-1":
                    self.assertEqual(report.error, "jellii_tcp_8080_ingress_present")
                else:
                    self.assertEqual(report.error, "jellii_unexpected_ingress_rule")

    def test_egress_is_not_rejected_as_ingress(self):
        allowed = {
            "GroupId": "sg-jellii-a",
            "IsEgress": False,
            "IpProtocol": "6",
            "FromPort": 8443,
            "ToPort": 8443,
            "ReferencedGroupInfo": {"GroupId": "sg-relay"},
        }
        egress = {"GroupId": "sg-jellii-a", "IsEgress": True, "IpProtocol": "-1"}
        report = _verify(ec2=FakeEc2([allowed, egress]))
        self.assertTrue(report.passed)

    def test_pagination_is_followed_for_all_attached_groups(self):
        class PaginatedEc2(FakeEc2):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def describe_security_group_rules(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return {"SecurityGroupRules": [], "NextToken": "next"}
                return {"SecurityGroupRules": self.rules}

        ec2 = PaginatedEc2()
        report = _verify(ec2=ec2)
        self.assertTrue(report.passed)
        self.assertEqual(ec2.calls, 2)

    def test_rejects_task_vpc_mismatch_and_unrelated_rule_response(self):
        class WrongVpcEc2(FakeEc2):
            def describe_network_interfaces(self, **kwargs):
                response = super().describe_network_interfaces(**kwargs)
                response["NetworkInterfaces"][0]["VpcId"] = "vpc-other"
                return response

        report = _verify(ec2=WrongVpcEc2())
        self.assertFalse(report.passed)
        self.assertEqual(report.error, "security_group_vpc_mismatch")

        class UnrelatedRuleEc2(FakeEc2):
            def describe_security_group_rules(self, **kwargs):
                return {"SecurityGroupRules": [{
                    "GroupId": "sg-not-attached",
                    "IsEgress": False,
                    "IpProtocol": "6",
                    "FromPort": 8443,
                    "ToPort": 8443,
                    "ReferencedGroupInfo": {"GroupId": "sg-relay"},
                }]}

        report = _verify(ec2=UnrelatedRuleEc2())
        self.assertFalse(report.passed)
        self.assertEqual(report.error, "security_group_rules_unavailable")

    def test_output_is_sanitized(self):
        out = io.StringIO()
        report = GateReport(False, {"jellii_task_identity": "pass"}, "jellii_tcp_8080_ingress_present")
        with redirect_stdout(out):
            _safe_print(report)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["status"], "FAIL")
        self.assertNotIn("task-arn", out.getvalue())
        self.assertNotIn("sg-relay", out.getvalue())


if __name__ == "__main__":
    unittest.main()
