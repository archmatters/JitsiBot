FROM python:3-slim

COPY . /jitsibot

WORKDIR /jitsibot

RUN pip install -r requirements.txt

ENTRYPOINT python3 bot.py
