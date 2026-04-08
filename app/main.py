import logging
import os
import sys
import asyncio
import string
import secrets
from contextlib import asynccontextmanager
from enum import Enum
from importlib.metadata import version
from socket import socket, AF_INET, SOCK_STREAM, SOCK_DGRAM

import docker
import requests
import requests.packages.urllib3
import semantic_version as semver

requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)
from app.config import log
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

# Load user app settings from .env file or env vars
load_dotenv()


class ConnectionMode(str, Enum):
    PORTS = "ports"
    TRAEFIK = "traefik"


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        print(
            f"ERROR: Required environment variable '{name}' is not set. Copy .env.example to .env and fill it in."
        )
        sys.exit(1)
    return value


# Required environment variables
DOCKER_USER: str = _require_env("DOCKER_USER")
DOCKER_REPO: str = _require_env("DOCKER_REPO")
MAX_CONTAINER_RETRIES: int = int(_require_env("MAX_CONTAINER_RETRIES"))
MAX_RUNNING_SERVERS: int = int(_require_env("MAX_RUNNING_SERVERS"))
MAX_TAGS: int = int(_require_env("MAX_TAGS"))

try:
    CONNECTION_MODE: ConnectionMode = ConnectionMode(
        os.getenv("CONNECTION_MODE", "traefik")
    )
except ValueError:
    print(
        f"ERROR: Invalid CONNECTION_MODE value '{os.getenv('CONNECTION_MODE')}'. Must be 'ports' or 'traefik'."
    )
    sys.exit(1)

# Settings for Ports connection mode
# HTTPS certs passed to game servers via a volume
SECRETS_VOLUME: str = os.getenv("SECRETS_VOLUME", "")
GAME_SERVER_PORT_MIN: int = int(os.getenv("GAME_SERVER_PORT_MIN", "7000"))
GAME_SERVER_PORT_MAX: int = int(os.getenv("GAME_SERVER_PORT_MAX", "7999"))
# Settings for Traefik connection mode
TRAEFIK_NETWORK: str = os.getenv("TRAEFIK_NETWORK", "frontend")
# Fixed port that game servers listen on internally, Traefik will route to this port on the container
TRAEFIK_GAME_PORT: int = int(os.getenv("TRAEFIK_GAME_PORT", "31400"))
GAME_SLUG: str = os.getenv("GAME_SLUG", "flappy-race")
# Optional host domain; when set, adds Host() to Traefik router rules so TLS SNI resolves correctly
TRAEFIK_HOST: str = os.getenv("TRAEFIK_HOST", "")
LOCAL_IMAGES: bool = os.getenv("LOCAL_IMAGES", "false").lower() in ("true", "1", "yes")

# Constants
DOCKER_HUB_URL = "https://hub.docker.com/v2/namespaces/{user}/repositories/{repo}/tags/"
IMAGE_NAME = f"{DOCKER_USER}/{DOCKER_REPO}"
# Remove some characters like 0 and 1 to avoid confusion
GAME_ID_ALPHABET = string.ascii_uppercase + "23456789"
GAME_ID_LENGTH = 8


def generate_game_id() -> str:
    return "".join(secrets.choice(GAME_ID_ALPHABET) for _ in range(GAME_ID_LENGTH))


# Load logger
log.init_loggers(__name__)
logger = logging.getLogger(__name__)


def get_settings():
    msg = f"""Loaded settings:
DOCKER_USER: {DOCKER_USER}
DOCKER_REPO: {DOCKER_REPO}
MAX_CONTAINER_RETRIES: {MAX_CONTAINER_RETRIES}
MAX_RUNNING_SERVERS: {MAX_RUNNING_SERVERS}
MAX_TAGS: {MAX_TAGS}
CONNECTION_MODE: {CONNECTION_MODE}
LOCAL_IMAGES: {LOCAL_IMAGES}
SECRETS_VOLUME: {SECRETS_VOLUME}
GAME_SERVER_PORT_RANGE: {GAME_SERVER_PORT_MIN}-{GAME_SERVER_PORT_MAX}
TRAEFIK_NETWORK: {TRAEFIK_NETWORK}
GAME_SLUG: {GAME_SLUG}
TRAEFIK_HOST: {TRAEFIK_HOST}"""
    return msg


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting game manager v%s...", version("game-server-manager"))
    logger.info(get_settings())
    if CONNECTION_MODE == ConnectionMode.PORTS:
        check_secrets_volume()
    if LOCAL_IMAGES:
        get_local_image_tags(IMAGE_NAME)
    else:
        get_latest_image_tags(DOCKER_USER, DOCKER_REPO)
        check_images_pulled(IMAGE_NAME, latest_tags)
    yield
    # Shutdown
    stop_all_servers()


app = FastAPI(lifespan=lifespan)
try:
    docker_client = docker.from_env()
except docker.errors.DockerException:
    logger.critical("Docker service is not running! Cannot run manager!")
    sys.exit(1)
containers = {}
latest_tags = []
min_supported_tag: semver.Version = None

next_port = GAME_SERVER_PORT_MIN


def get_next_port() -> int:
    global next_port
    port = next_port
    next_port += 1
    if next_port > GAME_SERVER_PORT_MAX:
        next_port = GAME_SERVER_PORT_MIN
    return port


@app.get("/")
@app.get("/api/manager")
@app.get("/api/manager/healthcheck")
async def hello_world():
    return "Hello world"


# This is needed to pass the CORS preflight checks from HTML5 builds so they can
# request games to be created
@app.options("/api/manager/request")
async def request_game_preflight():
    return


class GameRequest(BaseModel):
    name: str
    list: bool
    version: str


@app.post("/api/manager/request", status_code=status.HTTP_201_CREATED)
async def request_game(game_request: GameRequest):
    logger.debug("Received request: %s", game_request)
    version: semver.Version
    try:
        version = semver.Version(game_request.version)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Request didn't contain a valid version! Supported versions: {', '.join(str(v) for v in latest_tags)}",
        )
    remove_stopped_containers()
    if len(containers) >= MAX_RUNNING_SERVERS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Max amount of official servers reached! Try joining a public one.",
        )
    if version < min_supported_tag:
        raise HTTPException(
            status_code=status.HTTP_426_UPGRADE_REQUIRED,
            detail=f"Your game version is out of date! Supported versions: {', '.join(str(v) for v in latest_tags)}",
        )
    if version not in latest_tags:
        # Try to see if there are new tags available
        if LOCAL_IMAGES:
            get_local_image_tags(IMAGE_NAME)
        else:
            get_latest_image_tags(DOCKER_USER, DOCKER_REPO)
        if version not in latest_tags:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported game version! Supported versions: {', '.join(str(v) for v in latest_tags)}",
            )
    result: dict = await create_server(game_request)
    return result


def get_latest_image_tags(user: str, repo: str):
    params = {"page_size": MAX_TAGS}
    req = requests.get(
        url=DOCKER_HUB_URL.format(user=user, repo=repo), params=params, timeout=30
    )
    if req.status_code != 200:
        logger.error("Failed to get latest image tags!")
        return
    data = req.json()
    tags = []
    min_tag = None
    for tag in data["results"]:
        try:
            version: semver.Version = semver.Version(tag["name"])
        except ValueError:
            continue
        else:
            if min_tag == None or min_tag > version:
                min_tag = version
            tags.append(version)
    global latest_tags, min_supported_tag
    latest_tags = tags
    min_supported_tag = min_tag
    logger.info(
        f"Got latest tags. New supported tags: {', '.join(str(v) for v in latest_tags)}, minimum supported version: {min_supported_tag}"
    )


def remove_stopped_containers():
    logger.debug("Checking and removing stopped containers...")
    for container in list(containers.values()):
        try:
            container.reload()
            logger.debug(f"Container {container.id} status: %s", container.status)
            if container.status != "running":
                logger.info(f"Removing {container.id} because it stopped")
                containers.pop(container.id)
        except:
            logger.info(f"Removing {container.id} because it was deleted")
            containers.pop(container.id)


def check_secrets_volume():
    """Verify that the secrets volume exists and is accessible."""
    logger.info(f"Checking secrets volume '{SECRETS_VOLUME}'...")
    try:
        volume = docker_client.volumes.get(SECRETS_VOLUME)
        logger.info(f"Secrets volume found: {volume.name}")
    except docker.errors.NotFound:
        logger.critical(f"Secrets volume '{SECRETS_VOLUME}' does not exist!")
        logger.critical(
            "Please create the volume or check your SECRETS_VOLUME configuration."
        )
        sys.exit(1)
    except docker.errors.APIError as err:
        logger.critical(f"Failed to access secrets volume: {err}")
        sys.exit(1)


def get_local_image_tags(image: str):
    """Populate latest_tags from locally available Docker images."""
    tags = []
    min_tag = None
    for img in docker_client.images.list(name=image):
        for tag_str in img.tags:
            # tag_str is like "repo/name:1.2.3"
            raw = tag_str.split(":", 1)[-1]
            try:
                version: semver.Version = semver.Version(raw)
            except ValueError:
                continue
            if min_tag is None or min_tag > version:
                min_tag = version
            tags.append(version)
    tags = sorted(set(tags), reverse=True)[:MAX_TAGS]
    global latest_tags, min_supported_tag
    latest_tags = tags
    min_supported_tag = min_tag
    logger.info(
        f"Using local image tags. Supported tags: {', '.join(str(v) for v in latest_tags)}, minimum supported version: {min_supported_tag}"
    )


def check_images_pulled(image: str, tags: list):
    for tag in tags:
        logger.info(f"Pulling '{image}:{tag}' image tag...")
        docker_client.images.pull(repository=image, tag=str(tag))
        logger.info(f"Finished pulling")


async def wait_for_traefik_router(router_name: str, path_prefix: str, timeout: int = 10):
    """Wait until Traefik is actually serving the route over HTTPS.

    Probes the route directly via the Traefik container on the Docker network
    (https://traefik<path_prefix>) so we know the route is live before returning,
    not just that the container label exists.
    """
    # Traefik is reachable at its container name on the TRAEFIK_NETWORK
    probe_url = f"https://traefik{path_prefix}"
    headers = {"Host": TRAEFIK_HOST} if TRAEFIK_HOST else {}
    for _ in range(timeout):
        try:
            resp = requests.get(probe_url, headers=headers, timeout=2, verify=False)
            # Traefik's own "no route" 404 has this exact body.
            # Any other response means Traefik has picked up the route.
            if not (resp.status_code == 404 and resp.text.strip() == "404 page not found"):
                logger.info(f"Traefik router '{router_name}' is ready")
                return
        except requests.exceptions.RequestException:
            pass
        await asyncio.sleep(1)
    logger.warning(f"Traefik router '{router_name}' not ready after {timeout}s, continuing anyway")


async def create_server(game_request: GameRequest) -> dict:
    if not LOCAL_IMAGES:
        check_images_pulled(IMAGE_NAME, latest_tags)
    for attempt in range(MAX_CONTAINER_RETRIES):
        try:
            logger.info(f"Running '{IMAGE_NAME}' container (attempts: {attempt})...")
            # IMPORTANT: make sure everything is converted to a string or you get weird json errors
            match CONNECTION_MODE:
                case ConnectionMode.PORTS:
                    port = find_free_port()
                    game_id = None
                    ports = {
                        f"{port}/udp": ("0.0.0.0", port),
                        f"{port}/tcp": ("0.0.0.0", port),
                    }
                    labels = {}
                    network = None
                    volumes = (
                        [f"{SECRETS_VOLUME}:/secrets:ro"] if SECRETS_VOLUME else []
                    )
                case ConnectionMode.TRAEFIK:
                    port = TRAEFIK_GAME_PORT
                    game_id = generate_game_id()
                    path_prefix = f"/games/{GAME_SLUG}/{game_id}"
                    router_name = f"gameserver-{game_id}"
                    ports = {}
                    traefik_rule = (
                        f"Host(`{TRAEFIK_HOST}`) && PathPrefix(`{path_prefix}`)"
                        if TRAEFIK_HOST
                        else f"PathPrefix(`{path_prefix}`)"
                    )
                    labels = {
                        "traefik.enable": "true",
                        f"traefik.http.routers.{router_name}.rule": traefik_rule,
                        f"traefik.http.routers.{router_name}.entrypoints": "websecure",
                        f"traefik.http.routers.{router_name}.tls": "true",
                        f"traefik.http.routers.{router_name}.middlewares": f"{router_name}-strip",
                        f"traefik.http.middlewares.{router_name}-strip.stripprefix.prefixes": path_prefix,
                        f"traefik.http.services.{router_name}.loadbalancer.server.port": str(
                            port
                        ),
                    }
                    network = TRAEFIK_NETWORK
                    volumes = []
            args = ["--name", game_request.name, "--port", str(port)]
            if CONNECTION_MODE == ConnectionMode.TRAEFIK:
                args += ["--game-id", game_id]
            if game_request.list:
                args.append("--list")
            container = docker_client.containers.run(
                image=f"{IMAGE_NAME}:{game_request.version}",
                command=args,
                tty=True,
                ports=ports,
                labels=labels,
                network=network,
                volumes=volumes,
                detach=True,
            )
        except docker.errors.APIError as err:
            logger.warning(f"Failed to start container will try again. Reason: {err}")
        except docker.errors.ImageNotFound as err:
            logger.warning(f"Image was removed, will try pulling again: {err}")
            check_images_pulled(IMAGE_NAME, latest_tags)
        else:
            # Wait for container to be running
            logger.info(f"Waiting for container {container.id} to start...")
            for _ in range(30):
                container.reload()
                if container.status == "running":
                    health = (
                        container.attrs.get("State", {}).get("Health", {}).get("Status")
                    )
                    if health == "healthy" or health is None:
                        # Container running successfully, save it for later
                        logger.info(
                            f"Server container {container.id} started and ready"
                        )
                        containers[container.id] = container
                        match CONNECTION_MODE:
                            case ConnectionMode.TRAEFIK:
                                logger.info(
                                    f"Game server started with game_id={game_id}"
                                )
                                await wait_for_traefik_router(router_name, path_prefix)
                                return {"game_id": game_id}
                            case ConnectionMode.PORTS:
                                logger.info(f"Game server started with port={port}")
                                return {"port": port}
                    elif health == "unhealthy":
                        logger.error(f"Server container {container.id} is unhealthy")
                        break
                elif container.status == "exited":
                    logger.error(f"Server container {container.id} exited immediately")
                    break
                await asyncio.sleep(1)

            # If we reach here, the container failed to start properly in this attempt
            logger.warning(
                f"Container {container.id} failed to reach ready state, retrying..."
            )
            try:
                container.stop()
            except:
                pass
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create game server after {MAX_CONTAINER_RETRIES} attempts. Please try again later.",
        )


def get_docker_used_ports() -> set:
    used_ports = set()
    try:
        # Check all containers to be safe (running and non-running)
        for container in docker_client.containers.list(all=True):
            ports = container.attrs.get("NetworkSettings", {}).get("Ports")
            if ports:
                for mappings in ports.values():
                    if mappings:
                        for mapping in mappings:
                            host_port = mapping.get("HostPort")
                            if host_port:
                                used_ports.add(int(host_port))
    except Exception as e:
        logger.warning(f"Failed to fetch used ports from Docker: {e}")
    return used_ports


def find_free_port() -> int:
    """Find a free port within the configured range."""
    used_ports = get_docker_used_ports()
    logger.debug(f"Ports currently in use by Docker: {used_ports}")

    for attempts in range(0, GAME_SERVER_PORT_MAX + 1 - GAME_SERVER_PORT_MIN):
        port = get_next_port()
        if port in used_ports:
            continue

        try:
            with socket(AF_INET, SOCK_STREAM) as s_tcp:
                s_tcp.bind(("0.0.0.0", port))
            with socket(AF_INET, SOCK_DGRAM) as s_udp:
                s_udp.bind(("0.0.0.0", port))
            return port
        except OSError:
            # Port is already in use, try next one
            continue
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"No free ports available. Please try again later.",
    )


def stop_all_servers():
    logger.info(f"Stopping {len(containers)} containers...")
    for container in containers.values():
        stop_server(container.id)


def stop_server(container_id: str):
    logger.info(f"Stopping container {container_id}...")
    try:
        container = docker_client.containers.get(container_id)
    except docker.errors.NotFound as exc:
        logger.warning(f"Failed to stop server: {exc.explanation}")
    else:
        container.stop()
