import docker
import uvicorn
from socket import socket
from fastapi import FastAPI

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
    pass


@app.on_event("shutdown")
def shutdown_event():
    stop_all_servers()


def create_server():
    with socket() as s:
        s.bind(("", 0))
        _, port = s.getsockname()

    client = docker.from_env()
    container = client.containers.create(
        image="flappyrace",
        tty=True,
        ports={f"{port}/udp": ("0.0.0.0", port), f"{port}/tcp": ("0.0.0.0", port)},
        detach=True,
        environment=[f"FLAPPY_PORT={port}"],
    )
    print(f"Starting container {container.id}...")
    container.start()
    print(container)
    containers[container.id] = container
    return port


def stop_all_servers():
    print(f"Stopping {len(containers)} containers")
    for container in containers.values():
        print(f"Stopping container {container.id}...")
        container.stop()
