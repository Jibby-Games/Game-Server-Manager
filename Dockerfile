FROM python:3.8-alpine3.17

# Set up the venv
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Install dependencies:
COPY requirements.txt .
RUN pip install -r requirements.txt

# Run the application:
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0"]
