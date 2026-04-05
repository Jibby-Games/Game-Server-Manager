# Game Server Manager - Generic Server Managing API

A game server manager which uses Docker to run and manage each server instance, written in Python with the FastAPI framework. This allows game clients to request a server to be created using a REST API.

Originally written for the open source game
[Flappy Race](https://github.com/Jibby-Games/Flappy-Race).
Used as part of the [Flappy Backend repo](https://github.com/Jibby-Games/Flappy-Backend) which
contains other microservices for the game.

## API Overview
- `POST /api/manager/request`: request to create a new game server
  - Expects the following JSON fields:
    - `name` (string) - the name of the game server for the server browser
    - `list` (bool) - if the game should appear in the server browser
    - `version` (string) - the game version to run based on the Docker image tag names (using Semantic Versioning format)
  - Returns the following JSON if successful, depending on `CONNECTION_MODE`:

### `ports` mode
The game server is bound to a host port. The client connects directly using the host IP and returned port.
```json
{ "port": 7000 }
```

### `traefik` mode
The game server is routed through Traefik. The client receives a `game_id` and constructs the WebSocket URL itself, e.g.:
`wss://<domain>/games/<slug>/<game_id>/ws`
```json
{ "game_id": "ABCDEF123456" }
```

## Connection Modes

| Mode | `CONNECTION_MODE` | How it works |
|---|---|---|
| Ports | `ports` | Game server containers bind a host port in the `GAME_SERVER_PORT_MIN`–`GAME_SERVER_PORT_MAX` range. TLS is handled by the game server using certs from `SECRETS_VOLUME`. |
| Traefik | `traefik` | Game server containers attach to the Traefik network with no published ports. Traefik routes `wss://<domain>/games/<slug>/<game_id>/ws` to the container and strips the path prefix before forwarding. TLS is terminated by Traefik — game containers receive plain WebSocket connections. |

# Requirements
The following must be installed and setup to use this repo correctly:
- Docker - For running and managing game servers
- [uv](https://docs.astral.sh/uv/) - For managing Python packages

# Development
Run the `./start_server.sh` script to start the app using `uv`.
This will auto reload any changes to the app to make testing easier.

# Updating Dependencies
Run the `./update_deps.sh` script to upgrade and re-lock all dependencies using `uv`.
Make sure everything still works and then commit these files if they were updated:
- pyproject.toml
- uv.lock
