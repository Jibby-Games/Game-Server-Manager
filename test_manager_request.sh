#!/bin/sh
# POST request to start a server
curl -H 'Content-Type: application/json' \
    -d '{ "name":"test-server","list":1, "version": "0.12.0"}' \
    -X POST -k \
    localhost:8000/api/manager/request