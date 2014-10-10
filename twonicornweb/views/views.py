from pyramid.view import view_config, forbidden_view_config
from pyramid.renderers import get_renderer
from pyramid.httpexceptions import HTTPFound
from pyramid.security import remember, forget
from pyramid.session import signed_serialize, signed_deserialize
from pyramid_ldap import get_ldap_connector, groupfinder
from pyramid.response import Response
import ConfigParser
import logging


from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy import func
from sqlalchemy.sql import label
from sqlalchemy import distinct
from sqlalchemy import or_
from sqlalchemy import desc
from twonicornweb.models import (
    DBSession,
    Application,
    Deploy,
    Artifact,
    ArtifactAssignment,
    ArtifactNote,
    Lifecycle,
    Env,
    Repo,
    RepoType,
    ArtifactType,
    RepoUrl,
    )


log = logging.getLogger(__name__)
denied = ''
prod_groups = ['Unix_Team'] # Need to make these configurable
admin_groups = ['Unix_Team'] # Need to make these configurable

# Parse the secret config - would like to pass this from __init__.py
secret_config_file = ConfigParser.ConfigParser()
secret_config_file.read('/app/secrets/twonicorn.conf')
cookie_token = secret_config_file.get('app:main', 'cookie_token')

def site_layout():
    renderer = get_renderer("twonicornweb:templates/global_layout.pt")
    layout = renderer.implementation().macros['layout']
    return layout

def get_user(request):
    """ Gets all the user information for an authenticated  user. Checks groups
        and permissions, and returns a dict of everything. """

    prod_auth = False
    admin_auth = False

    try:
        id = request.authenticated_userid
        (first,last) = format_user(id)
        groups = format_groups(groupfinder(id, request))
        auth = True
        pretty = "%s %s" % (first, last)
    except Exception, e:
        log.error("%s (%s)" % (Exception, e))
        (pretty, id, ad_login, groups, first, last, auth, prd_auth, admin_auth) = ('', '', '', '', '', '', False, False, False)

    try:
        ad_login = validate_username_cookie(request.cookies['un'])
    except:
        return HTTPFound('/logout?message=Your cookie has been tampered with. You have been logged out')

    # Check if the user is authorized to do stuff to prod
    for a in prod_groups:
        if a in groups:
            prod_auth = True
            break

    # Check if the user is authorized as an admin
    for a in admin_groups:
        if a in groups:
            admin_auth = True
            break

    user = {}
    user['id'] = id
    user['ad_login'] = ad_login
    user['groups'] = groups
    user['first'] = first
    user['last'] = last
    user['loggedin'] = auth
    user['prod_auth'] = prod_auth
    user['admin_auth'] = admin_auth
    user['pretty'] = pretty

    return (user)

def format_user(user):
    # Make the name readable
    (last,first,junk) = user.split(',',2)
    last = last.rstrip('\\')
    last = last.strip('CN=')
    return(first,last)

def format_groups(groups):

    formatted = []
    for g in range(len(groups)):
        formatted.append(find_between(groups[g], 'CN=', ',OU='))
    return formatted

def find_between(s, first, last):
    try:
        start = s.index( first ) + len( first )
        end = s.index( last, start )
        return s[start:end]
    except ValueError:
        return ""

def validate_username_cookie(cookieval):
    """ Returns the username if it validates. Otherwise throws
    an exception"""

    return signed_deserialize(cookieval, cookie_token)

@view_config(route_name='logout', renderer='twonicornweb:templates/logout.pt')
def logout(request):

    message = 'You have been logged out'

    try:
        if request.params['message']:
            message = request.params['message']
    except:
        pass

    headers = forget(request)
    # Do I really need this?
    headers.append(('Set-Cookie', 'un=; Max-Age=0; Path=/'))
    request.response.headers = headers

    # No idea why I have to re-define these, but I do or it poops itself
    request.response.content_type = 'text/html'
    request.response.charset = 'UTF-8'
    request.response.status = '200 OK'
    
    return {'message': message}

@view_config(route_name='login', renderer='twonicornweb:templates/login.pt')
@forbidden_view_config(renderer='twonicornweb:templates/login.pt')
def login(request):
    page_title = 'Login'

    user = get_user(request)
    denied = ''

    if request.referer:
        referer_host = request.referer.split('/')[2]
    else:
        referer_host = None

    if request.referer and referer_host == request.host and request.referer.split('/')[3][:6] != 'logout':
        return_url = request.referer
    elif request.path != '/login':
        return_url = request.url
    else:
        return_url = '/applications'

    login = ''
    password = ''
    error = ''

    if 'form.submitted' in request.POST:
        login = request.POST['login']
        password = request.POST['password']
        connector = get_ldap_connector(request)
        data = connector.authenticate(login, password)

        if data is not None:
            dn = data[0]
            encrypted = signed_serialize(login, cookie_token)
            #encrypted = signed_serialize(login, 'titspervert')
            headers = remember(request, dn)
            headers.append(('Set-Cookie', 'un=' + str(encrypted) + '; Max-Age=604800; Path=/'))

            return HTTPFound(request.POST['return_url'], headers=headers)
        else:
            error = 'Invalid credentials'

    if request.authenticated_userid:

        if request.path == '/login':
          error = 'You are already logged in'
          page_title = 'Already Logged In'
          denied = True
        else:
          error = 'You do not have permission to access this page'
          page_title = 'Access Denied'
          denied = True

    return {'layout': site_layout(),
            'page_title': page_title,
            'user': user,
            'return_url': return_url,
            'login': login,
            'password': password,
            'error': error,
            'denied': denied,
           }

@view_config(route_name='home', permission='view', renderer='twonicornweb:templates/home.pt')
def view_home(request):

    page_title = 'Home'
    user = get_user(request)

    return {'layout': site_layout(),
            'page_title': page_title,
            'user': user,
            'denied': denied
           }

@view_config(route_name='applications', permission='view', renderer='twonicornweb:templates/applications.pt')
def view_applications(request):
    page_title = 'Applications'
    user = get_user(request)

    perpage = 10
    offset = 0

    try:
        offset = int(request.GET.getone("start"))
    except:
        pass


    try:
        q = DBSession.query(Application)
        total = q.count()
        applications = q.limit(perpage).offset(offset)
    except Exception, e:
        conn_err_msg = e
        return Response(str(conn_err_msg), content_type='text/plain', status_int=500)


    return {'layout': site_layout(),
            'page_title': page_title,
            'user': user,
            'perpage': perpage,
            'offset': offset,
            'total': total,
            'applications': applications,
            'denied': denied,
           }

@view_config(route_name='deploys', permission='view', renderer='twonicornweb:templates/deploys.pt')
def view_deploys(request):

    page_title = 'Deploys'
    user = get_user(request)

    perpage = 10
    offset = 0
    end = 10
    total = 0

    try:
        offset = int(request.GET.getone("start"))
        end = perpage + offset
    except:
        pass


    params = {'application_id': None,
              'nodegroup': None,
              'history': None,
              'deploy_id': None,
              'env': None,
              'to_env': None,
              'to_state': None,
              'commit': None,
              'artifact_id': None,
             }
    for p in params:
        try:
            params[p] = request.params[p]
        except:
            pass

    application_id = params['application_id']
    nodegroup = params['nodegroup']
    deploy_id = params['deploy_id']
    env = params['env']
    to_env = params['to_env']
    to_state = params['to_state']
    commit = params['commit']
    artifact_id = params['artifact_id']

    try:
        q = DBSession.query(Application)
        q = q.filter(Application.application_id == application_id)
        app = q.one()
    except Exception, e:
        conn_err_msg = e
        return Response(str(conn_err_msg), content_type='text/plain', status_int=500)


    return {'layout': site_layout(),
            'page_title': page_title,
            'user': user,
            'perpage': perpage,
            'offset': offset,
            'total': 1000, #STUB
            'app': app,
            'application_id': application_id,
            'nodegroup': nodegroup,
            'history': None,
            'hist_list': None,
            'env': env,
            'deploy_id': deploy_id,
            'to_env': to_env,
            'to_state': to_state,
            'commit': commit,
            'artifact_id': artifact_id,
            'denied': denied,
           }


@view_config(route_name='promote', permission='view', renderer='twonicornweb:templates/promote.pt')
def view_promote(request):

    page_title = 'Promote'
    user = get_user(request)

    denied = ''
    message = ''
    promote = ''

    params = {'deploy_id': None,
              'artifact_id': None,
              'to_env': None,
              'commit': 'false'
             }
    for p in params:
        try:
            params[p] = request.params[p]
        except:
            pass

    deploy_id = params['deploy_id']
    artifact_id = params['artifact_id']
    to_env = params['to_env']
    commit = params['commit']

    if not user['prod_auth'] and to_env == 'prd':
        to_state = '3'
    else:
        to_state = '2'

    try:
        dq = DBSession.query(Artifact)
        dq = dq.filter(Lifecycle.name == 'current')
        dq = dq.filter(Deploy.deploy_id == '%s' % deploy_id)
        dq = dq.filter(Env.name == 'qat')
        dq = dq.filter(Artifact.artifact_id == '%s' % artifact_id)
        dq = dq.filter(RepoUrl.ct_loc == 'lax1')
        promote = dq.first()
    except Exception, e:
        conn_err_msg = e
        return Response(str(conn_err_msg), content_type='text/plain', status_int=500)


    if artifact_id and commit == 'true':
        if not user['prod_auth'] and to_env == 'prd' and to_state == '2':
            denied = True
            message = 'You do not have permission to perform the promote action on production!'
        else:
            # Actually promoting
            try:
                # Convert the env name to the id
                dq = DBSession.query(Env)
                dq = dq.filter(Env.name == '%s' % to_env)
                env_id=dq.one()

                promote = ArtifactAssignment(deploy_id=deploy_id, artifact_id=artifact_id, env_id=env_id.env_id, lifecycle_id=to_state, user=user['ad_login'])
                DBSession.add(promote)
                DBSession.flush()
                

                dq = DBSession.query(Application)
                dq = dq.filter(Application.application_id == '%s' % application_id)
                return_url = '/deploys?application_id=%s&nodegroup=%s&artifact_id=%s&to_env=%s&to_state=%s&commit=%s' % (results[0][0], results[0][1], artifact_id, to_env, to_state, commit)
                return HTTPFound(return_url)
            except Exception, e:
                log.error("Failed to promote artifact (%s)" % (e))
                

    return {'layout': site_layout(),
            'page_title': page_title,
            'user': user,
            'denied': denied,
            'message': message,
            'deploy_id': deploy_id,
            'artifact_id': artifact_id,
            'to_env': to_env,
            'to_state': to_state,
            'commit': commit,
            'promote': promote,
           }

@view_config(route_name='help', permission='view', renderer='twonicornweb:templates/help.pt')
def view_help(request):

    page_title = 'Help'
    user = get_user(request)

    return {'layout': site_layout(),
            'page_title': page_title,
            'user': user,
            'denied': denied
           }

@view_config(route_name='user', permission='view', renderer='twonicornweb:templates/user.pt')
def view_user(request):

    page_title = 'User Data'
    user = get_user(request)

    return {'layout': site_layout(),
            'page_title': page_title,
            'user': user,
            'denied': denied,
           }

@view_config(route_name='admin', permission='view', renderer='twonicornweb:templates/admin.pt')
def view_admin(request):

    page_title = 'Admin'
    user = get_user(request)

    return {'layout': site_layout(),
            'page_title': page_title,
            'user': user,
            'prod_groups': prod_groups,
            'denied': denied,
           }
