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
  - Returns the following JSON if successful:
    - `port` (int) the port the created game server is using, which the game client should connect to

# Requirements
The following must be installed and setup to use this repo correctly:
- Docker - For running and managing game servers
- pipenv - For managing Python packages

# Development
Run the `./start_server.sh` script to create a pipenv with the right python version and
run the app. This will auto reload any changes to the app to make testing easier.

# Updating Dependencies
Run the `./update_deps.sh` script to update everthing using `pipenv` and generate a new `requirements.txt`
Make sure everything still works and then commit these files if they were updated:
- Pipfile
- Pipfile.lock
- requirements.txt (required for the Dockerfile to simplify installation)
