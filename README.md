# 📊 Docker Log Viewer

A real-time web application for viewing and monitoring Docker container logs with an intuitive interface designed for developers.

## ✨ Features

- **Real-time Log Streaming**: Live tail of Docker container logs with Server-Sent Events (SSE)
- **Smart Auto-scroll**: Automatically follows new logs, pauses when you scroll up, resumes when you scroll to bottom
- **Interactive Controls**: 
  - Container selection dropdown
  - Log level filtering (ANY, DEBUG, INFO, WARN, ERROR, CRITICAL)
  - Search functionality
  - Pause/Resume/Stop controls
  - Clear logs button
- **Responsive Design**: Works on desktop and mobile devices
- **Line Numbers**: Synchronized line numbers for easy reference
- **Status Indicators**: Live/Paused/Ready status with visual feedback
- **Security**: Optional token-based authentication

## 🚀 Quick Start

### Prerequisites

- Python 3.6+
- Docker installed and running
- Access to Docker containers (may require sudo)

### Installation

1. **Clone the repository**:
   ```bash
   git clone git@github.com:shekhar12020/access-server-logs.git
   cd access-server-logs
   ```

2. **Install dependencies** (uses only standard library):
   ```bash
   # No additional dependencies required!
   ```

3. **Configure environment variables** (optional):
   ```bash
   export LOG_WEB_HOST=0.0.0.0
   export LOG_WEB_PORT=8080
   export LOG_WEB_TOKEN=your-secret-token
   export LOG_WEB_DOCKER_SUDO=1  # If Docker requires sudo
   ```

4. **Run the application**:
   ```bash
   python3 log_web.py
   ```

5. **Access the web interface**:
   - Open your browser to `http://localhost:8080`
   - If using token auth: `http://localhost:8080?token=your-secret-token`

## 🔧 Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_WEB_HOST` | `0.0.0.0` | Host to bind the server to |
| `LOG_WEB_PORT` | `8080` | Port to run the server on |
| `LOG_WEB_TOKEN` | `""` | Optional authentication token |
| `LOG_WEB_DOCKER_SUDO` | `0` | Use sudo for Docker commands (0/1) |
| `LOG_WEB_DOCKER_BIN` | `docker` | Path to Docker binary |

### Docker Permissions

If you get permission errors, either:

**Option 1: Use sudo** (less secure):
```bash
export LOG_WEB_DOCKER_SUDO=1
python3 log_web.py
```

**Option 2: Add user to docker group** (recommended):
```bash
sudo usermod -aG docker $USER
# Log out and back in, then:
python3 log_web.py
```

## 📱 Usage

### Basic Operations

1. **Select Container**: Choose from the dropdown list of running containers
2. **Start Following**: Click "▶ Follow" to begin live log streaming
3. **Pause/Resume**: Click "⏸ Pause" to pause, "▶ Resume" to continue
4. **Stop**: Click "⏹ Stop" to stop following and clear logs
5. **Search**: Use the search box to filter logs by content
6. **Filter by Level**: Select specific log levels (DEBUG, INFO, WARN, ERROR, CRITICAL)

### Smart Scrolling

- **Auto-scroll**: Automatically scrolls to show new logs
- **Manual Scroll**: Scroll up to pause auto-scroll and view older logs
- **Resume**: Scroll back to bottom to resume auto-scroll
- **Status Indicator**: Shows Live/Paused/Ready status

### Mobile Usage

The interface is fully responsive and works on mobile devices with touch-friendly controls.

## 🛡️ Security

### Production Deployment

For production use, consider:

1. **Enable HTTPS** with nginx reverse proxy
2. **Set a strong authentication token**:
   ```bash
   export LOG_WEB_TOKEN="your-very-strong-random-token-here"
   ```
3. **Restrict access by IP** in nginx/firewall
4. **Use VPN** instead of public exposure

### Authentication

The app supports optional token-based authentication:
- Set `LOG_WEB_TOKEN` environment variable
- Access with `?token=your-token` in the URL
- All API endpoints require the token when enabled

## 🔍 API Endpoints

- `GET /` - Main web interface
- `GET /containers` - List available containers
- `GET /stream?container=NAME&level=LEVEL&q=QUERY` - Stream logs via SSE

## 🐛 Troubleshooting

### Common Issues

**Empty container list**:
- Check Docker is running: `docker ps`
- Try with sudo: `export LOG_WEB_DOCKER_SUDO=1`
- Check Docker binary path: `export LOG_WEB_DOCKER_BIN=/usr/bin/docker`

**Permission denied**:
- Add user to docker group or use sudo
- Check Docker daemon is running

**Connection refused**:
- Check firewall settings
- Verify port is not in use: `netstat -tlnp | grep 8080`

**Logs not streaming**:
- Check container is running: `docker ps`
- Verify container name is correct
- Check Docker logs manually: `docker logs CONTAINER_NAME`

## 📁 File Structure

```
access-server-logs/
├── log_web.py          # Main Flask web application
├── log_tui.py          # Terminal UI version (curses)
└── README.md           # This file
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📄 License

This project is open source and available under the MIT License.

## 🆘 Support

For issues and questions:
- Create an issue on GitHub
- Check the troubleshooting section above
- Review Docker and Python documentation

---

**Happy Logging! 🚀**
