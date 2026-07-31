from setuptools import setup, find_packages

setup(
    name='oasira_b2c',
    version='1.0.22',
    description='Oasira Home Integration for Home Assistant',
    author='EffortlessHome',
    packages=find_packages(),
    install_requires=[
        'oasira==0.2.18',
        'gcal-sync==6.2.0',
        'oauth2client==4.1.3',
        'google-auth==2.28.1',
        'google-auth-oauthlib==1.2.0',
        'google-auth-httplib2==0.2.0',
        'google-api-python-client==2.126.0',
        'gTTS==2.5.0',
        'httpx>=0.27.0'
    ],
)