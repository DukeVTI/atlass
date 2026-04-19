#!/bin/bash
# ─── Atlas VPS Docker Installation Script ─────────────────────────────────────
# Run this on your fresh Ubuntu/Debian VPS to install Docker and Docker Compose.
# Usage: chmod +x install_docker_vps.sh && ./install_docker_vps.sh

set -e

echo "========================================"
echo " Starting Docker Installation on VPS...  "
echo "========================================"

# 1. Update package index and install required dependencies
echo "Updating apt packages..."
sudo apt-get update
sudo apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release

# 2. Add Docker's official GPG key
echo "Adding Docker GPG key..."
sudo mkdir -m 0755 -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# 3. Set up the Docker repository
echo "Setting up Docker repository..."
echo \
  "deb [arch="$(dpkg --print-architecture)" signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  "$(. /etc/os-release && echo "$VERSION_CODENAME")" stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 4. Install Docker Engine, containerd, and Docker Compose
echo "Installing Docker Engine and Compose..."
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 5. Add current user to docker group (optional, prevents needing sudo for docker commands)
echo "Adding user '$USER' to the docker group..."
sudo usermod -aG docker $USER

echo "========================================"
echo " Docker Installation Complete! ✅"
echo "========================================"
echo "To apply the group changes, you must log out and log back in (or run 'newgrp docker')."
echo "Test the installation with: docker run hello-world"
