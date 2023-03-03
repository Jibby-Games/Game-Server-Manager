import docker
import logging
from socket import socket
from fastapi import FastAPI
from app.config import log

IMAGE_NAME = "jibby/flappyrace"

log.init_loggers(__name__)
logger = logging.getLogger(__name__)

app = FastAPI()
containers = {}


@app.get("/")
async def read_root():
    return {"Hello": "World"}


@app.get("/api/request")
async def request_game():
    port = create_server()
    return port


@app.on_event("startup")
def startup_event():
    logger.info("Starting game manager...")
    check_images_pulled()


@app.on_event("shutdown")
def shutdown_event():
    stop_all_servers()


def check_images_pulled():
    client = docker.from_env()
    logger.info(f"Checking if '{IMAGE_NAME}' image exists")
    try:
        client.images.get(IMAGE_NAME)
    except docker.errors.ImageNotFound:
        logger.warn(f"Unable to find image for '{IMAGE_NAME}', pulling latest...")
        client.images.pull(IMAGE_NAME)
        logger.info(f"Finished pulling")
    else:
        logger.info(f"Image already pulled")


def create_server():
    client = docker.from_env()
    check_images_pulled()
    logger.info(f"Creating '{IMAGE_NAME}' container...")
    port = find_free_port()
    container = client.containers.create(
        image=IMAGE_NAME,
        tty=True,
        ports={f"{port}/udp": ("0.0.0.0", port), f"{port}/tcp": ("0.0.0.0", port)},
        detach=True,
        environment=[f"FLAPPY_PORT={port}"],
    )
    logger.info(f"Starting container {container.id}...")
    container.start()
    containers[container.id] = container
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
    client = docker.from_env()
    try:
        container = client.containers.get(container_id)
    except docker.errors.NotFound as exc:
        logger.warn(f"Failed to stop server: {exc.explanation}")
    else:
        container.stop()
