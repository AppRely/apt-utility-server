from src.config.common import *  # noqa

# Testing
# INSTALLED_APPS += ('django_nose',)  # noqa
# TEST_RUNNER = 'django_nose.NoseTestSuiteRunner'
# NOSE_ARGS = ['-s', '--nologcapture', '--with-progressive', '--with-fixture-bundling']
INSTALLED_APPS += ('pytest_django',)  # noqa
#
TEST_RUNNER = 'pytest_django.runner.DjangoPytestRunner'
