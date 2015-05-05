import StringIO
import time

from fabric.api import *
import fabric.contrib.files

env.use_ssh_config = True
RVM = '~/.rvm/scripts/rvm'
PHANTOMJS = '~/phantomjs-1.9.7-linux-x86_64/bin'


def _setup():
    sudo('apt-get update')
    sudo('apt-get install --yes git')
    _setup_rvm()


def _setup_rvm():
    if not fabric.contrib.files.exists(RVM):
        sudo('apt-get install --yes curl')
        run('gpg --keyserver hkp://keys.gnupg.net --recv-keys 409B6B1796C275462A1703113804BB82D39DC0E3')
        run('wget -q -O - https://get.rvm.io | bash -s -- --ignore-dotfiles')


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


def _install_postgresql():
    sudo('apt-get install --yes postgresql-9.3 postgresql-server-dev-9.3 postgresql-client-9.3 postgresql-contrib-9.3 postgresql-plpython-9.3 libpq-dev')
    fabric.contrib.files.sed('/etc/postgresql/9.3/main/pg_hba.conf', '^local\s*all\s*all\s*peer\s*$', 'local all all md5', use_sudo=True)
    sudo('/etc/init.d/postgresql restart')


def _postgres_user_exists(username):
    return '1' in sudo('psql postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname=\'%s\'"' % username, user='postgres')


def _postgres_db_exists(dbname):
    return ' {} '.format(dbname) in sudo('psql -l --pset="pager=off"', user='postgres')


def _postgres_user_exists(username):
    return '1' in sudo('psql postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname=\'%s\'"' % username, user='postgres')


def _postgres_db_exists(dbname):
    return dbname in sudo('psql -l --pset="pager=off"', user='postgres')


def _install_nodejs():
    # the nodejs package in trusty is too old for grunt-cli,
    # so manually installing it here
    if run('which node', warn_only=True):
        return
    sudo('apt-get install --yes make g++')
    run('wget http://nodejs.org/dist/v0.12.2/node-v0.12.2.tar.gz')
    run('tar xf node-v0.12.2.tar.gz')
    with cd('node-v0.12.2'):
        run('./configure')
        run('make')
        sudo('make install')
    run('rm -rf node-v0.12.2*')


def accounts_setup(https=''):
    """Set up openstax/accounts"""
    _setup()
    _setup_ssl()
    if not fabric.contrib.files.exists('accounts'):
        if https:
            run('git clone https://github.com/openstax/accounts')
        else:
            run('git clone git@github.com:openstax/accounts')
    if not _postgres_user_exists('accounts'):
        sudo('psql -d postgres -c "CREATE USER accounts WITH SUPERUSER PASSWORD \'accounts\';"', user='postgres')
    if not _postgres_db_exists('accounts'):
        sudo('createdb -O accounts accounts', user='postgres')
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            run('rvm install $(cat .ruby-version)')
            run('rvm gemset create accounts')
            run('rvm gemset use accounts')
            # FIXME not sure why bundle isn't installed
            run('which bundle || gem install bundle')
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
                if _postgres_db_exists('accounts-testing'):
                    sudo('dropdb accounts-testing', user='postgres')
                sudo('createdb -O accounts accounts-testing', user='postgres')
                run('bundle install')
                run('RAILS_ENV=test rake db:setup')
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
            env.append('LOCAL_INI=.travis_testing.ini')
            run('{} {} ./bin/python setup.py test'
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

def tutor_server_setup(https=''):
    """Set up openstax/tutor-server"""
    _setup()
    _install_postgresql()
    sudo('apt-get install --yes qt5-default libqt5webkit5-dev')
    if not fabric.contrib.files.exists('tutor-server'):
        if https:
            run('git clone https://github.com/openstax/tutor-server.git')
        else:
            run('git clone git@github.com:openstax/tutor-server.git')
    if not _postgres_user_exists('ox_tutor'):
        sudo('psql -d postgres -c "CREATE USER ox_tutor WITH SUPERUSER PASSWORD \'ox_tutor_secret_password\';"', user='postgres')
    if not _postgres_db_exists('ox_tutor_dev'):
        sudo('createdb -O ox_tutor ox_tutor_dev', user='postgres')

    with cd('tutor-server'):
        with prefix('source {}'.format(RVM)):
            run('rvm install $(cat .ruby-version)')
            run('rvm gemset create $(cat .ruby-gemset)')
            run('rvm gemset use $(cat .ruby-gemset)')
            run('bundle install --without production')
            run('rake db:migrate')
            run('rake db:seed')


def tutor_server_run():
    """Run rails server on openstax/tutor-server"""
    with cd('tutor-server'):
        with prefix('source {}'.format(RVM)):
            if fabric.contrib.files.exists('tmp/pids/server.pid'):
                run('kill `cat tmp/pids/server.pid`', warn_only=True)
                run('rm -f tmp/pids/server.pid')
            run('rake db:migrate')
            run('rails server -b 0.0.0.0')


def tutor_server_test(test_case=None):
    """Run openstax/tutor-server tests"""
    if _postgres_db_exists('ox_tutor_test'):
        sudo('dropdb ox_tutor_test', user='postgres')
    sudo('createdb -O ox_tutor ox_tutor_test', user='postgres')

    with cd('tutor-server'):
        with prefix('source {}'.format(RVM)):
            if test_case:
                run('rspec -b {}'.format(test_case))
            else:
                run('bundle install --without production')
                run('rake db:drop && rake db:create && rake db:migrate')
                run('rake')


def tutor_js_setup(https=''):
    """Set up openstax/tutor-js"""
    _setup()
    _install_nodejs()
    sudo('npm install -g gulp bower')
    if not fabric.contrib.files.exists('tutor-js'):
        if https:
            run('git clone https://github.com/openstax/tutor-js.git')
        else:
            run('git clone git@github.com:openstax/tutor-js.git')

    with cd('tutor-js'):
        run('npm install')
        run('bower install')


def tutor_js_run():
    """Run openstax/tutor-js"""
    with cd('tutor-js'):
        run('PORT=8001 gulp serve')


def osc_setup():
    """Set up lml/osc"""
    _setup()
    sudo('apt-get install libxml2-dev libxslt-dev')
    if not fabric.contrib.files.exists('osc'):
        run('git clone git@github.com:lml/osc.git')
    with cd('osc'):
        run('rm -f .rvmrc')
        with prefix('source {}'.format(RVM)):
            run('rvm install $(cat .ruby-version)')
            run('rvm gemset create $(cat .ruby-gemset)')
            run('rvm gemset use $(cat .ruby-gemset)')
            # Install bundler in case it is not installed
            run('which bundle || gem install bundler')
            run('bundle install --without production')
            run('rake db:setup')

def osc_run():
    """Run lml/osc server"""
    with cd('osc'):
        with prefix('source {}'.format(RVM)):
            # ctrl-c doesn't kill the rails server so the old server is still
            # running
            run('kill -9 `cat tmp/pids/server.pid`', warn_only=True)
            run('rails server -p 3002')

def osc_test(test_case=None):
    """Run lml/osc tests"""
    with cd('osc'):
        with prefix('source {}'.format(RVM)):
            if test_case:
                run('rspec -b {}'.format(test_case))
            else:
                run('bundle install')
                run('rake db:migrate')
                run('rake')


def exercises_setup(https=''):
    """Set up openstax/exercises"""
    _setup()
    sudo('apt-get install --yes libicu-dev')
    if not fabric.contrib.files.exists('exercises'):
        if https:
            run('git clone https://github.com/openstax/exercises.git')
        else:
            run('git clone git@github.com:openstax/exercises.git')
    if not _postgres_user_exists('ox_exercises'):
        sudo('psql -d postgres -c "CREATE USER ox_exercises WITH CREATEDB PASSWORD \'ox_exercises\'"', user='postgres')
    if not _postgres_db_exists('ox_exercises_dev'):
        sudo('createdb -O ox_exercises ox_exercises_dev', user='postgres')
    with cd('exercises'):
        with prefix('source {}'.format(RVM)):
            run('rvm install $(cat .ruby-version)')
            run('rvm gemset create $(cat .ruby-gemset)')
            run('rvm gemset use $(cat .ruby-gemset)')
            run('bundle install --without production')
            run('rake db:migrate')
            run('rake db:seed')


def exercises_run():
    """Run openstax/exercises"""
    with cd('exercises'):
        with prefix('source {}'.format(RVM)):
            # ctrl-c doesn't kill the rails server so the old server is still
            # running
            run('kill -9 `cat tmp/pids/server.pid`', warn_only=True)
            run('rails server')


def exercises_test(test_case=None):
    """Run openstax/exercises tests"""
    if _postgres_db_exists('ox_exercises_test'):
        sudo('dropdb ox_exercises_test', user='postgres')
    sudo('createdb -O ox_exercises ox_exercises_test', user='postgres')
    with cd('exercises'):
        with prefix('source {}'.format(RVM)):
            if test_case:
                run('rspec -b {}'.format(test_case))
            else:
                run('bundle install --without production')
                run('rake db:migrate')
                run('rake')


def exchange_setup(https=''):
    """Set up openstax/exchange"""
    _setup()
    if not fabric.contrib.files.exists('exchange'):
        if https:
            run('git clone https://github.com/openstax/exchange.git')
        else:
            run('git clone git@github.com:openstax/exchange.git')
    if not _postgres_user_exists('ox_exchange'):
        sudo('psql -d postgres -c "CREATE USER ox_exchange WITH CREATEDB PASSWORD \'ox_exchange\'"', user='postgres')
    if not _postgres_db_exists('ox_exchange_dev'):
        sudo('createdb -O ox_exchange ox_exchange_dev', user='postgres')
    with cd('exchange'):
        with prefix('source {}'.format(RVM)):
            run('rvm install $(cat .ruby-version)')
            run('rvm gemset create $(cat .ruby-gemset)')
            run('rvm gemset use $(cat .ruby-gemset)')
            run('bundle install --without production')
            run('rake db:migrate')
            run('rake db:seed')


def exchange_run():
    """Run openstax/exchange"""
    with cd('exchange'):
        with prefix('source {}'.format(RVM)):
            # ctrl-c doesn't kill the rails server so the old server is still
            # running
            run('kill -9 `cat tmp/pids/server.pid`', warn_only=True)
            run('rails server')


def exchange_test(test_case=None):
    """Run openstax/exchange tests"""
    if _postgres_db_exists('ox_exchange_test'):
        sudo('dropdb ox_exchange_test', user='postgres')
    sudo('createdb -O ox_exchange ox_exchange_test', user='postgres')
    with cd('exchange'):
        with prefix('source {}'.format(RVM)):
            if test_case:
                run('rspec -b {}'.format(test_case))
            else:
                run('bundle install --without production')
                run('rake db:migrate')
                run('rake')
