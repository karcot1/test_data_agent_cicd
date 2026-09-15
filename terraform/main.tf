locals {
  adk_class_methods = [
    {"api_mode" = "", "name" = "get_session"},
    {"api_mode" = "", "name" = "list_sessions"},
    {"api_mode" = "", "name" = "create_session"},
    {"api_mode" = "", "name" = "delete_session"},
    {"api_mode" = "async", "name" = "async_get_session"},
    {"api_mode" = "async", "name" = "async_list_sessions"},
    {"api_mode" = "async", "name" = "async_create_session"},
    {"api_mode" = "async", "name" = "async_delete_session"},
    {"api_mode" = "async", "name" = "async_add_session_to_memory"},
    {"api_mode" = "async", "name" = "async_search_memory"},
    {"api_mode" = "stream", "name" = "stream_query"},
    {"api_mode" = "async_stream", "name" = "async_stream_query"},
    {"api_mode" = "async_stream", "name" = "streaming_agent_run_with_events"}
  ]

  a2a_class_methods = [
    {"api_mode" = "a2a_extension", "name" = "on_message_send"},
    {"api_mode" = "a2a_extension", "name" = "on_get_task"},
    {"api_mode" = "a2a_extension", "name" = "on_cancel_task"},
    {"api_mode" = "a2a_extension", "name" = "handle_authenticated_agent_card"}
  ]

  class_methods = var.agent_framework == "a2a" ? local.a2a_class_methods : local.adk_class_methods
}

# define the resource with the BYOC configuration
resource "google_vertex_ai_reasoning_engine" "byoc_agent" {
  display_name = var.agent_display_name
  description  = var.agent_description
  project      = var.project_id
  region       = var.location

  spec {
    class_methods   = jsonencode(local.class_methods)
    agent_framework = var.agent_framework
    container_spec {
      image_uri = "${var.repository_location}-docker.pkg.dev/${var.project_id}/${var.repository_name}/${var.agent_name}:${var.image_tag}"
    }
  }
}