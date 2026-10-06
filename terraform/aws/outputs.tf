output "server_ip" {
  description = "Elastic (permanent) public IP of the EC2 instance"
  value       = aws_eip.aicalling.public_ip
}

output "ssh_command" {
  description = "SSH command to connect to the server"
  value       = "ssh -i ~/.ssh/${var.key_pair_name}.pem ubuntu@${aws_eip.aicalling.public_ip}"
}

output "api_health_url" {
  description = "URL to check if the backend is healthy"
  value       = "https://v3api.innvox.in/health"
}

output "bootstrap_log" {
  description = "SSH command to watch the bootstrap script progress"
  value       = "ssh -i ~/.ssh/${var.key_pair_name}.pem ubuntu@${aws_eip.aicalling.public_ip} 'tail -f /var/log/cloud-init-output.log'"
}
