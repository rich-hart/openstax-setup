from fabric.api import *
import fabric.contrib.files

env.use_ssh_config = True
RVM = '~/.rvm/scripts/rvm'

def _setup():
    sudo('apt-get update')
    sudo('apt-get install --yes git')
    _setup_rvm()

def _setup_rvm():
    if not fabric.contrib.files.exists(RVM):
        run('wget -q -O - https://get.rvm.io | bash -s -- --ignore-dotfiles')

def _setup_ssl():
    if not fabric.contrib.files.exists('server.crt'):
        run('openssl genrsa -des3 -passout pass:x -out server.pass.key 2048')
        run('openssl rsa -passin pass:x -in server.pass.key -out server.key')
        run('rm server.pass.key')
        run('openssl req -new -key server.key -out server.csr')
        run('openssl x509 -req -days 365 -in server.csr -signkey server.key -out server.crt')

def services_setup():
    _setup()
    _setup_ssl()
    if not fabric.contrib.files.exists('services'):
        run('git clone https://github.com/openstax/services')
    with cd('services'):
        with prefix('source {}'.format(RVM)):
            run('rvm install ruby-1.9.3-p392')
            run('rvm gemset create services')
            run('rvm gemset use services')
            run('bundle install --without production')
            run('rake db:setup', warn_only=True)
    print """
To use the facebook and twitter login:

1. Create an app on facebook and twitter

2. Paste the "App ID" and "App Secret" from the facebook app settings page into services/config/secret_settings.yml:
   facebook_app_id: '1234567890'
   facebook_app_secret: '1234567890abcdef'

   Paste the "Consumer Key" and "Consumer Secret" from the twitter app settings page into services/config/secret_settings.yml:
   twitter_consumer_key: 'xxxxx'
   twitter_consumer_secret: 'yyyyy'

3. Set the callback url on the facebook and twitter app settings page to https://{server}:3000/auth/facebook and https://{server}:3000/auth/twitter respectively. (or the IP address of {server})

""".format(server=env.host)

def services_run():
    with cd('services'):
        with prefix('source {}'.format(RVM)):
            run('rake db:migrate')
            # ctrl-c doesn't kill the rails server so the old server is still running
            run('kill -9 `cat tmp/pids/server.pid`', warn_only=True)
            run('rails server')

def services_run_ssl():
    with cd('services'):
        with prefix('source {}'.format(RVM)):
            run('thin start -p 3000 --ssl --ssl-verify --ssl-key-file ~/server.key --ssl-cert-file ~/server.crt')

def services_test(test_case=None):
    with cd('services'):
        with prefix('source {}'.format(RVM)):
            if test_case:
                run('rspec {}'.format(test_case))
            else:
                run('rake')

def example_setup():
    _setup()
    sudo('apt-get install --yes nodejs')
    if not fabric.contrib.files.exists('connect-rails'):
        run('git clone https://github.com/openstax/connect-rails')
    with cd('connect-rails'):
        with prefix('source {}'.format(RVM)):
            run('rvm install ruby-1.9.3-p392')
            run('rvm gemset create connect-rails')
            run('rvm gemset use connect-rails')
            run('bundle install --without production')
    pwd = run('pwd')
    filename = 'connect-rails/lib/openstax/connect/engine.rb'
    if not fabric.contrib.files.contains(filename, ':client_options'):
        fabric.contrib.files.sed(filename,
                'OpenStax::Connect.configuration.openstax_application_secret',
                'OpenStax::Connect.configuration.openstax_application_secret, '
                '{:client_options => {:ssl => {:ca_file => "%s/server.crt"}}}' % pwd)
    with cd('connect-rails/example'):
        with prefix('source {}'.format(RVM)):
            run('rake db:setup', warn_only=True)
            run('rake openstax_connect:install:migrations')

    print """
To set up openstax/connect-rails with openstax/services:

1. Go to http://{server}:2999/oauth/applications

2. Create a "New application" with callback url: "http://{server}:4000/connect/auth/openstax/callback"

3. Click the "Trusted?" checkbox and submit

4. Copy the application ID and secret into connect-rails/example/config/secret_settings.yml, for example:
   openstax_application_id: '54cc59280662417f2b30c6869baa9c6cb3360c81c4f9d829155d2485d5bcfeed'
   openstax_application_secret: '7ce94d06d7bc8aec4ff81c3f65883300e1e2fa10051e60e58de6d79de91d8608'

5. Set config.openstax_services_url in connect-rails/example/config/initializers/openstax_connect.rb to "https://{server}:3000/" (or the IP address of {server})

6. Start the example application.

7. Go to http://{server}:4000 and click log in

See https://github.com/openstax/connect-rails for full documentation.
""".format(server=env.host)

def example_run():
    with cd('connect-rails/example'):
        with prefix('source {}'.format(RVM)):
            run('rake db:migrate')
            # ctrl-c doesn't kill the rails server so the old server is still running
            run('kill -9 `cat tmp/pids/server.pid`', warn_only=True)
            run('rails server')
