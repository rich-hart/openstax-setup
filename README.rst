=========================
README for openstax-setup
=========================

Installation
------------

1. Install virtualenv

   ``sudo apt-get install python-virtualenv``

   OR

   Download it from https://pypi.python.org/pypi/virtualenv

2. Set up virtual env

   ``virtualenv .``

3. Install fabric

   ``./bin/pip install fabric``

4. Have a look at what tasks are available:

   ``./bin/fab -l``

Example Usage
-------------

1. Create a VM or have a server with Ubuntu 14.04.1 (which we will call trusty).

2. (Optional) Set up your ssh key and hostname in your ssh config.

3. Set up openstax/accounts on raring: (The "Common Name" should be the site name, "trusty" in this case, when creating the ssl cert)::

     ./bin/fab -H trusty accounts_setup

4. Read the output and do some manual setup.

5. Start openstax/accounts::

     ./bin/fab -H trusty accounts_run_unicorn

6. Create an admin user::

     ./bin/fab -H trusty accounts_create_admin_user

7. Go to https://trusty:3000 and try to login as admin.

8. If you want to also install the python pyramid app that uses openstax/accounts::

     ./bin/fab -H trusty accounts_pyramid_test

   This installs Connexions/openstax-accounts and runs the stub test and the local accounts user test.
