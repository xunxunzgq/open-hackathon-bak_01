# -*- coding: utf-8 -*-
#
# -----------------------------------------------------------------------------------
# Copyright (c) Microsoft Open Technologies (Shanghai) Co. Ltd.  All rights reserved.
#
# The MIT License (MIT)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
# -----------------------------------------------------------------------------------

__author__ = 'Junbo Wang'
__version__ = '2.0'

from flask import Flask
from flask_restful import Api
from flask_cors import CORS
from datetime import timedelta

from util import safe_get_config, get_class, Utility
from hackathon_factory import factory, RequiredFeature
from hackathon_scheduler import HackathonScheduler
from hackathon_response import internal_server_error, bad_request
from log import log


# initialize flask and flask restful
app = Flask(__name__)
app.config['SECRET_KEY'] = safe_get_config("app.secret_key", "secret_key")
app.debug = True
api = Api(app)

# Enable CORS support
app.config['CORS_HEADERS'] = 'Content-Type, token, hackathon_name'
cors = CORS(app)

# initialize hackathon scheduler
scheduler = HackathonScheduler(app)


@app.errorhandler(400)
def bad_request_handler(error):
    log.error(error)
    return bad_request(error.message)


@app.errorhandler(Exception)
def exception_handler(error):
    log.error(error)
    return internal_server_error(error.message)


class Component(object):
    """Base class of business object

    inheritance classes can make use of self.log, self.db and self.util directly without import or instantiating,
    """
    log = RequiredFeature("log")
    db = RequiredFeature("db")
    util = RequiredFeature("util")


class Context(object):
    '''
    A collection of parameters that will be passed through threads/databases
    NEVER put complex object in Context such as instance of db models or business manager
    '''
    props = {}

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            self.props[key] = value

    def __getattr__(self, name):
        if name in self.props:
            return self.props[name]

        raise AttributeError()

    def __setattr__(self, key, value):
        self.props[key] = value

    def __repr__(self):
        return repr(self.props)

    def __contains__(self, key):
        return key in self.props


def init_components():
    from hackathon.database import db_session
    from hackathon.database.db_adapters import SQLAlchemyAdapter
    from hackathon.user.user_mgr import UserManager
    from hackathon.azureformation.fileService import FileService
    from hackathon.azureformation.azureCertManagement import AzureCertManagement
    from hackathon.hack.host_server_mgr import DockerHostManager
    from hackathon.hack import HackathonManager
    from hackathon.registration.register_mgr import RegisterManager
    from hackathon.template.template_mgr import TemplateManager
    from hackathon.admin.admin_mgr import AdminManager
    from hackathon.remote.guacamole import GuacamoleInfo
    from hackathon.expr.expr_mgr import ExprManager
    from hackathon.team.team_mgr import TeamManager

    # dependencies MUST be provided in advance
    factory.provide("util", Utility)
    factory.provide("log", log)
    factory.provide("db", SQLAlchemyAdapter, db_session)

    # scheduler
    factory.provide("scheduler", scheduler)

    # business components
    factory.provide("user_manager", UserManager)
    factory.provide("hackathon_manager", HackathonManager)
    factory.provide("register_manager", RegisterManager)
    factory.provide("file_service", FileService)
    factory.provide("azure_cert_management", AzureCertManagement)
    factory.provide("docker_host_manager", DockerHostManager)
    factory.provide("template_manager", TemplateManager)
    factory.provide("expr_manager", ExprManager)
    factory.provide("admin_manager", AdminManager)
    factory.provide("team_manager", TeamManager)
    factory.provide("guacamole", GuacamoleInfo)

    factory.provide("health_check_mysql", get_class("hackathon.health.health_check.MySQLHealthCheck"))
    factory.provide("health_check_hosted_docker", get_class("hackathon.health.health_check.HostedDockerHealthCheck"))
    factory.provide("health_check_alauda_docker", get_class("hackathon.health.health_check.AlaudaDockerHealthCheck"))
    factory.provide("health_check_guacamole", get_class("hackathon.health.health_check.GuacamoleHealthCheck"))
    factory.provide("health_check_azure", get_class("hackathon.health.health_check.AzureHealthCheck"))

    factory.provide("docker", get_class("hackathon.docker.docker_helper.DockerHelper"))
    factory.provide("hosted_docker", get_class("hackathon.docker.hosted_docker.HostedDockerFormation"))
    factory.provide("alauda_docker", get_class("hackathon.docker.alauda_docker.AlaudaDockerFormation"))

    init_hackathon_storage()


def init_hackathon_storage():
    from hackathon.storage import AzureStorage, LocalStorage

    storage_type = safe_get_config("storage.type", "local")
    if storage_type == "azure":
        factory.provide("storage", AzureStorage)
    else:
        factory.provide("storage", LocalStorage)


def init_schedule_jobs():
    if safe_get_config("environment", "local") == "local":
        return

    import threading

    t = threading.Thread(target=__init_schedule_jobs)
    t.start()


def __init_schedule_jobs():
    sche = RequiredFeature("scheduler")
    expr_manager = RequiredFeature("expr_manager")

    # schedule job to recycle idle experiments
    next_run_time = util.get_now() + timedelta(minutes=1)
    sche.add_interval(feature="expr_manager",
                      method="recycle_expr",
                      id="recycle_expr",
                      next_run_time=next_run_time,
                      minutes=util.safe_get_config("recycle.check_idle_interval_minutes", 10))

    # schedule job to pre-allocate environment
    expr_manager.schedule_pre_allocate_expr_job()

    # schedule job to pull docker images automatically
    if not safe_get_config("docker.alauda.enabled", False):
        docker = RequiredFeature("hosted_docker")
        docker.ensure_images()


def init_app():
    init_components()

    from views import init_routes

    init_routes()
    init_schedule_jobs()


init_app()
