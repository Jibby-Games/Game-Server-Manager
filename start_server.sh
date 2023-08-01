#!/bin/sh
python3 -m venv .venv
. ./.venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
