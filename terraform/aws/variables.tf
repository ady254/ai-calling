variable "aws_region" {
  description = "AWS region to deploy in"
  type        = string
  default     = "ap-south-1" # Mumbai
}

variable "instance_type" {
  description = "EC2 instance size"
  type        = string
  default     = "t2.micro" # Free tier
}

variable "your_ip" {
  description = "Your home IP for SSH access — run: curl ifconfig.me"
  type        = string
}

variable "key_pair_name" {
  description = "Name of your existing EC2 key pair in AWS (NOT the .pem filename, the name shown in console)"
  type        = string
}

variable "secret_key" {
  description = "Django/FastAPI secret key — generate with: openssl rand -hex 32"
  type        = string
  sensitive   = true
}

variable "internal_api_key" {
  description = "Internal API key — generate with: openssl rand -hex 32"
  type        = string
  sensitive   = true
}

variable "database_url" {
  description = "Neon PostgreSQL connection URL (postgresql+asyncpg://...)"
  type        = string
  sensitive   = true
}

variable "redis_url" {
  description = "Upstash Redis URL (rediss://...)"
  type        = string
  sensitive   = true
}

variable "livekit_url" {
  description = "LiveKit server URL"
  type        = string
  default     = "wss://innvox-um8kvrmw.livekit.cloud"
}

variable "livekit_api_key" {
  description = "LiveKit API Key"
  type        = string
  sensitive   = true
}

variable "livekit_api_secret" {
  description = "LiveKit API Secret"
  type        = string
  sensitive   = true
}

variable "livekit_sip_domain" {
  description = "LiveKit SIP Domain"
  type        = string
  default     = ""
}

variable "gemini_api_key" {
  description = "Google Gemini API Key (also used as GOOGLE_API_KEY)"
  type        = string
  sensitive   = true
}

variable "eleven_api_key" {
  description = "ElevenLabs API Key"
  type        = string
  sensitive   = true
}

variable "deepgram_api_key" {
  description = "Deepgram API Key"
  type        = string
  sensitive   = true
}

variable "twilio_account_sid" {
  description = "Twilio Account SID"
  type        = string
  sensitive   = true
}

variable "twilio_auth_token" {
  description = "Twilio Auth Token"
  type        = string
  sensitive   = true
}

variable "twilio_phone_number" {
  description = "Twilio Phone Number (e.g. +1234567890)"
  type        = string
}

variable "base_url" {
  description = "Your backend domain (e.g. https://v3api.innvox.in)"
  type        = string
  default     = "https://v3api.innvox.in"
}

variable "allowed_origins" {
  description = "Your Vercel frontend URL (e.g. https://ai-calling-seven.vercel.app)"
  type        = string
  default     = "https://ai-calling-seven.vercel.app"
}

variable "github_repo_url" {
  description = "Your GitHub repo URL (e.g. https://github.com/ady254/ai-calling.git)"
  type        = string
  default     = "https://github.com/ady254/ai-calling.git"
}
