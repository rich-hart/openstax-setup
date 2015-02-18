import StringIO

from fabric.api import *
import fabric.contrib.files

env.use_ssh_config = True
RVM = '~/.rvm/scripts/rvm'
PHANTOMJS = '~/phantomjs-1.9.7-linux-x86_64/bin'

def _setup():
    #sudo('apt-get update')
    sudo('apt-get install --yes git')
    _setup_rvm()

def _setup_rvm():
    if not fabric.contrib.files.exists(RVM):
        sudo('apt-get install --yes curl')
        run('wget --no-check-certificate -q -O - https://get.rvm.io | bash -s -- --ignore-dotfiles')

def _setup_ssl():
    if not fabric.contrib.files.exists('server.crt'):
        run('openssl genrsa -des3 -passout pass:x -out server.pass.key 2048')
        run('openssl rsa -passin pass:x -in server.pass.key -out server.key')
        run('rm server.pass.key')
        run('openssl req -new -key server.key -out server.csr')
        run('openssl x509 -req -days 365 -in server.csr -signkey server.key -out server.crt')

def _setup_phantomjs():
    if not fabric.contrib.files.exists('phantomjs-1.9.7-linux-x86_64'):
        run("wget 'https://bitbucket.org/ariya/phantomjs/downloads/phantomjs-1.9.7-linux-x86_64.tar.bz2'")
        run('tar xf phantomjs-1.9.7-linux-x86_64.tar.bz2')

def _postgres_user_exists(username):
    return '1' in sudo('psql postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname=\'%s\'"' % username, user='postgres')

def _postgres_db_exists(dbname):
    return dbname in sudo('psql -l --pset="pager=off"', user='postgres')

def accounts_setup():
    _setup()
    _setup_ssl()
    if not fabric.contrib.files.exists('accounts'):
        run('git clone https://github.com/openstax/accounts')
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            run('rvm install $(cat .ruby-version)')
            run('rvm gemset create accounts')
            run('rvm gemset use accounts')
            run('bundle install --without production')
            run('rake db:setup', warn_only=True)
    _configure_accounts_nginx()
    print """
To use the facebook and twitter login:

1. Create an app on facebook and twitter

2. Paste the "App ID" and "App Secret" from the facebook app settings page into accounts/config/secret_settings.yml:
   facebook_app_id: '1234567890'
   facebook_app_secret: '1234567890abcdef'

   Paste the "Consumer Key" and "Consumer Secret" from the twitter app settings page into accounts/config/secret_settings.yml:
   twitter_consumer_key: 'xxxxx'
   twitter_consumer_secret: 'yyyyy'

3. Set the callback url on the facebook and twitter app settings page to https://{server}:3000/auth/facebook and https://{server}:3000/auth/twitter respectively. (or the IP address of {server})

""".format(server=env.host)

def accounts_setup_postgres():
    _setup()
    _setup_ssl()
    if not fabric.contrib.files.exists('accounts'):
        run('git clone https://github.com/openstax/accounts')
    if not _postgres_user_exists('accounts'):
        sudo('psql -d postgres -c "CREATE USER accounts WITH SUPERUSER PASSWORD \'accounts\';"', user='postgres')
    if not _postgres_db_exists('accounts'):
        sudo('createdb -O accounts accounts', user='postgres')
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            run('rvm install $(cat .ruby-version)')
            run('rvm gemset create accounts')
            run('rvm gemset use accounts')
            if not fabric.contrib.files.contains('Gemfile', "^gem 'pg'"):
                fabric.contrib.files.append('Gemfile', "gem 'pg'")
            if not fabric.contrib.files.contains('config/database.yml', '#development'):
                fabric.contrib.files.sed('config/database.yml', '^([^#])', r'#\1')
                fabric.contrib.files.append('config/database.yml', '''
development:
  adapter: postgresql
  database: accounts
  username: accounts
  password: accounts
  port: 5432
''')
            run('bundle install --without production')
            run('rake db:setup', warn_only=True)

def accounts_run():
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            run('rake db:migrate')
            # ctrl-c doesn't kill the rails server so the old server is still running
            run('kill -9 `cat tmp/pids/server.pid`', warn_only=True)
            run('rails server')

def accounts_run_ssl():
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            run('thin start -p 3000 --ssl --ssl-verify --ssl-key-file ~/server.key --ssl-cert-file ~/server.crt')

def _configure_accounts_nginx():
    sudo('apt-get install --yes nginx')
    if not fabric.contrib.files.exists('/etc/nginx/sites-available/accounts',
                                       use_sudo=True):
        put(StringIO.StringIO("""\
upstream unicorn {
  server unix:/tmp/unicorn.accounts.sock fail_timeout=0;
}

server {
  listen 3000 default deferred;
  keepalive_timeout 5;
  ssl on;
  ssl_ciphers RC4:HIGH:!aNULL:!MD5;
  ssl_prefer_server_ciphers on;
  ssl_certificate %(home_dir)s/server.crt;
  ssl_certificate_key %(home_dir)s/server.key;
  add_header Strict-Transport-Security "max-age=631138519";
  root %(home_dir)s/accounts/public;
  try_files $uri/index.html $uri @unicorn;

  location @unicorn {
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Host $http_host;
    proxy_redirect off;
    proxy_pass http://unicorn;
  }
}
""" % {'home_dir': run('pwd')}),
            '/etc/nginx/sites-available/accounts',
            use_sudo=True)
        sudo('ln -sf /etc/nginx/sites-available/accounts '
             '/etc/nginx/sites-enabled/accounts')
        sudo('/etc/init.d/nginx restart')

def accounts_run_unicorn():
    with cd('accounts'):
        if not fabric.contrib.files.exists('config/unicorn.rb'):
            put(StringIO.StringIO("""\
working_directory "{pwd}"

pid "{pwd}/unicorn.pid"

stderr_path "{pwd}/log/unicorn.log"
stdout_path "{pwd}/log/unicorn.log"

listen "/tmp/unicorn.accounts.sock"

worker_processes 2

timeout 30
""".format(pwd=run('pwd'))), 'config/unicorn.rb')
        with prefix('source {}'.format(RVM)):
            run('bundle install')
            run('kill -9 `cat unicorn.pid` || 0', warn_only=True)
            run('unicorn_rails -D -c config/unicorn.rb')

def accounts_test(test_case=None, traceback=''):
    _setup_phantomjs()
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            if test_case:
                run('PATH=$PATH:{} rspec {} {}'.format(PHANTOMJS, traceback and '-b', test_case))
            else:
#                run('bundle install')
#                run('RAILS_ENV=test rake db:setup')
                run('PATH=$PATH:{} rake'.format(PHANTOMJS))

def accounts_routes():
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            run('rake routes')

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
To set up openstax/connect-rails with openstax/accounts:

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

def accounts_pyramid_run():
    """Run openstax-accounts
    """
    with cd('openstax-accounts'):
        run('./bin/python setup.py install')
        run('./bin/pserve development.ini')

def accounts_pyramid_test(test_case=None, display=None):
    """Run openstax-accounts tests
    """
    # sudo('apt-get install xvfb')
    if test_case:
        test_case = '-s {}'.format(test_case)
    else:
        test_case = ''
    if not fabric.contrib.files.exists('openstax-accounts'):
        run('git clone git@github.com:karenc/openstax-accounts.git')
    if not fabric.contrib.files.exists('openstax-accounts/chromedriver'):
        with cd('openstax-accounts'):
            run("wget 'http://chromedriver.storage.googleapis.com/2.9/chromedriver_linux64.zip'")
            run('unzip chromedriver_linux64.zip')
            run('rm chromedriver_linux64.zip')
    with cd('openstax-accounts'):
        env = ['PATH=$PATH:.']
        if display:
            env.append('DISPLAY={}'.format(display))
        run('./bin/python setup.py install')
        if test_case:
            run('{} {} ./bin/python setup.py test {}'.format(' '.join(env),
                not display and 'xvfb-run' or '', test_case))
        else:
            env.append('TESTING_INI=test_stub.ini')
            run('{} {} ./bin/python setup.py test -s '
                'openstax_accounts.tests.FunctionalTests.test_stub'
                .format(' '.join(env), not display and 'xvfb-run' or ''))
            env[-1] = 'TESTING_INI=test_local.ini'
            run('{} {} ./bin/python setup.py test'
                .format(' '.join(env), not display and 'xvfb-run' or ''))
