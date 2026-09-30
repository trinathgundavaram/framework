output "workflows" {
  description = "Step Functions workflow per project (and all_projects): name and ARN."
  value       = { for k, m in aws_sfn_state_machine.workflow : k => { name = m.name, arn = m.arn } }
}

output "schedule_rules" {
  value = { for k, r in aws_cloudwatch_event_rule.schedule : k => r.name }
}
