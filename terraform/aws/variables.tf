# Variables = inputs to your Terraform config
# You fill them in via terraform.tfvars (never commit that file!)

variable "aws_region" {
  description = "AWS region to deploy in"
  type        = string
  default     = "ap-south-1"   # Mumbai — change to match your current EC2
}

variable "instance_type" {
  description = "EC2 instance size"
  type        = string
  default     = "t2.micro"    # Free tier
}

variable "your_ip" {
  description = "Your home IP for SSH access (run: curl ifconfig.me)"
  type        = string
}

variable "key_pair_name" {
  description = "Name of your EC2 key pair (already exists in AWS)"
  type        = string
}

variable "db_password" {
  description = "PostgreSQL password"
  type        = string
  sensitive   = true   # Won't be shown in logs
}
