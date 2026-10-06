# Ubuntu 24.04 LTS AMI for ap-south-1 (Mumbai) — ami-0f58b397bc5c1f2e8
# Hardcoded to avoid requiring ec2:DescribeImages permission
# To verify: https://cloud-images.ubuntu.com/locator/ec2/
locals {
  ubuntu_ami = "ami-0f58b397bc5c1f2e8" # Ubuntu 24.04 LTS, ap-south-1, amd64, hvm-ssd
}

resource "aws_instance" "aicalling" {
  ami                    = local.ubuntu_ami
  instance_type          = var.instance_type
  key_name               = var.key_pair_name
  vpc_security_group_ids = [aws_security_group.aicalling.id]

  root_block_device {
    volume_size = 20 # GB — same as manual setup (free tier allows up to 30 GB)
    volume_type = "gp3"
  }

  # This script runs ONCE when the server first boots.
  # It does everything you did manually: Docker, swap, clone repo, .env, Caddyfile, compose up.
  user_data = <<-EOF
    #!/bin/bash
    set -e
    exec > /var/log/cloud-init-output.log 2>&1

    echo "=== [1/8] Updating system ==="
    apt-get update -y
    apt-get upgrade -y

    echo "=== [2/8] Installing Docker ==="
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker ubuntu
    systemctl enable docker
    systemctl start docker

    echo "=== [3/8] Installing Git ==="
    apt-get install -y git

    echo "=== [4/8] Adding swap space (critical for t2.micro 1GB RAM) ==="
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab

    echo "=== [5/8] Cloning repo ==="
    git clone ${var.github_repo_url} /home/ubuntu/ai-calling
    chown -R ubuntu:ubuntu /home/ubuntu/ai-calling

    echo "=== [6/8] Creating .env file ==="
    cat > /home/ubuntu/ai-calling/.env <<ENVFILE
SECRET_KEY=${var.secret_key}
INTERNAL_API_KEY=${var.internal_api_key}
DATABASE_URL=${var.database_url}
REDIS_URL=${var.redis_url}
BASE_URL=${var.base_url}
ALLOWED_ORIGINS=${var.allowed_origins}
ALLOW_PUBLIC_SIGNUP=false
ENVIRONMENT=production
LOG_FORMAT=json
LIVEKIT_URL=${var.livekit_url}
LIVEKIT_API_KEY=${var.livekit_api_key}
LIVEKIT_API_SECRET=${var.livekit_api_secret}
LIVEKIT_SIP_DOMAIN=${var.livekit_sip_domain}
GEMINI_API_KEY=${var.gemini_api_key}
GOOGLE_API_KEY=${var.gemini_api_key}
ELEVEN_API_KEY=${var.eleven_api_key}
DEEPGRAM_API_KEY=${var.deepgram_api_key}
TWILIO_ACCOUNT_SID=${var.twilio_account_sid}
TWILIO_AUTH_TOKEN=${var.twilio_auth_token}
TWILIO_PHONE_NUMBER=${var.twilio_phone_number}
ENVFILE

    chown ubuntu:ubuntu /home/ubuntu/ai-calling/.env
    chmod 600 /home/ubuntu/ai-calling/.env

    echo "=== [7/8] Creating Caddyfile ==="
    cat > /home/ubuntu/ai-calling/Caddyfile <<CADDYFILE
v3api.innvox.in {
    reverse_proxy backend:8000
    encode gzip
}
CADDYFILE

    chown ubuntu:ubuntu /home/ubuntu/ai-calling/Caddyfile

    echo "=== [8/8] Starting docker compose ==="
    cd /home/ubuntu/ai-calling
    sudo -u ubuntu docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

    echo "=== DONE — server is starting up ==="
  EOF

  tags = {
    Name    = "aicalling-server"
    Project = "ai-calling-platform"
  }
}

# Elastic IP = permanent public IP address
# Without this, IP changes every time EC2 restarts → DNS breaks
resource "aws_eip" "aicalling" {
  instance = aws_instance.aicalling.id
  domain   = "vpc"

  tags = {
    Name    = "aicalling-eip"
    Project = "ai-calling-platform"
  }
}
