## Highlights

- Modern Python development with Python 3.8+
- Bleeding edge Django 3.1+
- Fully dockerized, local development via docker-compose.
- PostgreSQL
- Full test coverage, continuous integration, and continuous deployment.
- Celery tasks

```bash
cp .env.dist .env                                             # create .env file and fill-in DB info
```

### Make Migrations

This will create migration & then migrate create/update tables/columns
#### For windows
```bash
docker-compose run --rm web python ./manage.py makemigrations
docker-compose run --rm web python ./manage.py migrate
```
#### For Mac
```bash
docker compose run --rm web python ./manage.py makemigrations
docker compose run --rm web python ./manage.py migrate
```

### Make Migrations for particular app

This will create migration & then migrate create/update tables/columns

```bash
docker-compose run --rm web python ./manage.py makemigrations order
docker-compose run --rm web python ./manage.py migrate order
```

### To build the application

```bash
docker-compose build
```
### Run the application

```bash
docker-compose up
```

