FROM python:3.13-alpine
WORKDIR /app
COPY . .
ENV PORT=8080 RDF_FILE=/data/race.rdf DATABASE_FILE=/data/results.sqlite POLL_SECONDS=5
EXPOSE 8080
CMD ["python", "server/app.py"]
