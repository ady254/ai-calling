# Outputs = things Terraform prints after apply finishes
# Like a summary of "here's what got created"

output "server_ip" {
  description = "Public IP of the EC2 instance"
  value       = aws_eip.aicalling.public_ip
}

output "ssh_command" {
  description = "SSH command to connect to the server"
  value       = "ssh -i ~/.ssh/your-key.pem ubuntu@${aws_eip.aicalling.public_ip}"
}

output "api_health_url" {
  description = "URL to check if backend is healthy"
  value       = "https://v3api.innvox.in/health"
}
