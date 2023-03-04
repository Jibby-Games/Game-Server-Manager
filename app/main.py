import docker
import logging
from socket import socket
from fastapi import FastAPI, HTTPException, status
from app.config import log

IMAGE_NAME = "jibby/flappyrace"
MAX_CONTAINER_RETRIES = 10
MAX_RUNNING_SERVERS = 20

log.init_loggers(__name__)
logger = logging.getLogger(__name__)

app = FastAPI()
docker_client = docker.from_env()
containers = {}


@app.get("/")
async def read_root():
    return {"Hello": "World"}


@app.get("/api/request", status_code=status.HTTP_201_CREATED)
async def request_game():
    remove_stopped_containers()
    if len(containers) >= MAX_RUNNING_SERVERS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Max amount of official servers reached! Try joining a public one.",
        )
    port = create_server()
    return {"port": port}


@app.on_event("startup")
def startup_event():
    logger.info("Starting game manager...")
    check_images_pulled()


@app.on_event("shutdown")
def shutdown_event():
    stop_all_servers()


def remove_stopped_containers():
    for container in list(containers.values()):
        try:
            container.reload()
        except:
            logger.debug(f"Removing {container.id} because it stopped")
            containers.pop(container.id)


def check_images_pulled():
    logger.info(f"Checking if '{IMAGE_NAME}' image exists")
    try:
        docker_client.images.get(IMAGE_NAME)
    except docker.errors.ImageNotFound:
        logger.warn(f"Unable to find image for '{IMAGE_NAME}', pulling latest...")
        docker_client.images.pull(IMAGE_NAME)
        logger.info(f"Finished pulling")
    else:
        logger.info(f"Image already pulled")


def create_server():
    check_images_pulled()
    for attempt in range(MAX_CONTAINER_RETRIES):
        try:
            logger.info(f"Running '{IMAGE_NAME}' container (attempts: {attempt})...")
            port = find_free_port()
            container = docker_client.containers.run(
                image=IMAGE_NAME,
                tty=True,
                ports={
                    f"{port}/udp": ("0.0.0.0", port),
                    f"{port}/tcp": ("0.0.0.0", port),
                },
                detach=True,
                environment=[f"FLAPPY_PORT={port}"],
            )
        except Exception as err:
            logger.warning(f"Failed to start container will try again. Reason: {err}")
        else:
            # Container running successfully, save it for later
            logger.info(f"Server container {container.id} started")
            containers[container.id] = container
            break
    else:
        logger.error(
            f"Failed to create container after {MAX_CONTAINER_RETRIES} attempts - stopping."
        )
    return port


def find_free_port():
    with socket() as s:
        s.bind(("", 0))
        _, port = s.getsockname()
    return port


def stop_all_servers():
    logger.info(f"Stopping {len(containers)} containers...")
    for container in containers.values():
        stop_server(container.id)


def stop_server(container_id):
    logger.info(f"Stopping container {container_id}...")
    try:
        container = docker_client.containers.get(container_id)
    except docker.errors.NotFound as exc:
        logger.warn(f"Failed to stop server: {exc.explanation}")
    else:
        container.stop()
