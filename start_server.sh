#!/bin/sh
. ./.venv/bin/activate
uvicorn app.main:app --reload
