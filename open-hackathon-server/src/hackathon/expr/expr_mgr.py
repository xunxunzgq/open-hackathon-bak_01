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

import sys

sys.path.append("..")

from hackathon import (
    Component,
    RequiredFeature,
)

from hackathon.constants import (
    EStatus,
    VERemoteProvider,
    VE_PROVIDER,
    PortBindingType,
    VEStatus,
    ReservedUser,
    AVMStatus,
)
from hackathon.database.models import (
    VirtualEnvironment,
    DockerHostServer,
    Experiment,
    Hackathon,
    Template,
    User
)

from hackathon.azureformation.azureFormation import (
    AzureFormation,
)

from hackathon.hackathon_response import (
    internal_server_error,
    precondition_failed,
    not_found,
    access_denied,
    ok,
)

from hackathon.template.docker_template_unit import (
    DockerTemplateUnit,
)
from hackathon.template.base_template import (
    BaseTemplate,
)
import json
import random
import string
from sqlalchemy import (
    and_,
)

from datetime import timedelta
from hackathon.constants import CLOUD_ECLIPSE


class ExprManager(Component):
    register_manager = RequiredFeature("register_manager")
    user_manager = RequiredFeature("user_manager")
    hackathon_manager = RequiredFeature("hackathon_manager")
    template_manager = RequiredFeature("template_manager")
    docker = RequiredFeature("docker")
    scheduler = RequiredFeature("scheduler")

    def start_expr(self, hackathon_name, template_name, user_id):
        """
        A user uses a template to start a experiment under a hackathon
        :param hackathon_name:
        :param template_name:
        :param user_id:
        :return:
        """
        hack_temp = self.__check_template_status(hackathon_name, template_name)
        if hack_temp is None:
            return not_found('hackathon or template is not existed')

        hackathon = hack_temp[0]
        if not self.register_manager.is_user_registered(user_id, hackathon):
            return access_denied("user not registered or not approved")

        if hackathon.event_end_time < self.util.get_now():
            self.log.warn("hackathon is ended. The expr starting process will be stopped")
            return precondition_failed('hackathen is ended')

        template = hack_temp[1]

        if user_id > 0:
            expr = self.__check_expr_status(user_id, hackathon, template)
            if expr is not None:
                return self.__report_expr_status(expr)

        # new expr
        return self.__start_new_expr(hackathon, template, user_id)

    def heart_beat(self, expr_id):
        expr = self.db.find_first_object_by(Experiment, id=expr_id, status=EStatus.RUNNING)
        if expr is None:
            return not_found('Experiment does not running')

        expr.last_heart_beat_time = self.util.get_now()
        self.db.commit()
        return ok('OK')

    def stop_expr(self, expr_id, force=0):
        """
        :param expr_id: experiment id
        :param force: 0: only stop container and release ports, 1: force stop and delete container and release ports.
        :return:
        """
        self.log.debug("begin to stop %d" % expr_id)
        expr = self.db.find_first_object_by(Experiment, id=expr_id, status=EStatus.RUNNING)
        if expr is not None:
            # Docker
            docker = self.docker.get_docker(expr.hackathon)
            if expr.template.provider == VE_PROVIDER.DOCKER:
                # stop containers
                for c in expr.virtual_environments.all():
                    try:
                        self.log.debug("begin to stop %s" % c.name)
                        if force:
                            docker.delete(c.name, virtual_environment=c, container=c.container, expr_id=expr_id)
                            c.status = VEStatus.DELETED
                        else:
                            docker.stop(c.name, virtual_environment=c, container=c.container, expr_id=expr_id)
                            c.status = VEStatus.STOPPED
                    except Exception as e:
                        self.log.error(e)
                        self.__roll_back(expr_id)
                        return internal_server_error('Failed stop/delete container')
                if force:
                    expr.status = EStatus.DELETED
                else:
                    expr.status = EStatus.STOPPED
                self.db.commit()
            else:
                try:
                    # todo support delete azure vm
                    hosted_docker = RequiredFeature("hosted_docker")
                    af = AzureFormation(hosted_docker.load_azure_key_id(expr_id))
                    af.stop(expr_id, AVMStatus.STOPPED_DEALLOCATED)
                except Exception as e:
                    self.log.error(e)
                    return internal_server_error('Failed stopping azure')

            self.log.debug("experiment %d ended success" % expr_id)
            return ok('OK')
        else:
            return ok('expr not exist')

    def get_expr_status(self, expr_id):
        expr = self.db.find_first_object_by(Experiment, id=expr_id)
        if expr is not None:
            return self.__report_expr_status(expr)
        else:
            return not_found('Experiment Not found')

    def get_expr_list_by_user_id(self, user_id):
        return map(lambda u: u.dic(),
                   self.db.find_all_objects(Experiment, and_(Experiment.user_id == user_id,
                                                             Experiment.status < 5)))

    def get_expr_list_by_hackathon_id(self, hackathon_id, **kwargs):
        condition = self.__get_filter_condition(hackathon_id, **kwargs)
        experiments = self.db.find_all_objects(Experiment, condition)
        return map(lambda u: self.__get_expr_with_user_info(u), experiments)

    def recycle_expr(self):
        """
        recycle experiment when idle more than 24 hours
        :return:
        """
        self.log.debug("start checking recyclable experiment ... ")

        recycle_hours = self.util.safe_get_config('recycle.idle_hours', 24)
        expr_time_cond = Experiment.last_heart_beat_time + timedelta(hours=recycle_hours) > self.util.get_now()
        recycle_cond = Experiment.hackathon_id.in_(self.hackathon_manager.get_recyclable_hackathon_list())
        status_cond = Experiment.status == EStatus.RUNNING
        r = self.db.find_first_object(Experiment, status_cond, expr_time_cond, recycle_cond)

        if r is not None:
            self.stop_expr(r.id)
            self.log.debug("it's stopping " + str(r.id) + " inactive experiment now")
        else:
            self.log.debug("There is now inactive experiment now")
            return

    def schedule_pre_allocate_expr_job(self):
        next_run_time = self.util.get_now() + timedelta(seconds=1)
        self.scheduler.add_interval(feature="expr_manager",
                                    method="pre_allocate_expr",
                                    id="pre_allocate_expr",
                                    next_run_time=next_run_time,
                                    minutes=self.util.safe_get_config("pre_allocate.check_interval_minutes", 5))

    def pre_allocate_expr(self):
        # only deal with online hackathons
        hackathon_id_list = self.hackathon_manager.get_pre_allocate_enabled_hackathon_list()
        templates = self.db.find_all_objects(Template, Template.hackathon_id.in_(hackathon_id_list))
        for template in templates:
            try:
                pre_num = self.hackathon_manager.get_pre_allocate_number(template.hackathon)
                curr_num = self.db.count(Experiment,
                                         Experiment.user_id == ReservedUser.DefaultUserID,
                                         Experiment.template_id == template.id,
                                         (Experiment.status == EStatus.STARTING) | (
                                             Experiment.status == EStatus.RUNNING))
                # todo test azure, config num
                if template.provider == VE_PROVIDER.AZURE:
                    if curr_num < pre_num:
                        remain_num = pre_num - curr_num
                        start_num = self.db.count_by(Experiment,
                                                     user_id=ReservedUser.DefaultUserID,
                                                     template=template,
                                                     status=EStatus.STARTING)
                        if start_num > 0:
                            self.log.debug("there is an azure env starting, will check later ... ")
                            return
                        else:
                            self.log.debug(
                                "no starting template: %s , remain num is %d ... " % (template.name, remain_num))
                            self.start_expr(template.hackathon.name, template.name, ReservedUser.DefaultUserID)
                            break
                            # curr_num += 1
                            # self.log.debug("all template %s start complete" % template.name)
                elif template.provider == VE_PROVIDER.DOCKER:
                    self.log.debug(
                        "template name is %s, hackathon name is %s" % (template.name, template.hackathon.name))
                    if curr_num < pre_num:
                        remain_num = pre_num - curr_num
                        self.log.debug("no idle template: %s, remain num is %d ... " % (template.name, remain_num))
                        self.start_expr(template.hackathon.name, template.name, ReservedUser.DefaultUserID)
                        # curr_num += 1
                        break
                        # self.log.debug("all template %s start complete" % template.name)
            except Exception as e:
                self.log.error(e)
                self.log.error("check default experiment failed")

    # --------------------------------------------- helper function ---------------------------------------------#

    def __start_new_expr(self, hackathon, template, user_id):
        # new expr
        expr = self.db.add_object_kwargs(Experiment,
                                         user_id=user_id,
                                         hackathon_id=hackathon.id,
                                         status=EStatus.INIT,
                                         template_id=template.id)
        self.db.commit()

        curr_num = self.db.count(Experiment,
                                 Experiment.user_id == ReservedUser.DefaultUserID,
                                 Experiment.template == template,
                                 (Experiment.status == EStatus.STARTING) |
                                 (Experiment.status == EStatus.RUNNING))
        if template.provider == VE_PROVIDER.DOCKER:
            try:
                template_dic = self.template_manager.load_template(template)
                virtual_environments_list = template_dic[BaseTemplate.VIRTUAL_ENVIRONMENTS]
                if curr_num != 0 and curr_num >= self.util.get_config("pre_allocate.docker"):
                    return
                expr.status = EStatus.STARTING
                self.db.commit()
                map(lambda virtual_environment_dic:
                    self.__remote_start_container(hackathon, expr, virtual_environment_dic),
                    virtual_environments_list)
                expr.status = EStatus.RUNNING
                self.db.commit()
            except Exception as e:
                self.log.error(e)
                self.log.error("Failed starting containers")
                self.__roll_back(expr.id)
                return internal_server_error('Failed starting containers')
        else:
            if curr_num != 0 and curr_num >= self.util.get_config("pre_allocate.azure"):
                return
            expr.status = EStatus.STARTING
            self.db.commit()
            try:
                af = AzureFormation(self.docker.__load_azure_key_id(expr.id))
                af.create(expr.id)
            except Exception as e:
                self.log.error(e)
                return internal_server_error('Failed starting azure vm')
        # after everything is ready, set the expr state to running
        # response to caller
        return self.__report_expr_status(expr)

    def __report_expr_status(self, expr):
        containers = self.__get_containers_by_exper(expr)
        for container in containers:
            # expr status(restarting or running) is not match container running status on docker host
            if not self.docker.hosted_docker.check_container_status_is_normal(container):
                try:
                    self.db.update_object(expr, status=EStatus.UNEXPECTED_ERROR)
                    self.db.update_object(container.virtual_environment, status=VEStatus.UNEXPECTEDERRORS)
                    break
                except Exception as ex:
                    self.log.error(ex)
        ret = {
            "expr_id": expr.id,
            "status": expr.status,
            "hackathon": expr.hackathon.name,
            "create_time": str(expr.create_time),
            "last_heart_beat_time": str(expr.last_heart_beat_time),
        }

        if expr.status != EStatus.RUNNING:
            return ret
        # return remote clients include guacamole and cloudEclipse
        remote_servers = []
        for ve in expr.virtual_environments.all():
            if ve.remote_provider == VERemoteProvider.Guacamole:
                try:
                    guacamole_config = json.loads(ve.remote_paras)
                    guacamole_host = self.util.safe_get_config("guacamole.host", "localhost:8080")
                    # target url format:
                    # http://localhost:8080/guacamole/#/client/c/{name}?name={name}&oh={token}
                    name = guacamole_config["name"]
                    url = guacamole_host + '/guacamole/#/client/c/%s?name=%s' % (name, name)
                    remote_servers.append({
                        "name": guacamole_config["displayname"],
                        "url": url
                    })
                    # cloud eclipse
                    cloud_eclipse_url = self.__get_cloud_eclipse_url(expr)
                    if cloud_eclipse_url is not None:
                        remote_servers.append({
                            "name": CLOUD_ECLIPSE.CLOUD_ECLIPSE,
                            "url": cloud_eclipse_url
                        })

                except Exception as e:
                    self.log.error(e)

        if expr.status == EStatus.RUNNING:
            ret["remote_servers"] = remote_servers
        # return public accessible web url
        public_urls = []
        if expr.template.provider == VE_PROVIDER.DOCKER:
            for ve in expr.virtual_environments.all():
                for p in ve.port_bindings.all():
                    if p.binding_type == PortBindingType.CLOUD_SERVICE and p.url is not None:
                        hs = self.db.find_first_object_by(DockerHostServer, id=p.binding_resource_id)
                        url = p.url.format(hs.public_dns, p.port_from)
                        public_urls.append({
                            "name": p.name,
                            "url": url
                        })
        else:
            for ve in expr.virtual_environments.all():
                for vm in ve.azure_virtual_machines_v.all():
                    ep = vm.azure_endpoints.filter_by(private_port=80).first()
                    url = 'http://%s:%s' % (vm.public_ip, ep.public_port)
                    public_urls.append({
                        "name": ep.name,
                        "url": url
                    })
        ret["public_urls"] = public_urls
        return ret

    def __check_template_status(self, hackathon_name, template_name):
        hackathon = self.db.find_first_object_by(Hackathon, name=hackathon_name)
        if hackathon is None:
            return None
        template = self.db.find_first_object_by(Template, hackathon_id=hackathon.id, name=template_name)
        if template is None or self.template_manager.load_template(template) is None:
            return None
        return [hackathon, template]

    def __remote_start_container(self, hackathon, expr, virtual_environment_dic):
        docker_template_unit = DockerTemplateUnit(virtual_environment_dic)
        old_name = docker_template_unit.get_name()
        suffix = "".join(random.sample(string.ascii_letters + string.digits, 8))
        new_name = '%d-%s-%s' % (expr.id, old_name, suffix)
        docker_template_unit.set_name(new_name)
        self.log.debug("starting to start container: %s" % new_name)
        # db entity
        ve = VirtualEnvironment(provider=VE_PROVIDER.DOCKER,
                                name=new_name,
                                image=docker_template_unit.get_image_with_tag(),
                                status=VEStatus.INIT,
                                remote_provider=VERemoteProvider.Guacamole,
                                experiment=expr)
        self.db.add_object(ve)

        # start container remotely , use hosted docker or alauda docker
        docker = self.docker.get_docker(hackathon)
        container_ret = docker.start(docker_template_unit,
                                     hackathon=hackathon,
                                     virtual_environment=ve,
                                     experiment=expr)
        if container_ret is None:
            self.log.error("container %s fail to run" % new_name)
            raise Exception("container_ret is none")
        self.log.debug("starting container %s is ended ... " % new_name)
        return ve

    def __check_expr_status(self, user_id, hackathon, template):
        """
        check experiment status, if there are pre-allocate experiments, the experiment will be assigned directly
        :param user_id:
        :param hackathon:
        :param template:
        :return:
        """
        exp_status = [EStatus.RUNNING, EStatus.STARTING]
        check_temp = Experiment.template_id == template.id if self.hackathon_manager.validate_admin_privilege(user_id,
                                                                                                              hackathon.id) else Experiment.id > -1
        expr = self.db.find_first_object(Experiment,
                                         Experiment.status.in_(exp_status),
                                         Experiment.user_id == user_id,
                                         Experiment.hackathon_id == hackathon.id,
                                         check_temp)
        if expr is not None:
            return expr

        expr = self.db.find_first_object_by(Experiment,
                                            status=EStatus.RUNNING,
                                            hackathon_id=hackathon.id,
                                            user_id=ReservedUser.DefaultUserID,
                                            template=template)
        if expr is not None:
            self.db.update_object(expr, user_id=user_id)
            self.db.commit()
            self.log.debug("experiment had been assigned, check experiment and start new job ... ")

            # add a job to start new pre-allocate experiment
            self.schedule_pre_allocate_expr_job()
            return expr

    def __roll_back(self, expr_id):
        """
        roll back when exception occurred
        :param expr_id: experiment id
        """
        self.log.debug("Starting rollback ...")
        expr = self.db.find_first_object_by(Experiment, id=expr_id)
        try:
            expr.status = EStatus.ROLL_BACKING
            self.db.commit()
            if expr is not None:
                # delete containers and change expr status
                for c in expr.virtual_environments:
                    if c.provider == VE_PROVIDER.DOCKER:
                        self.docker.delete(c.name, container=c.container, expr_id=expr_id)
                        c.status = VEStatus.DELETED
                        self.db.commit()
            # delete ports
            expr.status = EStatus.ROLL_BACKED

            self.db.commit()
            self.log.info("Rollback succeeded")
        except Exception as e:
            expr.status = EStatus.FAILED
            self.db.commit()
            self.log.info("Rollback failed")
            self.log.error(e)

            # --------------------------------------------- helper function ---------------------------------------------#

    def __get_expr_with_user_info(self, experiment):
        info = experiment.dic()
        info['user_info'] = self.user_manager.user_display_info(experiment.user)
        return info

    def __get_filter_condition(self, hackathon_id, **kwargs):
        condition = Experiment.hackathon_id == hackathon_id
        # check status: -1 means query all status
        if kwargs['status'] != -1:
            condition = and_(condition, Experiment.status == kwargs['status'])
        # check user name
        if len(kwargs['user_name']) > 0:
            users = self.db.find_all_objects(User, User.nickname.like('%' + kwargs['user_name'] + '%'))
            uids = map(lambda x: x.id, users)
            condition = and_(condition, Experiment.user_id.in_(uids))
        return condition

    def __get_cloud_eclipse_url(self, experiment):
        reg = self.register_manager.get_registration_by_user_and_hackathon(experiment.user_id, experiment.hackathon_id)
        if reg is None:
            return None
        if reg.git_project is None:
            return None
        # http://www.idehub.cn/api/ide/18?git=http://git.idehub.cn/root/test-c.git&user=root&from=hostname(frontend)
        api = self.util.safe_get_config("%s.api" % CLOUD_ECLIPSE.CLOUD_ECLIPSE, "http://www.idehub.cn/api/ide")
        openId = experiment.user.openid
        url = "%s/%d?git=%s&user=%s&from=" % (api, experiment.id, reg.git_project, openId)
        self.log.debug("cloud eclipse url : %s" % url)
        return url

    def __get_containers_by_exper(self, expr):
        """get experiment's all containers which are based on hosted_docker

        :type  expr: Experiment
        :param expr: which to get containers from

        :return DockerContainer object List by query

        """
        ves = self.db.find_all_objects_by(VirtualEnvironment, experiment_id=expr.id, provider=VE_PROVIDER.DOCKER)
        return map(lambda x: x.container, ves)
