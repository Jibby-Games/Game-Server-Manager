import docker
import logging
from socket import socket
from fastapi import FastAPI, HTTPException, status
import requests
from app.config import log
from pydantic import BaseModel
import semantic_version as semver


DOCKER_USER = "jibby"
DOCKER_REPO = "flappyrace"
DOCKER_HUB_URL = "https://hub.docker.com/v2/namespaces/{user}/repositories/{repo}/tags/"
IMAGE_NAME = f"{DOCKER_USER}/{DOCKER_REPO}"
SECRETS_VOLUME = "flappy-backend_nginx_secrets"
MAX_CONTAINER_RETRIES = 10
MAX_RUNNING_SERVERS = 20
MAX_TAGS = 5


log.init_loggers(__name__)
logger = logging.getLogger(__name__)

app = FastAPI()
docker_client = docker.from_env()
containers = {}
latest_tags = []
min_supported_tag: semver.Version = None


@app.get("/")
async def read_root():
    return {"Hello": "World"}


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
        get_latest_image_tags(DOCKER_USER, DOCKER_REPO)
        if version not in latest_tags:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported game version! Supported versions: {', '.join(str(v) for v in latest_tags)}",
            )
    port: int = create_server(game_request)
    return {"port": port}


@app.on_event("startup")
def startup_event():
    logger.info("Starting game manager...")
    get_latest_image_tags(DOCKER_USER, DOCKER_REPO)
    check_images_pulled(IMAGE_NAME, latest_tags)


@app.on_event("shutdown")
def shutdown_event():
    stop_all_servers()


def get_latest_image_tags(user: str, repo: str):
    params = {"page_size": MAX_TAGS}
    req = requests.get(url=DOCKER_HUB_URL.format(user=user, repo=repo), params=params)
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
    for container in list(containers.values()):
        try:
            container.reload()
        except:
            logger.debug(f"Removing {container.id} because it stopped")
            containers.pop(container.id)


def check_images_pulled(image: str, tags: list):
    for tag in tags:
        logger.info(f"Pulling '{image}:{tag}' image tag...")
        docker_client.images.pull(repository=image, tag=str(tag))
        logger.info(f"Finished pulling")


def create_server(game_request: GameRequest) -> int:
    check_images_pulled(IMAGE_NAME, latest_tags)
    for attempt in range(MAX_CONTAINER_RETRIES):
        try:
            logger.info(f"Running '{IMAGE_NAME}' container (attempts: {attempt})...")
            port = find_free_port()
            # IMPORTANT: make sure everything is converted to a string or you get weird json errors
            args = ["--name", game_request.name, "--port", str(port)]
            if game_request.list:
                args.append("--list")
            container = docker_client.containers.run(
                image=f"{IMAGE_NAME}:{game_request.version}",
                command=args,
                tty=True,
                ports={
                    f"{port}/udp": ("0.0.0.0", port),
                    f"{port}/tcp": ("0.0.0.0", port),
                },
                volumes=[f"{SECRETS_VOLUME}:/secrets:ro"],
                detach=True,
            )
        except docker.errors.APIError as err:
            logger.warning(f"Failed to start container will try again. Reason: {err}")
        except docker.errors.ImageNotFound as err:
            logger.warning(f"Image was removed, will try pulling again: {err}")
            check_images_pulled(IMAGE_NAME, latest_tags)
        else:
            # Container running successfully, save it for later
            logger.info(f"Server container {container.id} started")
            containers[container.id] = container
            return port
    else:
        raise Exception(
            f"Failed to create container after {MAX_CONTAINER_RETRIES} attempts - stopping."
        )


def find_free_port() -> int:
    with socket() as s:
        s.bind(("", 0))
        _, port = s.getsockname()
    return port


def stop_all_servers():
    logger.info(f"Stopping {len(containers)} containers...")
    for container in containers.values():
        stop_server(container.id)


def stop_server(container_id: str):
    logger.info(f"Stopping container {container_id}...")
    try:
        container = docker_client.containers.get(container_id)
    except docker.errors.NotFound as exc:
        logger.warn(f"Failed to stop server: {exc.explanation}")
    else:
        container.stop()
