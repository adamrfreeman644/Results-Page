FROM python:3.13-alpine
WORKDIR /app
COPY . .
ENV PORT=6543 DATABASE_FILE=/data/results.sqlite POLL_SECONDS=30
EXPOSE 6543
CMD ["python", "server/app.py"]
