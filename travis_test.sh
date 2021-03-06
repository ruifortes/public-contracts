#!/bin/sh
set -e

if [ $CONFIGURATION = "API" ];
then
# install
pip install -r api_requirements.txt

# script
python -m contracts.tools.example

elif [ $CONFIGURATION = "WEBSITE" ]
then
# install
pip install -r production_requirements.txt

# script
python -m contracts.tools.example
# todo: add test to `python manage.py runserver`

elif [ $CONFIGURATION = "PRODUCTION" ]
then
# install
pip install -r production_requirements.txt

# script
python -m contracts.tools.example
PYTHONPATH=$PYTHONPATH:`pwd` django-admin.py test --settings=law.tests.settings law.tests
PYTHONPATH=$PYTHONPATH:`pwd` django-admin.py test --settings=contracts.tests.settings contracts.tests
fi
