variable "project_id" {
  type        = string
  description = "project-id"
}

variable "location" {
  type        = string
  description = "The region to deploy the reasoning engine"
  default     = "global"
}

variable "repository_name" {
  type        = string
  description = "The Artifact Registry repository name"
  default     = "agent-repo"
}

variable "image_tag" {
  type        = string
  description = "The tag of the container image to deploy"
  default     = "latest"
}

variable "repository_location" {
  type        = string
  description = "The region or multi-region of the Artifact Registry repository"
  default     = "us"
}

variable "agent_name" {
  type        = string
  description = "The name of the agent"
}

variable "agent_display_name" {
  type        = string
  description = "The display name of the agent"
}

variable "agent_description" {
  type        = string
  description = "The description of the agent"
}

variable "agent_framework" {
  type        = string
  description = "The framework of the agent (e.g. google-adk or a2a)"
  default     = "google-adk"
}