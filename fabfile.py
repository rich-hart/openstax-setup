import StringIO
import time

from fabric.api import *
import fabric.contrib.files

#env.use_ssh_config = True
RVM = '$DEPLOY_DIR/.rvm/scripts/rvm'
PHANTOMJS = '$DEPLOY_DIR/phantomjs-1.9.7-linux-x86_64/bin'


def _setup():
    sudo('apt-get update')
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


def accounts_setup(https=''):
    """Set up openstax/accounts"""
    _setup()
    _setup_ssl()
    if not fabric.contrib.files.exists('accounts'):
        if https:
            run('git clone https://github.com/openstax/accounts')
        else:
            run('git clone git@github.com:openstax/accounts')
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            run('rvm install $(cat .ruby-version)')
            run('rvm gemset create accounts')
            run('rvm gemset use accounts')
            run('bundle install --without production')
            run('gem install unicorn-rails')
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


def accounts_setup_postgres(https=''):
    """Set up openstax/accounts using postgres db"""
    _setup()
    _setup_ssl()
    sudo('apt-get install --yes postgresql libpq-dev')
    if not sudo('grep "^local\s*all\s*all\s*md5" '
                '/etc/postgresql/*/main/pg_hba.conf', warn_only=True):
        fabric.contrib.files.sed(
            '/etc/postgresql/*/main/pg_hba.conf',
            '^local\s+all\s+all\s+peer\s*',
            'local all all md5',
            use_sudo=True)
        sudo('/etc/init.d/postgresql restart')
    if not fabric.contrib.files.exists('accounts'):
        if https:
            run('git clone https://github.com/openstax/accounts')
        else:
            run('git clone git@github.com:openstax/accounts.git')
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
            run('gem install unicorn-rails')
            run('rake db:setup', warn_only=True)
    _configure_accounts_nginx()


def accounts_create_admin_user(username='admin', password='password'):
    """Create an admin user in accounts (default admin/password)
    """
    print('Creating admin user with username {} and password {}'.format(
        username, password))
    with cd('accounts'):
        put(StringIO.StringIO("""\
user = FactoryGirl.create :user, :admin, :terms_agreed, username: '{}'
identity = FactoryGirl.create :identity, user: user, password: '{}'
FactoryGirl.create :authentication, provider: 'identity', uid: identity.id.to_s, user: user
""".format(username, password)), 'admin_user.rb')
        with prefix('source {}'.format(RVM)):
            run('bundle exec rails console <admin_user.rb')


def _accounts_run():
    # Should use accounts_run_unicorn
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            run('rake db:migrate')
            # ctrl-c doesn't kill the rails server so the old server is still running
            run('kill -9 `cat tmp/pids/server.pid`', warn_only=True)
            run('rails server')


def _accounts_run_ssl():
    # should use accounts_run_unicorn
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            run('thin start -p 3000 --ssl --ssl-verify --ssl-key-file $DEPLOY_DIR/server.key --ssl-cert-file $DEPLOY_DIR/server.crt')


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
    """Run openstax/accounts using unicorn_rails"""
    with cd('accounts'):
        if not fabric.contrib.files.exists('config/unicorn.rb'):
            put(StringIO.StringIO("""\
working_directory "{pwd}"

pid "{pwd}/unicorn.pid"

stderr_path "{pwd}/log/unicorn.log"
stdout_path "{pwd}/log/unicorn.log"

listen "/tmp/unicorn.accounts.sock"

worker_processes 1

timeout 30
""".format(pwd=run('pwd'))), 'config/unicorn.rb')
        with prefix('source {}'.format(RVM)):
            run('bundle install')
            run('pkill -f unicorn_rails || 0', warn_only=True)
            run('rm -f /tmp/unicorn.accounts.sock')
            run('unicorn_rails -D -c config/unicorn.rb')


def accounts_test(test_case=None, traceback=''):
    """Run openstax/accounts tests"""
    _setup_phantomjs()
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            if test_case:
                run('PATH=$PATH:{} rspec {} {}'.format(PHANTOMJS, traceback and '-b', test_case))
            else:
                run('bundle install')
                run('rake db:migrate')
                run('PATH=$PATH:{} rake --trace'.format(PHANTOMJS))


def accounts_routes():
    """Run "rake routes" on openstax/accounts"""
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            run('rake routes')


def example_setup():
    """Set up openstax/connect-rails (outdated)"""
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
        fabric.contrib.files.sed(
            filename,
            'OpenStax::Connect.configuration.openstax_application_secret',
            'OpenStax::Connect.configuration.openstax_application_secret, '
            '{:client_options => {:ssl => {:ca_file => "%s/server.crt"}}}'
            % pwd)
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
    """Run openstax/connect-rails (outdated)"""
    with cd('connect-rails/example'):
        with prefix('source {}'.format(RVM)):
            run('rake db:migrate')
            # ctrl-c doesn't kill the rails server so the old server is still
            # running
            run('kill -9 `cat tmp/pids/server.pid`', warn_only=True)
            run('rails server')


def accounts_pyramid_setup(https=''):
    """Set up Connexions/openstax-accounts (python)"""
    if not fabric.contrib.files.exists('openstax-accounts'):
        if https:
            run('git clone https://github.com/Connexions/openstax-accounts.git')
        else:
            run('git clone git@github.com:Connexions/openstax-accounts.git')


def accounts_pyramid_run():
    """Run Connexions/openstax-accounts (python)"""
    with cd('openstax-accounts'):
        run('./bin/python setup.py install')
        run('./bin/pserve development.ini')


def accounts_pyramid_test(test_case=None, display=None, test_all=None):
    """Run Connexions/openstax-accounts (python) tests"""
    if not display:
        sudo('apt-get install --yes xvfb')
        run('pkill -f xvfb', warn_only=True)
    if test_case:
        test_case = '-s {}'.format(test_case)
    else:
        test_case = ''
    if not fabric.contrib.files.exists('openstax-accounts'):
        run('git clone https://github.com/Connexions/openstax-accounts.git')
    if not fabric.contrib.files.exists('openstax-accounts/chromedriver'):
        with cd('openstax-accounts'):
            if not fabric.contrib.files.exists('chromedriver'):
                run("wget 'http://chromedriver.storage.googleapis.com/2.14/chromedriver_linux64.zip'")
                sudo('apt-get install --yes unzip')
                run('unzip chromedriver_linux64.zip')
                run('rm chromedriver_linux64.zip')
                sudo('apt-get install --yes chromium-browser')
    sudo('apt-get install --yes python-virtualenv')
    with cd('openstax-accounts'):
        if not fabric.contrib.files.exists('bin/python'):
            run('virtualenv .')
        env = ['PATH=$PATH:.']
        if display:
            env.append('DISPLAY={}'.format(display))
        run('./bin/python setup.py install')
        if test_case:
            run('{} {} ./bin/python setup.py test {}'.format(' '.join(env),
                not display and 'xvfb-run' or '', test_case))
        elif test_all:
            run('{} {} ./bin/python setup.py test -s '
                'openstax_accounts.tests.FunctionalTests'
                .format(' '.join(env), not display and 'xvfb-run' or ''))
            env.append('TESTING_INI=test_stub.ini')
            run('{} {} ./bin/python setup.py test -s '
                'openstax_accounts.tests.StubTests'
                .format(' '.join(env), not display and 'xvfb-run' or ''))
        else:
            env.append('TESTING_INI=test_stub.ini')
            run('{} {} ./bin/python setup.py test -s '
                'openstax_accounts.tests.StubTests'
                .format(' '.join(env), not display and 'xvfb-run' or ''))
            time.sleep(1)
            env[-1] = 'TESTING_INI=test_local.ini'
            run('{} {} ./bin/python setup.py test -s '
                'openstax_accounts.tests.FunctionalTests.test_local'
                .format(' '.join(env), not display and 'xvfb-run' or ''))

def tutor_deployment_setup():
    if not fabric.contrib.files.exists('tutor-deployment'):
        run('git clone -b feature/exercises git@github.com:openstax/tutor-deployment.git')
        sudo('pip install virtualenvwrapper')
    with cd('tutor-deployment'):
        with prefix('WORKON_HOME=$HOME/.environments'):
            with prefix('source /usr/local/bin/virtualenvwrapper_lazy.sh'):
                run('mkvirtualenv -p `which python2` tutordep')
                with prefix('workon tutordep'):
                    run('pip install -r requirements.txt')

def accounts_deploy(env='qa'):
    with cd('tutor-deployment'):
        with prefix('WORKON_HOME=$HOME/.environments'):
            with prefix('source /usr/local/bin/virtualenvwrapper_lazy.sh'):
                with prefix('workon tutordep'):
                    run('ansible-playbook -i environments/{env}/accounts-{env}1 '
                        'accounts_only.yml '
                        '--vault-password-file $HOME/.ssh/vault-accounts-{env}1 '
                        '--private-key $HOME/.ssh/tutor-{env}-kp.pem'.format(env=env))

def openstax_api_setup(https=''):
    if not fabric.contrib.files.exists('openstax_api'):
        if https:
            run('git clone https://github.com/openstax/openstax_api.git')
        else:
            run('git clone git@github.com:openstax/openstax_api.git')
    with cd('openstax_api'):
        with prefix('source {}'.format(RVM)):
            run('rvm install $(cat .ruby-version)')
            run('rvm gemset create openstax_api')
            run('rvm gemset use openstax_api')

def openstax_api_test():
    with cd('openstax_api'):
        with prefix('source {}'.format(RVM)):
            run('rvm gemset use openstax_api')
            run('bundle')
            run('rake db:migrate')
            run('rake')

def biglearn_algs_setup():
    """Set up openstax/biglearn-algs"""
    sudo('apt-get install python-numpy python-scipy')
    sudo('pip install virtualenvwrapper')
    if not fabric.contrib.files.exists('biglearn-algs'):
        run('git clone git@github.com:openstax/biglearn-algs.git')
    with cd('biglearn-algs'
            ), prefix('export WORKON_HOME=$HOME/.environments'
                      ), prefix('source /usr/local/bin/virtualenvwrapper.sh'):
        # --system-side-packages includes dist packages (like scipy and
        # numpy) in virtualenv
        run('mkvirtualenv -p `which python2` --system-site-packages blapidev')
        with prefix('workon blapidev'):
            run('pip install -e .')

def biglearn_algs_test():
    """Run openstax/biglearn-algs tests"""
    with cd('biglearn-algs'):
        with prefix('export WORKON_HOME=$HOME/.environments'):
            with prefix('source /usr/local/bin/virtualenvwrapper.sh'):
                with prefix('workon blapidev'):
                    run('python setup.py test')

def biglearn_common_setup():
    """Set up openstax/biglearn-common"""
    sudo('pip install virtualenvwrapper')
    if not fabric.contrib.files.exists('biglearn-common'):
        run('git clone git@github.com:openstax/biglearn-common.git')
    with cd('biglearn-common'
            ), prefix('export WORKON_HOME=$HOME/.environments'
                      ), prefix('source /usr/local/bin/virtualenvwrapper.sh'):
        run('mkvirtualenv -p `which python2` --system-site-packages blapidev')
        with prefix('workon blapidev'):
            run('pip install -e .')

def biglearn_platform_setup():
    """Set up openstax/biglearn-platform"""
    biglearn_common_setup()
    biglearn_algs_setup()
    sudo('pip install virtualenvwrapper')
    if not fabric.contrib.files.exists('biglearn-platform'):
        run('git clone git@github.com:openstax/biglearn-platform.git')
    with cd('biglearn-platform/app'
            ), prefix('export WORKON_HOME=$HOME/.environments'
                      ), prefix('source /usr/local/bin/virtualenvwrapper.sh'):
        run('mkvirtualenv -p `which python2` --system-site-packages blapidev')
        with prefix('workon blapidev'):
            run('pip install -e .')
