# Copyright 2017 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# Licensed under the Amazon Software License (the "License"). You may not use this file except in compliance with the License. A copy of the License is located at
#     http://aws.amazon.com/asl/
# or in the "license" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and limitations under the License.
import os
from pynt import task
import http.server
import socketserver

# # Output key value and invoke url to apigw.js
# apigw_js = open('%ssrc/apigw.js' % web_build_dir, 'w')
# apigw_js.write('var apiBaseUrl="%s";\nvar apiKey="%s";\n' % (api_base_url, api_key_value))
# apigw_js.close()

@task()
def webuiserver(webdir="web-ui/",port=8080):
    '''Start a local lightweight HTTP server to serve the Web UI.'''
    web_build_dir = 'build/%s' % webdir

    os.chdir(web_build_dir)
    
    Handler = http.server.SimpleHTTPRequestHandler

    httpd = socketserver.TCPServer(("0.0.0.0", port), Handler)

    print("Starting local Web UI Server in directory '%s' on port %s" % (web_build_dir, port))
    
    httpd.serve_forever()
    
    return



