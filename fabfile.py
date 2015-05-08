import signal
import StringIO
import sys
import time

from fabric.api import *
import fabric.contrib.files

DEPLOY_DIR='/opt'
env.use_ssh_config = True
env.hosts = 'default'
env.ssh_config_path ='../../.ssh_config'
env.use_sudo=True
env.cwd= DEPLOY_DIR
RVM = '{}/.rvm/scripts/rvm'.format(DEPLOY_DIR)
PHANTOMJS = '{}/phantomjs-1.9.7-linux-x86_64/bin'.format(DEPLOY_DIR)


def _setup():
    sudo('apt-get update')
    sudo('apt-get install --yes git')
    _setup_rvm()


def _setup_rvm():
    if not fabric.contrib.files.exists(RVM):
        sudo('apt-get install --yes curl')
        sudo('gpg --keyserver hkp://keys.gnupg.net --recv-keys 409B6B1796C275462A1703113804BB82D39DC0E3')
        sudo('wget -q -O - https://get.rvm.io | bash -s -- --ignore-dotfiles')
        sudo('mv /usr/local/rvm/ {}/.rvm'.format(DEPLOY_DIR))
        #run('mv ~/.rvm {}/.rvm'.format(DEPLOY_DIR)) # when not run a root

def _setup_ssl():
    if not fabric.contrib.files.exists('server.crt'):
        sudo('openssl genrsa -des3 -passout pass:x -out server.pass.key 2048')
        sudo('openssl rsa -passin pass:x -in server.pass.key -out server.key')
        sudo('rm server.pass.key')
        sudo('openssl req -new -key server.key -out server.csr')
        sudo('openssl x509 -req -days 365 -in server.csr -signkey server.key -out server.crt')


def _setup_phantomjs():
    if not fabric.contrib.files.exists('phantomjs-1.9.7-linux-x86_64'):
        sudo("wget 'https://bitbucket.org/ariya/phantomjs/downloads/phantomjs-1.9.7-linux-x86_64.tar.bz2'")
        sudo('tar xf phantomjs-1.9.7-linux-x86_64.tar.bz2')


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
    # the nodejs package in trusty is too old for gsudot-cli,
    # so manually installing it here
    if sudo('which node', warn_only=True):
        return
    sudo('apt-get install --yes make g++')
    sudo('wget http://nodejs.org/dist/v0.12.2/node-v0.12.2.tar.gz')
    sudo('tar xf node-v0.12.2.tar.gz')
    with cd('node-v0.12.2'):
        sudo('./configure')
        sudo('make')
        sudo('make install')
    sudo('rm -rf node-v0.12.2*')

def _setup_login_accounts():
    sudo("cat >{}/accounts/config/secret_settings.yml <<EOF\n"
         "# Set up facebook and twitter app id and secret\n"
         "secret_token: 'Hu7aghaiaiPai2ewAix8OoquNoa1cah4'\n"
         "smtp_settings:\n"
         "  address: 'localhost'\n"
         "  port: 25\n"
         "# Facebook OAuth API settings\n"
         "facebook_app_id: '114585082701'\n"
         "facebook_app_secret: '35b6df2c95b8e3bc7bcd46ce47b1ae02'\n"
         "# Twitter OAuth API settings\n"
         "twitter_consumer_key: 'wsSnMNS15nbJRDTqDCDc9IxVs'\n"
         "twitter_consumer_secret: '78OkKbqZbVSGOZcW7Uv6XyTJWKITepl4TeR7rawjkAsBR5pgZ8'\n"
         "# Google OAuth API settings \n"
         "google_client_id: '860946374358-7fvpoadjfpgr2c3d61gca4neatsuhb6a.apps.googleusercontent.com'\n "
         "google_client_secret: '7gr2AYXrs1GneoVm4mKjG98N'\n"
         "EOF\n".format(DEPLOY_DIR))

def accounts_setup(https=''):
    """Set up openstax/accounts"""
    _setup()
    _setup_ssl()
    if not fabric.contrib.files.exists('accounts'):
        if https:
            sudo('git clone https://github.com/openstax/accounts')
        else:
            sudo('git clone git@github.com:openstax/accounts')
    if not _postgres_user_exists('ox_accounts'):
        sudo('psql -d postgres -c "CREATE USER ox_accounts WITH SUPERUSER PASSWORD \'ox_accounts\';"', user='postgres')
    if not _postgres_db_exists('ox_accounts_dev'):
        sudo('createdb -O ox_accounts ox_accounts_dev', user='postgres')
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            sudo('rvm install $(cat .ruby-version)')
            sudo('rvm gemset create accounts')
            sudo('rvm gemset use accounts')
            # FIXME not sure why bundle isn't installed
            sudo('which bundle || gem install bundle')
            sudo('bundle install --without production')
            sudo('gem install unicorn-rails')
            sudo('rake db:setup', warn_only=True)
    _configure_accounts_nginx()
    _setup_login_accounts()
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
            sudo('bundle exec rails console <admin_user.rb')


def _accounts_sudo():
    # Should use accounts_sudo_unicorn
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            sudo('rake db:migrate')
            # ctrl-c doesn't kill the rails server so the old server is still sudoning
            sudo('kill -9 `cat tmp/pids/server.pid`', warn_only=True)
            sudo('rails server')


def _accounts_sudo_ssl():
    # should use accounts_sudo_unicorn
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            sudo('thin start -p 3000 --ssl --ssl-verify --ssl-key-file {0}/server.key --ssl-cert-file {0}/server.crt'.format(DELPOY_DIR))


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
""" % {'home_dir': sudo('pwd')}),
            '/etc/nginx/sites-available/accounts',
            use_sudo=True)
        sudo('ln -sf /etc/nginx/sites-available/accounts '
             '/etc/nginx/sites-enabled/accounts')
        sudo('/etc/init.d/nginx restart')


def accounts_sudo_unicorn():
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
""".format(pwd=sudo('pwd'))), 'config/unicorn.rb')
        with prefix('source {}'.format(RVM)):
            sudo('bundle install')
            sudo('pkill -f unicorn_rails || 0', warn_only=True)
            sudo('rm -f /tmp/unicorn.accounts.sock')
            sudo('unicorn_rails -D -c config/unicorn.rb')


def accounts_test(test_case=None, traceback=''):
    """Run openstax/accounts tests"""
    _setup_phantomjs()
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            if test_case:
                sudo('PATH=$PATH:{} rspec {} {}'.format(PHANTOMJS, traceback and '-b', test_case))
            else:
                if _postgres_db_exists('ox_accounts_test'):
                    sudo('dropdb ox_accounts_test', user='postgres')
                sudo('createdb -O ox_accounts ox_accounts_test', user='postgres')
                sudo('bundle install')
                sudo('RAILS_ENV=test rake db:setup')
                sudo('rake db:migrate')
                sudo('PATH=$PATH:{} rake --trace'.format(PHANTOMJS))


def accounts_routes():
    """Run "rake routes" on openstax/accounts"""
    with cd('accounts'):
        with prefix('source {}'.format(RVM)):
            sudo('rake routes')


def example_setup():
    """Set up openstax/connect-rails (outdated)"""
    _setup()
    sudo('apt-get install --yes nodejs')
    if not fabric.contrib.files.exists('connect-rails'):
        sudo('git clone https://github.com/openstax/connect-rails')
    with cd('connect-rails'):
        with prefix('source {}'.format(RVM)):
            sudo('rvm install ruby-1.9.3-p392')
            sudo('rvm gemset create connect-rails')
            sudo('rvm gemset use connect-rails')
            sudo('bundle install --without production')
    pwd = sudo('pwd')
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
            sudo('rake db:setup', warn_only=True)
            sudo('rake openstax_connect:install:migrations')

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


def example_sudo():
    """Run openstax/connect-rails (outdated)"""
    with cd('connect-rails/example'):
        with prefix('source {}'.format(RVM)):
            sudo('rake db:migrate')
            # ctrl-c doesn't kill the rails server so the old server is still
            # sudoning
            sudo('kill -9 `cat tmp/pids/server.pid`', warn_only=True)
            sudo('rails server')


def accounts_pyramid_setup(https=''):
    """Set up Connexions/openstax-accounts (python)"""
    if not fabric.contrib.files.exists('openstax-accounts'):
        if https:
            sudo('git clone https://github.com/Connexions/openstax-accounts.git')
        else:
            sudo('git clone git@github.com:Connexions/openstax-accounts.git')


def accounts_pyramid_sudo():
    """Run Connexions/openstax-accounts (python)"""
    with cd('openstax-accounts'):
        sudo('./bin/python setup.py install')
        sudo('./bin/pserve development.ini')


def accounts_pyramid_test(test_case=None, display=None, test_all=None):
    """Run Connexions/openstax-accounts (python) tests"""
    if not display:
        sudo('apt-get install --yes xvfb')
        sudo('pkill -f xvfb', warn_only=True)
    if test_case:
        test_case = '-s {}'.format(test_case)
    else:
        test_case = ''
    if not fabric.contrib.files.exists('openstax-accounts'):
        sudo('git clone https://github.com/Connexions/openstax-accounts.git')
    if not fabric.contrib.files.exists('openstax-accounts/chromedriver'):
        with cd('openstax-accounts'):
            if not fabric.contrib.files.exists('chromedriver'):
                sudo("wget 'http://chromedriver.storage.googleapis.com/2.14/chromedriver_linux64.zip'")
                sudo('apt-get install --yes unzip')
                sudo('unzip chromedriver_linux64.zip')
                sudo('rm chromedriver_linux64.zip')
                sudo('apt-get install --yes chromium-browser')
    sudo('apt-get install --yes python-virtualenv')
    with cd('openstax-accounts'):
        if not fabric.contrib.files.exists('bin/python'):
            sudo('virtualenv .')
        env = ['PATH=$PATH:.']
        if display:
            env.append('DISPLAY={}'.format(display))
        sudo('./bin/python setup.py install')
        if test_case:
            sudo('{} {} ./bin/python setup.py test {}'.format(' '.join(env),
                not display and 'xvfb-sudo' or '', test_case))
        elif test_all:
            sudo('{} {} ./bin/python setup.py test -s '
                'openstax_accounts.tests.FunctionalTests'
                .format(' '.join(env), not display and 'xvfb-sudo' or ''))
            env.append('TESTING_INI=test_stub.ini')
            sudo('{} {} ./bin/python setup.py test -s '
                'openstax_accounts.tests.StubTests'
                .format(' '.join(env), not display and 'xvfb-sudo' or ''))
        else:
            env.append('LOCAL_INI=.travis_testing.ini')
            sudo('{} {} ./bin/python setup.py test'
                .format(' '.join(env), not display and 'xvfb-sudo' or ''))

def tutor_deployment_setup():
    if not fabric.contrib.files.exists('tutor-deployment'):
        sudo('git clone -b feature/exercises git@github.com:openstax/tutor-deployment.git')
        sudo('pip install virtualenvwrapper')
    with cd('tutor-deployment'):
        with prefix('WORKON_HOME=$HOME/.environments'):
            with prefix('source /usr/local/bin/virtualenvwrapper_lazy.sh'):
                sudo('mkvirtualenv -p `which python2` tutordep')
                with prefix('workon tutordep'):
                    sudo('pip install -r requirements.txt')

def accounts_deploy(env='qa'):
    with cd('tutor-deployment'):
        with prefix('WORKON_HOME=$HOME/.environments'):
            with prefix('source /usr/local/bin/virtualenvwrapper_lazy.sh'):
                with prefix('workon tutordep'):
                    sudo('ansible-playbook -i environments/{env}/accounts-{env}1 '
                        'accounts_only.yml '
                        '--vault-password-file $HOME/.ssh/vault-accounts-{env}1 '
                        '--private-key $HOME/.ssh/tutor-{env}-kp.pem'.format(env=env))

def openstax_api_setup(https=''):
    if not fabric.contrib.files.exists('openstax_api'):
        if https:
            sudo('git clone https://github.com/openstax/openstax_api.git')
        else:
            sudo('git clone git@github.com:openstax/openstax_api.git')
    with cd('openstax_api'):
        with prefix('source {}'.format(RVM)):
            sudo('rvm install $(cat .ruby-version)')
            sudo('rvm gemset create openstax_api')
            sudo('rvm gemset use openstax_api')

def openstax_api_test():
    with cd('openstax_api'):
        with prefix('source {}'.format(RVM)):
            sudo('rvm gemset use openstax_api')
            sudo('bundle')
            sudo('rake db:migrate')
            sudo('rake')

def biglearn_algs_setup():
    """Set up openstax/biglearn-algs"""
    sudo('apt-get install python-numpy python-scipy')
    sudo('pip install virtualenvwrapper')
    if not fabric.contrib.files.exists('biglearn-algs'):
        sudo('git clone git@github.com:openstax/biglearn-algs.git')
    with cd('biglearn-algs'
            ), prefix('export WORKON_HOME=$HOME/.environments'
                      ), prefix('source /usr/local/bin/virtualenvwrapper.sh'):
        # --system-side-packages includes dist packages (like scipy and
        # numpy) in virtualenv
        sudo('mkvirtualenv -p `which python2` --system-site-packages blapidev')
        with prefix('workon blapidev'):
            sudo('pip install -e .')

def biglearn_algs_test():
    """Run openstax/biglearn-algs tests"""
    with cd('biglearn-algs'):
        with prefix('export WORKON_HOME=$HOME/.environments'):
            with prefix('source /usr/local/bin/virtualenvwrapper.sh'):
                with prefix('workon blapidev'):
                    sudo('python setup.py test')

def biglearn_common_setup():
    """Set up openstax/biglearn-common"""
    sudo('pip install virtualenvwrapper')
    if not fabric.contrib.files.exists('biglearn-common'):
        sudo('git clone git@github.com:openstax/biglearn-common.git')
    with cd('biglearn-common'
            ), prefix('export WORKON_HOME=$HOME/.environments'
                      ), prefix('source /usr/local/bin/virtualenvwrapper.sh'):
        sudo('mkvirtualenv -p `which python2` --system-site-packages blapidev')
        with prefix('workon blapidev'):
            sudo('pip install -e .')

def biglearn_platform_setup():
    """Set up openstax/biglearn-platform"""
    biglearn_common_setup()
    biglearn_algs_setup()
    sudo('pip install virtualenvwrapper')
    if not fabric.contrib.files.exists('biglearn-platform'):
        sudo('git clone git@github.com:openstax/biglearn-platform.git')
    with cd('biglearn-platform/app'
            ), prefix('export WORKON_HOME=$HOME/.environments'
                      ), prefix('source /usr/local/bin/virtualenvwrapper.sh'):
        sudo('mkvirtualenv -p `which python2` --system-site-packages blapidev')
        with prefix('workon blapidev'):
            sudo('pip install -e .')

def tutor_server_setup(https=''):
    """Set up openstax/tutor-server"""
    _setup()
    _install_postgresql()
    sudo('apt-get install --yes qt5-default libqt5webkit5-dev')
    if not fabric.contrib.files.exists('tutor-server'):
        if https:
            sudo('git clone https://github.com/openstax/tutor-server.git')
        else:
            sudo('git clone git@github.com:openstax/tutor-server.git')
    if not _postgres_user_exists('ox_tutor'):
        sudo('psql -d postgres -c "CREATE USER ox_tutor WITH SUPERUSER PASSWORD \'ox_tutor_secret_password\';"', user='postgres')
    if not _postgres_db_exists('ox_tutor_dev'):
        sudo('createdb -O ox_tutor ox_tutor_dev', user='postgres')

    with cd('tutor-server'):
        with prefix('source {}'.format(RVM)):
            sudo('rvm install $(cat .ruby-version)')
            sudo('rvm gemset create $(cat .ruby-gemset)')
            sudo('rvm gemset use $(cat .ruby-gemset)')
            sudo('bundle install --without production')
            sudo('rake db:migrate')
            sudo('rake db:seed')


def tutor_server_sudo():
    """Run rails server on openstax/tutor-server"""
    def sigint_handler(signal, frame):
        if fabric.contrib.files.exists('tmp/pids/server.pid'):
            sudo('kill `cat tmp/pids/server.pid`', warn_only=True)
            sudo('rm -f tmp/pids/server.pid')
        sys.exit(0)
    signal.signal(signal.SIGINT, sigint_handler)

    with cd('tutor-server'):
        with prefix('source {}'.format(RVM)):
            sudo('rake db:migrate')
            sudo('rails server -b 0.0.0.0')


def tutor_server_test(test_case=None):
    """Run openstax/tutor-server tests"""
    if _postgres_db_exists('ox_tutor_test'):
        sudo('dropdb ox_tutor_test', user='postgres')
    sudo('createdb -O ox_tutor ox_tutor_test', user='postgres')

    with cd('tutor-server'):
        with prefix('source {}'.format(RVM)):
            if test_case:
                sudo('rspec -b {}'.format(test_case))
            else:
                sudo('bundle install --without production')
                sudo('rake db:drop && rake db:create && rake db:migrate')
                sudo('rake')


def tutor_js_setup(https=''):
    """Set up openstax/tutor-js"""
    _setup()
    _install_nodejs()
    sudo('npm install -g gulp bower')
    if not fabric.contrib.files.exists('tutor-js'):
        if https:
            sudo('git clone https://github.com/openstax/tutor-js.git')
        else:
            sudo('git clone git@github.com:openstax/tutor-js.git')

    with cd('tutor-js'):
        sudo('npm install')
        sudo('bower install')


def tutor_js_sudo():
    """Run openstax/tutor-js"""
    with cd('tutor-js'):
        sudo('PORT=8001 gulp serve')


def tutor_js_test():
    """Run openstax/tutor-js tests"""
    with cd('tutor-js'):
        sudo('npm install -g gulp bower')
        sudo('bower install')
        sudo('npm install')
        sudo('npm test')


def osc_setup():
    """Set up lml/osc"""
    _setup()
    sudo('apt-get install libxml2-dev libxslt-dev')
    if not fabric.contrib.files.exists('osc'):
        sudo('git clone git@github.com:lml/osc.git')
    with cd('osc'):
        sudo('rm -f .rvmrc')
        with prefix('source {}'.format(RVM)):
            sudo('rvm install $(cat .ruby-version)')
            sudo('rvm gemset create $(cat .ruby-gemset)')
            sudo('rvm gemset use $(cat .ruby-gemset)')
            # Install bundler in case it is not installed
            sudo('which bundle || gem install bundler')
            sudo('bundle install --without production')
            sudo('rake db:setup')

def osc_sudo():
    """Run lml/osc server"""
    with cd('osc'):
        with prefix('source {}'.format(RVM)):
            # ctrl-c doesn't kill the rails server so the old server is still
            # sudoning
            sudo('kill -9 `cat tmp/pids/server.pid`', warn_only=True)
            sudo('rails server -p 3002')

def osc_test(test_case=None):
    """Run lml/osc tests"""
    with cd('osc'):
        with prefix('source {}'.format(RVM)):
            if test_case:
                sudo('rspec -b {}'.format(test_case))
            else:
                sudo('bundle install')
                sudo('rake db:migrate')
                sudo('rake')


def exercises_setup(https=''):
    """Set up openstax/exercises"""
    _setup()
    sudo('apt-get install --yes libicu-dev')
    if not fabric.contrib.files.exists('exercises'):
        if https:
            sudo('git clone https://github.com/openstax/exercises.git')
        else:
            sudo('git clone git@github.com:openstax/exercises.git')
    if not _postgres_user_exists('ox_exercises'):
        sudo('psql -d postgres -c "CREATE USER ox_exercises WITH CREATEDB PASSWORD \'ox_exercises\'"', user='postgres')
    if not _postgres_db_exists('ox_exercises_dev'):
        sudo('createdb -O ox_exercises ox_exercises_dev', user='postgres')
    with cd('exercises'):
        with prefix('source {}'.format(RVM)):
            sudo('rvm install $(cat .ruby-version)')
            sudo('rvm gemset create $(cat .ruby-gemset)')
            sudo('rvm gemset use $(cat .ruby-gemset)')
            sudo('bundle install --without production')
            sudo('rake db:migrate')
            sudo('rake db:seed')


def exercises_sudo():
    """Run openstax/exercises"""
    with cd('exercises'):
        with prefix('source {}'.format(RVM)):
            # ctrl-c doesn't kill the rails server so the old server is still
            # sudoning
            sudo('kill -9 `cat tmp/pids/server.pid`', warn_only=True)
            sudo('rails server')


def exercises_test(test_case=None):
    """Run openstax/exercises tests"""
    if _postgres_db_exists('ox_exercises_test'):
        sudo('dropdb ox_exercises_test', user='postgres')
    sudo('createdb -O ox_exercises ox_exercises_test', user='postgres')
    with cd('exercises'):
        with prefix('source {}'.format(RVM)):
            if test_case:
                sudo('rspec -b {}'.format(test_case))
            else:
                sudo('bundle install --without production')
                sudo('rake db:migrate')
                sudo('rake')


def exchange_setup(https=''):
    """Set up openstax/exchange"""
    _setup()
    if not fabric.contrib.files.exists('exchange'):
        if https:
            sudo('git clone https://github.com/openstax/exchange.git')
        else:
            sudo('git clone git@github.com:openstax/exchange.git')
    if not _postgres_user_exists('ox_exchange'):
        sudo('psql -d postgres -c "CREATE USER ox_exchange WITH CREATEDB PASSWORD \'ox_exchange\'"', user='postgres')
    if not _postgres_db_exists('ox_exchange_dev'):
        sudo('createdb -O ox_exchange ox_exchange_dev', user='postgres')
    with cd('exchange'):
        with prefix('source {}'.format(RVM)):
            sudo('rvm install $(cat .ruby-version)')
            sudo('rvm gemset create $(cat .ruby-gemset)')
            sudo('rvm gemset use $(cat .ruby-gemset)')
            sudo('bundle install --without production')
            sudo('rake db:migrate')
            sudo('rake db:seed')


def exchange_sudo():
    """Run openstax/exchange"""
    with cd('exchange'):
        with prefix('source {}'.format(RVM)):
            # ctrl-c doesn't kill the rails server so the old server is still
            # sudoning
            sudo('kill -9 `cat tmp/pids/server.pid`', warn_only=True)
            sudo('rails server')


def exchange_test(test_case=None):
    """Run openstax/exchange tests"""
    if _postgres_db_exists('ox_exchange_test'):
        sudo('dropdb ox_exchange_test', user='postgres')
    sudo('createdb -O ox_exchange ox_exchange_test', user='postgres')
    with cd('exchange'):
        with prefix('source {}'.format(RVM)):
            if test_case:
                sudo('rspec -b {}'.format(test_case))
            else:
                sudo('bundle install --without production')
                sudo('rake db:migrate')
                sudo('rake')
