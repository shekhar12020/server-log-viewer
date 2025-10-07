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
- **Database Integration**: Read-only database access with table browsing and custom SQL queries
- **Responsive Design**: Works on desktop and mobile devices
- **Line Numbers**: Synchronized line numbers for easy reference
- **Status Indicators**: Live/Paused/Ready status with visual feedback
- **Security**: Optional token-based authentication

## 🚀 Quick Start

### Prerequisites

- Docker installed and running
- Git (for cloning the repository)

### Installation & Deployment

#### Option 1: Docker (Recommended)

1. **Clone the repository on your server**:
   ```bash
   git clone https://github.com/shekhar12020/server-log-viewer.git
   cd server-log-viewer
   ```

2. **Build and run with Docker**:
   ```bash
   # Build the Docker image
   docker build -t log-viewer .
   
   # Run the container
   docker run -d \
     --name log-viewer \
     -p 8080:8080 \
     -v /var/run/docker.sock:/var/run/docker.sock:ro \
     --restart unless-stopped \
     log-viewer
   ```

3. **Or use Docker Compose** (easier):
   ```bash
   # Start the application
   docker-compose up -d
   
   # View logs
   docker-compose logs -f
   
   # Stop the application
   docker-compose down
   ```

4. **Access the web interface**:
   - Open your browser to `http://your-server-ip:8080`
   - If using token auth: `http://your-server-ip:8080?token=your-secret-token`

#### Option 2: Direct Python (Development)

1. **Clone and setup**:
   ```bash
   git clone https://github.com/shekhar12020/server-log-viewer.git
   cd server-log-viewer
   pip install psycopg2-binary  # Optional: for database features
   ```

2. **Configure environment variables** (optional):
   ```bash
   export LOG_WEB_HOST=0.0.0.0
   export LOG_WEB_PORT=8080
   export LOG_WEB_TOKEN=your-secret-token
   export LOG_WEB_DOCKER_SUDO=1  # If Docker requires sudo
   ```

3. **Run the application**:
   ```bash
   python3 log_web.py
   ```

## 🔧 Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_WEB_HOST` | `127.0.0.1` | Host to bind the server to |
| `LOG_WEB_PORT` | `8080` | Port to run the server on |
| `LOG_WEB_TOKEN` | `""` | Optional authentication token |
| `LOG_WEB_DOCKER_SUDO` | `0` | Use sudo for Docker commands (0/1) |
| `LOG_WEB_DOCKER_BIN` | `docker` | Path to Docker binary |
| `LOG_WEB_DB_HOST` | `""` | Database host for direct connections |
| `LOG_WEB_DB_NAME` | `""` | Database name |
| `LOG_WEB_DB_USER_RO` | `""` | Read-only database user |
| `LOG_WEB_DB_PASS_RO` | `""` | Read-only database password |
| `LOG_WEB_DB_TOKEN` | `""` | Database access token |
| `LOG_WEB_DB_CONTAINER` | `""` | Docker container for database access |

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
7. **Database Access**: Switch to the Database tab to browse tables and run queries

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

### Logs
- `GET /` - Main web interface
- `GET /containers` - List available containers
- `GET /stream?container=NAME&level=LEVEL&q=QUERY` - Stream logs via SSE

### Database (Read-only)
- `GET /db/tables` - List available database tables
- `GET /db/table?name=TABLE&limit=LIMIT&offset=OFFSET` - Get table data
- `POST /db/query` - Execute custom SELECT queries

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

**Docker-specific issues**:
- **Permission denied**: Ensure Docker socket is accessible: `sudo chmod 666 /var/run/docker.sock`
- **Container can't access Docker**: Check volume mount: `-v /var/run/docker.sock:/var/run/docker.sock:ro`
- **Build fails**: Check Docker is running: `docker --version`
- **Port already in use**: Change port mapping: `-p 8081:8080`

## 📁 File Structure

```
server-log-viewer/
├── log_web.py          # Main web application
├── Dockerfile          # Docker configuration
├── docker-compose.yml  # Docker Compose configuration
├── requirements.txt    # Python dependencies
├── .dockerignore       # Docker build context exclusions
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
