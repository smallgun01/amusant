# P4-T4b controller-side security-group gate

The Relay container's TCP/8080 probe is only a reachability defense-in-depth check. A timeout or connection refusal can result from Relay egress, routing, or a missing listener; it is not evidence that Jellii's inbound security group denies 8080.

Before the one-off Relay RunTask can be launched, the authorized deployment controller must run the read-only gate below against the actual running Jellii ECS service task. Continue only when the process exits 0 and emits a PASS JSON report; any failed, incomplete, timed-out, or unavailable read is a hard stop before any Relay prompt. The gate does not grant AWS read permissions to the Relay task role and makes no AWS mutations.

The gate verifies the ECS task belongs to the expected service and task-definition family, resolves its attached task ENI and every security group actually attached to that ENI, and inspects all paginated ingress rules. It passes only if:

- no attached Jellii task security group permits TCP 8080, including via all-protocol rules, CIDRs, prefix lists, or referenced groups; and
- TCP 8443 is permitted only by an exact TCP/8443 ingress rule sourced from the expected dedicated Relay security group; no broad rule or other source also permits 8443; and
- the Relay and Jellii security groups are in the same VPC.

The controller needs read-only ECS DescribeTasks and DescribeTaskDefinition, and EC2 DescribeNetworkInterfaces, DescribeSecurityGroups, and DescribeSecurityGroupRules. Client timeouts/retries are finite and configured endpoint URLs are ignored for these AWS proofs. No AuthorizeSecurityGroup, RevokeSecurityGroup, RunTask, or service update is performed by this checker.

Example invocation (substitute the approved target values; do not place actual infrastructure identifiers in public notes):

    python deploy/t4b_security_group_gate.py \
      --region "$AWS_REGION" \
      --cluster "$JELLII_CLUSTER" \
      --task-arn "$RUNNING_JELLII_TASK_ARN" \
      --expected-service "$JELLII_SERVICE" \
      --expected-family "$JELLII_TASK_FAMILY" \
      --expected-relay-sg-id "$APPROVED_RELAY_SECURITY_GROUP_ID"

The JSON report contains only sanitized check names and status. Add that exact report plus timestamp to the restricted T4b execution record. Do not interpret source code or fake-client unit tests as live SG evidence. The gate has not been run against AWS; Issue #26 remains NO-GO until the actual check passes under separate execution authorization.
