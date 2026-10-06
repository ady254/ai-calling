resource "aws_security_group" "aicalling" {
  name        = "aicalling-sg"
  description = "AI Calling Platform firewall rules"

  # SSH — only from YOUR IP (not the whole internet)
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["${var.your_ip}/32"]
    description = "SSH from my IP only"
  }

  # HTTP — Caddy needs this for Let's Encrypt SSL certificate challenge
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTP for SSL cert challenge"
  }

  # HTTPS — the API
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS API traffic"
  }

  # Allow all outbound traffic (downloads, API calls, etc.)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "aicalling-sg"
    Project = "ai-calling-platform"
  }
}
