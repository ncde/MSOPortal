import json
import time
import logging
from urllib.parse import urlparse
from datetime import datetime

from django.conf import settings
from threading import Thread

from django.db import models, connection

from django.forms.models import model_to_dict

from cloudify_rest_client import CloudifyClient
from cloudify_rest_client.executions import Execution
from cloudify_rest_client.exceptions import CloudifyClientError
from cloudify_rest_client.exceptions \
    import DeploymentEnvironmentCreationPendingError
from cloudify_rest_client.exceptions \
    import DeploymentEnvironmentCreationInProgressError

WAIT_FOR_EXECUTION_SLEEP_INTERVAL = 3

# Get an instance of a logger
LOGGER = logging.getLogger(__name__)


def backend(function):
    def decorator(*args, **kwargs):
        t = Thread(target=function, args=args, kwargs=kwargs)
        t.daemon = True
        t.start()
    return decorator


def _to_dict(model_instance):
    if model_instance is None:
        return None
    else:
        return model_to_dict(model_instance)


class Orchestrator(models.Model):
    """ Custom permissions for Experiments Tool app """

    class Meta:

        managed = False  # No database table creation or deletion operations \
        # will be performed for this model.

        permissions = (
            ('register_app',
             'Can register a new app in the orchestrator'),
            ('remove_app',
             'Can remove an app in the orchestrator'),
            ('create_instance',
             'Can create a new app instance in the orchestrator'),
            ('run_instance',
             'Can run workflows of an instance in the orchestrator'),
            ('destroy_instance',
             'Can delete an app instance in the orchestrator'),
        )


class DataCatalogueKey(models.Model):
    code = models.CharField(max_length=50)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )

    @classmethod
    def get(cls, owner, return_dict=False):
        """ If returning a dict, password is removed """
        key = None
        try:
            key = cls.objects.get(owner=owner)
        except cls.DoesNotExist:
            pass

        if not return_dict:
            return key
        else:
            if key is not None:
                key = _to_dict(key)
            return {'key': key}

    @classmethod
    def create(cls,
               code,
               owner,
               return_dict=False):
        error = None
        key = None
        try:
            key = cls.objects.create(code=code, owner=owner)
        except Exception as err:
            LOGGER.exception(err)
            error = str(err)

        if not return_dict:
            return (key, error)
        else:
            return {'key': _to_dict(key), 'error': error}

    def update(self,
               code):
        self.code = code
        self.save()

    @classmethod
    def remove(cls, owner, return_dict=False):
        error = None
        key = cls.get(owner)

        if key is not None:
            key.delete()
        else:
            error = "Can't delete key because it doesn't exists"

        if not return_dict:
            return (key, error)
        else:
            return {'key': _to_dict(key), 'error': error}

    def __str__(self):
        return "Key from {0}".format(self.owner.username)


class TunnelConnection(models.Model):
    name = models.CharField(max_length=50)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    host = models.CharField(max_length=50)
    user = models.CharField(max_length=50)
    private_key = models.CharField(max_length=1800, blank=True, default='')
    private_key_password = models.CharField(
        max_length=50, blank=True, default='')
    password = models.CharField(max_length=50, blank=True, default='')

    @classmethod
    def get(cls, pk, owner, return_dict=False):
        """ If returning a dict, password is removed """
        error = None
        tunnel = None
        try:
            tunnel = cls.objects.get(pk=pk)
        except cls.DoesNotExist:
            pass

        if tunnel is not None and owner != tunnel.owner:
            tunnel = None
            error = 'Tunnel does not belong to user'

        if not return_dict:
            return (tunnel, error)
        else:
            if tunnel is not None:
                tunnel = _to_dict(tunnel)
                tunnel.pop('private_key')
                tunnel.pop('private_key_password')
                tunnel.pop('password')
            return {'tunnel': tunnel, 'error': error}

    @classmethod
    def list(cls, owner, return_dict=False):
        """ If returning a dict, passwords are removed """
        error = None
        tunnel_list = []
        try:
            tunnel_list = cls.objects.filter(owner=owner)
        except cls.DoesNotExist:
            pass
        except Exception as err:
            LOGGER.exception(err)
            error = str(err)

        if not return_dict:
            return (tunnel_list, error)
        else:
            passwordless_list = []
            for tunnel in tunnel_list:
                tunnel_dict = _to_dict(tunnel)
                tunnel_dict.pop('private_key')
                tunnel_dict.pop('private_key_password')
                tunnel_dict.pop('password')
                passwordless_list.append(tunnel_dict)
            return {'tunnel_list':  passwordless_list,
                    'error': error}

    @classmethod
    def create(cls,
               name,
               owner,
               host,
               user,
               private_key,
               private_key_password,
               password,
               return_dict=False):
        error = None
        tunnel = None
        try:
            tunnel = cls.objects.create(name=name,
                                        owner=owner,
                                        host=host,
                                        user=user,
                                        private_key=private_key,
                                        private_key_password=private_key_password,
                                        password=password)
        except Exception as err:
            LOGGER.exception(err)
            error = str(err)

        if not return_dict:
            return (tunnel, error)
        else:
            return {'tunnel': _to_dict(tunnel), 'error': error}

    @classmethod
    def remove(cls, pk, owner, return_dict=False):
        tunnel, error = cls.get(pk, owner)

        if error is None:
            if tunnel is not None:
                tunnel.delete()
            else:
                error = "Can't delete tunnel because it doesn't exists"

        if not return_dict:
            return (tunnel, error)
        else:
            if tunnel is not None:
                tunnel = _to_dict(tunnel)
                tunnel.pop('private_key')
                tunnel.pop('private_key_password')
                tunnel.pop('password')
            return {'tunnel': tunnel, 'error': error}

    def __str__(self):
        return "{0}: Tunnel at {1} from {2}({3})".format(
            self.name,
            self.host,
            self.owner.username,
            self.user)

    def to_dict(self):
        return {
            'host': self.host,
            'user': self.user,
            'private_key': self.private_key,
            'private_key_password': self.private_key_password,
            'password': self.password,
        }


class HPCInfrastructure(models.Model):
    name = models.CharField(max_length=50)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    host = models.CharField(max_length=50)
    user = models.CharField(max_length=50)
    private_key = models.CharField(max_length=1800, blank=True, default='')
    private_key_password = models.CharField(
        max_length=50, blank=True, default='')
    password = models.CharField(max_length=50, blank=True, default='')
    time_zone = models.CharField(max_length=20)
    tunnel = models.ForeignKey(
        TunnelConnection,
        on_delete=models.CASCADE,
        null=True
    )

    SLURM = 'SLURM'
    MANAGER_CHOICES = (
        (SLURM, 'Slurm'),
    )
    manager = models.CharField(
        max_length=5,
        choices=MANAGER_CHOICES,
        default=SLURM,
    )

    @classmethod
    def get(cls, pk, owner, return_dict=False):
        """ If returning a dict, password is removed """
        error = None
        hpc = None
        try:
            hpc = cls.objects.get(pk=pk)
        except cls.DoesNotExist:
            pass

        if hpc is not None and owner != hpc.owner:
            hpc = None
            error = 'HPC does not belong to user'

        if not return_dict:
            return (hpc, error)
        else:
            if hpc is not None:
                hpc_dict = _to_dict(hpc)
                hpc_dict.pop('private_key')
                hpc_dict.pop('private_key_password')
                hpc_dict.pop('password')
                if hpc.tunnel is not None:
                    hpc_dict['tunnel'] = _to_dict(hpc.tunnel)
                    hpc_dict['tunnel'].pop('private_key')
                    hpc_dict['tunnel'].pop('private_key_password')
                    hpc_dict['tunnel'].pop('password')
            return {'hpc': hpc_dict, 'error': error}

    @classmethod
    def list(cls, owner, return_dict=False):
        """ If returning a dict, passwords are removed """
        error = None
        hpc_list = []
        try:
            hpc_list = cls.objects.filter(owner=owner)
        except cls.DoesNotExist:
            pass
        except Exception as err:
            LOGGER.exception(err)
            error = str(err)

        if not return_dict:
            return (hpc_list, error)
        else:
            passwordless_list = []
            for hpc in hpc_list:
                hpc_dict = _to_dict(hpc)
                hpc_dict.pop('private_key')
                hpc_dict.pop('private_key_password')
                hpc_dict.pop('password')
                if hpc.tunnel is not None:
                    hpc_dict['tunnel'] = _to_dict(hpc.tunnel)
                    hpc_dict['tunnel'].pop('private_key')
                    hpc_dict['tunnel'].pop('private_key_password')
                    hpc_dict['tunnel'].pop('password')
                passwordless_list.append(hpc_dict)
            return {'hpc_list':  passwordless_list,
                    'error': error}

    @classmethod
    def create(cls,
               name,
               owner,
               host,
               user,
               private_key,
               private_key_password,
               password,
               tz,
               manager,
               tunnel_pk,
               return_dict=False):
        error = None
        hpc = None
        tunnel = None

        if tunnel_pk is not None:
            tunnel, error = TunnelConnection.get(tunnel_pk, owner)
            if error is None:
                if tunnel is None:
                    error = "Can't create HPC because tunnel doesn't exists"

        if error is None:
            try:
                hpc = cls.objects.create(name=name,
                                         owner=owner,
                                         host=host,
                                         user=user,
                                         private_key=private_key,
                                         private_key_password=private_key_password,
                                         password=password,
                                         time_zone=tz,
                                         manager=manager,
                                         tunnel=tunnel)
            except Exception as err:
                LOGGER.exception(err)
                error = str(err)

        if not return_dict:
            return (hpc, error)
        else:
            return {'hpc': _to_dict(hpc), 'error': error}

    @classmethod
    def remove(cls, pk, owner, return_dict=False):
        hpc, error = cls.get(pk, owner)

        if error is None:
            if hpc is not None:
                hpc.delete()
            else:
                error = "Can't delete HPC because it doesn't exists"

        if not return_dict:
            return (hpc, error)
        else:
            if hpc is not None:
                hpc_dict = _to_dict(hpc)
                hpc_dict.pop('private_key')
                hpc_dict.pop('private_key_password')
                hpc_dict.pop('password')
                if hpc.tunnel is not None:
                    hpc_dict['tunnel'] = _to_dict(hpc.tunnel)
                    hpc_dict['tunnel'].pop('private_key')
                    hpc_dict['tunnel'].pop('private_key_password')
                    hpc_dict['tunnel'].pop('password')
            return {'hpc': hpc, 'error': error}

    def __str__(self):
        return "{0}: HPC at {1} from {2}({3})".format(
            self.name,
            self.host,
            self.owner.username,
            self.user)

    def to_dict(self):
        inputs_data = {
            'credentials': {
                'host': self.host,
                'user': self.user,
                'private_key': self.private_key,
                'private_key_password': self.private_key_password,
                'password': self.password,
            },
            'country_tz': self.time_zone,
            'workload_manager': self.manager
        }

        if self.tunnel is not None:
            inputs_data['credentials']["tunnel"] = self.tunnel.to_dict()

        return inputs_data


def _get_client():
    client = CloudifyClient(host=settings.ORCHESTRATOR_HOST,
                            username=settings.ORCHESTRATOR_USER,
                            password=settings.ORCHESTRATOR_PASS,
                            tenant=settings.ORCHESTRATOR_TENANT)
    return client


class Application(models.Model):
    name = models.CharField(max_length=50)

    description = models.CharField(max_length=256, null=True)
    marketplace_id = models.CharField(max_length=10, db_index=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )

    @classmethod
    def _get(cls, pk):
        app = None
        try:
            app = cls.objects.get(pk=pk)
        except cls.DoesNotExist:
            pass

        return app

    @classmethod
    def get(cls, pk, owner=None, return_dict=False):
        error = None
        app = cls._get(pk)

        if owner is not None and app is not None and owner != app.owner:
            app = None
            error = 'Application does not belong to user'

        if not return_dict:
            return (app, error)
        else:
            if app is not None:
                app = _to_dict(app)
            return {'app': app, 'error': error}

    @classmethod
    def list(cls, marketplace_ids, return_dict=False):
        error = None
        app_list = []
        try:
            app_list = cls.objects.filter(marketplace_id__in=marketplace_ids)
        except cls.DoesNotExist:
            pass

        if not return_dict:
            return (app_list, error)
        else:
            return {'app_list': [_to_dict(app) for app in app_list],
                    'error': error}

    @classmethod
    def create(cls,
               path,
               blueprint_id,
               marketplace_id,
               owner,
               return_dict=False):
        error = None
        app = None

        blueprint, error = cls._upload_blueprint(path, blueprint_id)

        if not error:
            try:
                app = cls.objects.create(
                    name=blueprint['id'],
                    description=blueprint['description'],
                    marketplace_id=marketplace_id,
                    owner=owner)
            except Exception as err:
                LOGGER.exception(err)
                error = str(err)
                cls._remove_blueprint(blueprint['id'])

        if not return_dict:
            return (app, error)
        else:
            return {'app': _to_dict(app), 'error': error}

    @classmethod
    def get_inputs(cls, pk, return_dict=False):
        """ Returns a list of dict with inputs, and a string error """
        inputs = None
        app = cls._get(pk)

        if app is not None:
            inputs, error = cls._get_inputs(app.name)
        else:
            error = "Can't get app inputs because it doesn't exists"

        if not return_dict:
            return (inputs, error)
        else:
            return {'inputs': inputs, 'error': error}

    @classmethod
    def remove(cls, pk, owner, return_dict=False):
        app, error = cls.get(pk, owner)

        if error is None:
            if app is not None:
                _, error = cls._remove_blueprint(app.name)
                if error is None:
                    app.delete()
            else:
                error = "Can't delete aplication because it doesn't exists"

        if not return_dict:
            return (app, error)
        else:
            return {'app': _to_dict(app), 'error': error}

    def __str__(self):
        return "Application {0} from {1}".format(
            self.name,
            self.owner.username)

    @staticmethod
    def _upload_blueprint(path, blueprint_id):
        error = None
        blueprint = None
        is_archive = bool(urlparse(path).scheme) or path.endswith(".tar.gz")

        client = _get_client()
        try:
            if is_archive:
                blueprint = client.blueprints.publish_archive(
                    path, blueprint_id)
            else:
                blueprint = client.blueprints.upload(path, blueprint_id)
        except CloudifyClientError as err:
            LOGGER.exception(err)
            error = str(err)

        return (blueprint, error)

    @staticmethod
    def _get_blueprints():
        error = None
        blueprints = None
        client = _get_client()
        try:
            blueprints = client.blueprints.list().items
        except CloudifyClientError as err:
            LOGGER.exception(err)
            error = str(err)

        return (blueprints, error)

    @staticmethod
    def _get_inputs(app_id):
        error = None
        data = None
        client = _get_client()
        try:
            blueprint_dict = client.blueprints.get(app_id)
            inputs = blueprint_dict['plan']['inputs']
            data = [{'name': name,
                     'type': input.get('type', '-'),
                     'default': input.get('default', '-'),
                     'description': input.get('description', '-')}
                    for name, input in inputs.items()]
        except CloudifyClientError as err:
            LOGGER.exception(err)
            error = str(err)

        return (data, error)

    @staticmethod
    def _remove_blueprint(app_id):
        error = None
        blueprint = None
        client = _get_client()
        try:
            blueprint = client.blueprints.delete(app_id)
        except CloudifyClientError as err:
            LOGGER.exception(err)
            error = str(err)

        return (blueprint, error)


class AppInstance(models.Model):
    name = models.CharField(max_length=50)
    app = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
    )

    description = models.CharField(max_length=256, null=True)
    inputs = models.CharField(max_length=256, null=True)
    outputs = models.CharField(max_length=256, null=True)

    PREPARED = 'prepared'
    TERMINATED = 'terminated'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    PENDING = 'pending'
    STARTED = 'started'
    CANCELLING = 'cancelling'
    FORCE_CANCELLING = 'force_cancelling'
    STATUS_CHOICES = (
        (TERMINATED, 'terminated'),
        (FAILED, 'failed'),
        (CANCELLED, 'cancelled'),
        (PENDING, 'pending'),
        (STARTED, 'started'),
        (CANCELLING, 'cancelling'),
        (FORCE_CANCELLING, 'force_cancelling'),
        (PREPARED, 'prepared')
    )
    status = models.CharField(
        max_length=5,
        choices=STATUS_CHOICES,
        default=PREPARED,
        blank=True
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )

    @classmethod
    def get(cls, pk, owner, return_dict=False):
        error = None
        instance = None
        try:
            instance = cls.objects.get(pk=pk)
        except cls.DoesNotExist:
            pass

        if instance is not None and owner != instance.owner:
            instance = None
            error = 'Application instance does not belong to user'

        if not return_dict:
            return (instance, error)
        else:
            if instance is not None:
                instance = _to_dict(instance)
            return {'instance': instance, 'error': error}

    @classmethod
    def list(cls, owner, return_dict=False):
        error = None
        instance_list = []
        try:
            instance_list = cls.objects.filter(owner=owner)
        except cls.DoesNotExist:
            pass

        if not return_dict:
            return (instance_list, error)
        else:
            return {
                'instance_list': [_to_dict(inst) for inst in instance_list],
                'error': error}

    @classmethod
    def create(cls, app_pk, deployment_id, inputs, owner, return_dict=False):
        error = None
        instance = None

        app, error = Application.get(app_pk)
        if error is None:
            if app is None:
                error = "Can't create instance because app doesn't exists"
            else:
                deployment, error = cls._create_deployment(app.name,
                                                           deployment_id,
                                                           inputs)

        if error is None:
            try:
                instance = cls.objects.create(
                    name=deployment['id'],
                    app=app,
                    description=deployment['description'],
                    inputs=json.dumps(
                        deployment['inputs'],
                        ensure_ascii=False,
                        separators=(',', ':')),
                    outputs=json.dumps(
                        deployment['outputs'],
                        ensure_ascii=False,
                        separators=(',', ':')),
                    owner=owner)
            except Exception as err:
                LOGGER.exception(err)
                error = str(err)
                cls._destroy_deployment(deployment['id'])

        if not return_dict:
            return (instance, error)
        else:
            return {'instance': _to_dict(instance), 'error': error}

    @backend
    def clean_up_execution(self, owner):
        self._update_status(AppInstance.PREPARED)

        # logs
        logs_list, error = ApplicationInstanceLog.list(
            self,
            owner,
            offset=0
        )
        if error is None:
            for log in logs_list:
                log.delete()
        else:
            LOGGER.warning("Couldn't clean up logs: "+error)

        # executions
        executions_list, error = WorkflowExecution.list(
            self,
            owner)
        if error is None:
            for execution in executions_list:
                execution.delete()
        else:
            LOGGER.warning("Couldn't clean up executions: "+error)

    @backend
    def run_workflows(self, owner):
        # TODO: create cfy deployment again
        self._update_status(Execution.STARTED)

        status = self.run_workflow(WorkflowExecution.INSTALL, owner)
        if WorkflowExecution.is_execution_finished(status) and \
                not WorkflowExecution.is_execution_wrong(status):
            status = self.run_workflow(WorkflowExecution.RUN, owner)
            if WorkflowExecution.is_execution_finished(status) and \
                    not WorkflowExecution.is_execution_wrong(status):
                status = self.run_workflow(WorkflowExecution.UNINSTALL, owner)

        if not WorkflowExecution.is_execution_finished(status):
            # TODO: cancel execution
            pass

        self._update_status(status)

        self.__class__._destroy_deployment(self.name, force=True)

        # Django creates a new connection that needs to be manually closed
        connection.close()

        return status

    def run_workflow(self, workflow, owner):
        error = None
        execution = None

        execution, error = WorkflowExecution.create(
            self,
            workflow,
            owner,
            force=False)

        if error is not None:
            status = Execution.CANCELLED
            self._update_status(Execution.CANCELLED)
            message = "Couldn't execute the workflow '" + \
                workflow+"': "+error
            LOGGER.error(message)
            ApplicationInstanceLog.create(
                self,
                datetime.now(),
                message)
            return status
        if execution is None:
            status = Execution.CANCELLED
            self._update_status(Execution.CANCELLED)
            message = "Couldn't create the execution for workflow '" + \
                workflow+"'"
            LOGGER.error(message)
            ApplicationInstanceLog.create(
                self,
                datetime.now(),
                message)
            return status

        ApplicationInstanceLog.create(
            self,
            datetime.now(),
            "-------"+workflow.upper()+"-------")

        retries = 5
        offset = 0
        finished = False
        status = Execution.PENDING
        while not finished and (retries > 0 or error is None):
            events, error = execution.get_execution_events(offset)
            if error is None:
                retries = 0
                offset = events['last']
                status = events['status']
                finished = WorkflowExecution.is_execution_finished(status)
                self.append_logs(events['logs'])
            else:
                retries -= 1
            time.sleep(10)

        return status

    def _update_status(self, status):
        if status != self.status:
            self.status = status
            self.save()

    def is_finished(self):
        return self.status in Execution.END_STATES \
            or self.status == AppInstance.PREPARED

    def is_wrong(self):
        return self.is_finished() and self.status != Execution.TERMINATED \
            and self.status != AppInstance.PREPARED

    def append_logs(self, events_list):
        for event in events_list:
            if not "reported_timestamp" in event:
                # this event cannot be logged
                LOGGER.warning(
                    "The event has not a timestamp, will not be registered")
                continue
            timestamp = event["reported_timestamp"]
            message = ""
            if "message" in event:
                message = event["message"]

            event_type = event["type"]
            if event_type == "cloudify_event":
                cloudify_event_type = event["event_type"]
                if cloudify_event_type == "workflow_node_event":
                    message += " " + event["node_instance_id"] + \
                        " (" + event["node_name"] + ")"
                elif cloudify_event_type == "sending_task" \
                        or cloudify_event_type == "task_started" \
                        or cloudify_event_type == "task_succeeded" \
                        or cloudify_event_type == "task_failed":
                    # message += " [" + event["operation"] + "]"
                    if "node_instance_id" in event and event["node_instance_id"]:
                        message += " " + event["node_instance_id"]
                    if "node_name" and event["node_name"]:
                        message += " (" + event["node_name"] + ")"
                elif cloudify_event_type == "workflow_started" \
                        or cloudify_event_type == "workflow_succeeded" \
                        or cloudify_event_type == "workflow_failed" \
                        or cloudify_event_type == "workflow_cancelled":
                    pass
                else:
                    event.pop("reported_timestamp")
                    message = json.dumps(event)
                if event["error_causes"]:
                    for cause in event["error_causes"]:
                        message += "\n" + \
                            cause['type'] + ": " + cause["message"]
                        message += "\n\t" + cause["traceback"]
            else:
                event.pop("reported_timestamp")
                message = json.dumps(event)

            ApplicationInstanceLog.create(self, timestamp, message)

    @classmethod
    def get_instance_events(cls, pk, offset, owner):
        logs_list = []
        status = None
        instance, error = cls.get(pk, owner)

        if error is None:
            if instance is not None:
                status = instance.status
                logs_list, error = ApplicationInstanceLog.list(
                    instance,
                    owner,
                    offset=offset
                )
        else:
            error = "Can't get instance events because it doesn't exist"

        return (
            {
                'logs': [_to_dict(log) for log in logs_list],
                'last': offset + len(logs_list),
                'status': instance.status,
                'finished': instance.is_finished()
            },
            error)

    @classmethod
    def remove(cls, pk, owner, return_dict=False, force=False):
        instance, error = cls.get(pk, owner)

        if error is None:
            if instance is not None:
                _, error = cls._destroy_deployment(
                    instance.name,
                    force=force)
                if error is None or instance.is_finished():
                    instance.delete()
            else:
                error = "Can't delete instance because it doesn't exists"

        if not return_dict:
            return (instance, error)
        else:
            return {'instance': _to_dict(instance), 'error': error}

    def __str__(self):
        return "Instance {0}:{1} from {2}".format(
            self.app.name,
            self.name,
            self.owner.username)

    @classmethod
    def _create_deployment(cls, app_id, instance_id, inputs, retries=3):
        error = None
        deployment = None

        client = _get_client()
        try:
            deployment = client.deployments.create(
                app_id,
                instance_id,
                inputs=inputs,
                skip_plugins_validation=True
            )
        except (DeploymentEnvironmentCreationPendingError,
                DeploymentEnvironmentCreationInProgressError) as err:
            if (retries > 0):
                time.sleep(WAIT_FOR_EXECUTION_SLEEP_INTERVAL)
                deployment, error = cls._create_deployment(app_id,
                                                           instance_id,
                                                           inputs,
                                                           retries - 1)
            LOGGER.exception(err)
            error = str(err)
        except CloudifyClientError as err:
            LOGGER.exception(err)
            error = str(err)

        return (deployment, error)

    @staticmethod
    def _destroy_deployment(instance_id, force=False):
        error = None
        deployment = None
        client = _get_client()
        try:
            deployment = client.deployments.delete(
                instance_id, ignore_live_nodes=force)
        except CloudifyClientError as err:
            LOGGER.exception(err)
            error = str(err)

        return (deployment, error)


class ApplicationInstanceLog(models.Model):
    instance = models.ForeignKey(
        AppInstance,
        on_delete=models.CASCADE,
    )

    generated = models.DateTimeField()
    message = models.TextField()

    @classmethod
    def list(cls, instance, owner, offset=0, return_dict=False):
        error = None
        logs_list = []
        try:
            logs_list = \
                cls.objects.filter(instance=instance)[offset:]
        except cls.DoesNotExist:
            pass

        if not return_dict:
            return (logs_list, error)
        else:
            return {
                'logs_list': [_to_dict(log) for log in logs_list],
                'error': error}

    @classmethod
    def create(cls, instance, generated, message):
        error = None

        if instance is None:
            error = 'Application instance does not belong to user'
        else:
            try:
                instance = cls.objects.create(
                    instance=instance,
                    generated=generated,
                    message=message)
            except Exception as err:
                LOGGER.exception(err)
                error = str(err)

        return error


class WorkflowExecution(models.Model):
    INSTALL = 'install'
    RUN = 'run_jobs'
    UNINSTALL = 'uninstall'

    id_code = models.CharField(max_length=50)
    app_instance = models.ForeignKey(
        AppInstance,
        on_delete=models.CASCADE,
    )
    workflow = models.CharField(max_length=50)
    # can't use auto_now_add because it set editable=False
    # and therefore model_to_dict skips the field
    created_on = models.DateTimeField(editable=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )

    @classmethod
    def get(cls, pk, owner, return_dict=False):
        error = None
        execution = None
        try:
            execution = cls.objects.get(pk=pk)
        except cls.DoesNotExist:
            pass

        if execution is not None and owner != execution.owner:
            execution = None
            error = 'Instance execution does not belong to user'

        if not return_dict:
            return (execution, error)
        else:
            if execution is not None:
                execution = _to_dict(execution)
            return {'execution': execution, 'error': error}

    @classmethod
    def list(cls, instance, owner, return_dict=False):
        error = None
        execution_list = []
        try:
            execution_list = cls.objects.filter(
                owner=owner,
                app_instance=instance)
        except cls.DoesNotExist:
            pass

        if not return_dict:
            return (execution_list, error)
        else:
            return {
                'execution_list':
                    [_to_dict(execution) for execution in execution_list],
                'error': error}

    @classmethod
    def create(cls, instance, workflow, owner,
               force=False, params=None, return_dict=False):
        error = None
        execution = None

        if instance is None:
            error = "Can't create execution because instance doesn't exist"
        elif instance.owner != owner:
            error = \
                "Can't create execution because instance doesn't " +\
                "belong to user"
        else:
            execution, error = cls._execute_workflow(
                instance.name,
                workflow,
                force,
                params)

        if error is None:
            try:
                execution = cls.objects.create(
                    id_code=execution['id'],
                    app_instance=instance,
                    workflow=workflow,
                    created_on=datetime.now(),
                    owner=owner)
            except Exception as err:
                LOGGER.exception(err)
                error = str(err)
                # TODO: cancel execution

        if not return_dict:
            return (execution, error)
        else:
            return {'execution': _to_dict(execution), 'error': error}

    def __str__(self):
        return "Workflow execution {0}:{1} [{2}] from {3}".format(
            self.workflow,
            self.app_instance.name,
            self.id_code,
            self.owner.username)

    @staticmethod
    def _execute_workflow(deployment_id, workflow, force, params=None):
        error = None
        execution = None

        client = _get_client()
        try:
            execution = client.executions.start(deployment_id,
                                                workflow,
                                                parameters=params,
                                                force=force)
        except CloudifyClientError as err:
            LOGGER.exception(err)
            error = str(err)

        return (execution, error)

    def get_execution_events(self, offset, return_dict=False):
        events = self._get_execution_events(offset)

        if not return_dict:
            return (events, None)
        else:
            return {'events': events, 'error': None}

    def _get_execution_events(self, offset):
        client = _get_client()

        # TODO: manage errors
        cfy_execution = client.executions.get(self.id_code)
        events = client.events.list(execution_id=self.id_code,
                                    _offset=offset,
                                    _size=100)
        last_message = events.metadata.pagination.total

        return {
            'logs': events.items,
            'last': last_message,
            'status': cfy_execution.status
        }

    @classmethod
    def is_execution_finished(cls, status):
        return status in Execution.END_STATES \
            or status == AppInstance.PREPARED

    @classmethod
    def is_execution_wrong(cls, status):
        return cls.is_execution_finished(status) and status != Execution.TERMINATED \
            and status != AppInstance.PREPARED
