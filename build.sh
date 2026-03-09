#!/usr/bin/env bash
# exit on error
set -o errexit

# 1. Instala las librerías
pip install -r requirements.txt

# 2. Empaqueta los estáticos (el mismo comando que acabas de correr)
python manage.py collectstatic --no-input

# 3. Aplica las migraciones a Supabase automáticamente
python manage.py migrate