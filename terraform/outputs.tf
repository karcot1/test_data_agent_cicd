output "reasoning_engine_id" {
  value       = google_vertex_ai_reasoning_engine.byoc_agent.id
  description = "The ID of the deployed reasoning engine"
}

output "reasoning_engine_resource_name" {
  value       = google_vertex_ai_reasoning_engine.byoc_agent.name
  description = "The resource name of the deployed reasoning engine"
}