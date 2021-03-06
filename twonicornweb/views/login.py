#  Copyright 2015 CityGrid Media, LLC
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
from pyramid.view import view_config, forbidden_view_config
from pyramid.httpexceptions import HTTPFound
from pyramid.security import remember
from pyramid.session import signed_serialize
from pyramid_ldap import get_ldap_connector
import logging
from twonicornweb.views import (
    site_layout,
    local_authenticate,
    get_user,
    )

log = logging.getLogger(__name__)


@view_config(route_name='login', renderer='twonicornweb:templates/login.pt')
@forbidden_view_config(renderer='twonicornweb:templates/login.pt')
def login(request):
    page_title = 'Login'

    user = get_user(request)

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

        # AD/LDAP
        if request.registry.settings['tcw.auth_mode'] == 'ldap':
            connector = get_ldap_connector(request)
            data = connector.authenticate(login, password)
        # LOCAL
        else:
            data = local_authenticate(login, password)
            
        if data is not None:
            dn = data[0]
            encrypted = signed_serialize(login, request.registry.settings['tcw.cookie_token'])
            headers = remember(request, dn)
            headers.append(('Set-Cookie', 'un=' + str(encrypted) + '; Max-Age=604800; Path=/'))

            return HTTPFound(request.POST['return_url'], headers=headers)
        else:
            error = 'Invalid credentials'

    if request.authenticated_userid:

        if request.path == '/login':
          error = 'You are already logged in'
          page_title = 'Already Logged In'
        else:
          error = 'You do not have permission to access this page'
          page_title = 'Access Denied'

    return {'layout': site_layout(),
            'page_title': page_title,
            'user': user,
            'return_url': return_url,
            'login': login,
            'password': password,
            'error': error,
           }
