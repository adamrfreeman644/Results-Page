FROM python:3.13-alpine
RUN apk add --no-cache chromium
WORKDIR /app
COPY . .
ENV PORT=6543 DATABASE_FILE=/data/results.sqlite POLL_SECONDS=30 CHROMIUM_BIN=/usr/bin/chromium-browser
EXPOSE 6543
CMD ["python", "server/app.py"]
