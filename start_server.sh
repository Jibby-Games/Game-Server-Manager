#!/bin/sh
pipenv --python 3.12 shell
uvicorn app.main:app --reload
