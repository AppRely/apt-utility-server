#!/bin/bash
set -e

# 1. Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env from .env.dist..."
    cp .env.dist .env
else
    echo ".env already exists."
fi

# 1.5 Wait for Database
if [ -f "/wait-for" ]; then
    echo "Waiting for database..."
    /wait-for db:5432 --timeout=30 -- echo "Database is up"
fi

# 2. Create migration folders
APPS=("common" "files" "notifications" "social" "users" "video")

for app in "${APPS[@]}"; do
    MIGRATION_DIR="src/$app/migrations"
    if [ ! -d "$MIGRATION_DIR" ]; then
        echo "Creating migrations directory for $app..."
        mkdir -p "$MIGRATION_DIR"
        touch "$MIGRATION_DIR/__init__.py"
    elif [ ! -f "$MIGRATION_DIR/__init__.py" ]; then
        echo "Creating __init__.py for $app migrations..."
        touch "$MIGRATION_DIR/__init__.py"
    fi
done

# 3. Run Django migration commands
echo "Running makemigrations..."
python manage.py makemigrations

echo "Running migrate..."
python manage.py migrate

# 4. Setup git hooks (optional, if .git exists and we are in dev)
if [ -d ".git" ]; then
    if [ -f "pre-commit.example" ]; then
        echo "Setting up pre-commit hook..."
        cp pre-commit.example .git/hooks/pre-commit
        chmod +x .git/hooks/pre-commit
    fi
fi

# 5. Start Django server (executes the command passed to docker run)
echo "Starting application..."
exec "$@"
