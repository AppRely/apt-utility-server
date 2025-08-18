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


### Create superuser

If you want, you can create initial super-user with next command, first populate initial data for user-role:

```bash
docker-compose run --rm web python ./manage.py createsuperuser
```

### Delete migration files
```bash
find . -path "*/migrations/*.py" -not -name "__init__.py" -delete
```

### Connect Database

To connect with database, first need to check pid for postgres sql & then replace {pid} with that id

```bash
docker ps
docker exec -it {pid} psql -U user -d database
```

### Few basic sql commands to check

After connecting with database, could some basic commands for reference

```bash
\dt
select * from reference_role;
```

Run a command inside the docker container:

```bash
docker-compose run --rm web [command]
```

Create a new app like settlement inside src
First create a blank directory inside app & then run command, replace {app name}
```bash
docker-compose run --rm web python manage.py startapp {app name} src/{app name}
```
### Running Tests

To run all tests with code-coverate report, simple run:

```bash
./manage.py test
```

### Running Pre-commit configuration (black and flake8)

```bash
docker-compose run --rm web pre-commit clean
docker-compose run --rm web pre-commit install
docker-compose run --rm web pre-commit run --all-files
```

### Running test command
```bash
 docker-compose run --rm web python manage.py test src.test.test --nologcapture
```
```bash
 docker-compose run --rm web python manage.py test src.users.test.test.test_users_views --nologcapture
```

### Running test with pytest fixture
```bash
docker-compose run --rm web pytest src/test -vv  -s
```
