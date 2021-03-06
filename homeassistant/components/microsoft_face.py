"""
Support for microsoft face recognition.

For more details about this component, please refer to the documentation at
https://home-assistant.io/components/microsoft_face/
"""
import asyncio
import json
import logging

import aiohttp
from aiohttp.hdrs import CONTENT_TYPE
import async_timeout
import voluptuous as vol

from homeassistant.const import CONF_API_KEY, CONF_TIMEOUT
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity
from homeassistant.loader import get_component
from homeassistant.util import slugify

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'microsoft_face'
DEPENDENCIES = ['camera']

FACE_API_URL = "api.cognitive.microsoft.com/face/v1.0/{0}"

DATA_MICROSOFT_FACE = 'microsoft_face'

CONF_AZURE_REGION = 'azure_region'

SERVICE_CREATE_GROUP = 'create_group'
SERVICE_DELETE_GROUP = 'delete_group'
SERVICE_TRAIN_GROUP = 'train_group'
SERVICE_CREATE_PERSON = 'create_person'
SERVICE_DELETE_PERSON = 'delete_person'
SERVICE_FACE_PERSON = 'face_person'

ATTR_GROUP = 'group'
ATTR_PERSON = 'person'
ATTR_CAMERA_ENTITY = 'camera_entity'
ATTR_NAME = 'name'

DEFAULT_TIMEOUT = 10

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_API_KEY): cv.string,
        vol.Optional(CONF_AZURE_REGION, default="westus"): cv.string,
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int,
    }),
}, extra=vol.ALLOW_EXTRA)

SCHEMA_GROUP_SERVICE = vol.Schema({
    vol.Required(ATTR_NAME): cv.string,
})

SCHEMA_PERSON_SERVICE = SCHEMA_GROUP_SERVICE.extend({
    vol.Required(ATTR_GROUP): cv.slugify,
})

SCHEMA_FACE_SERVICE = vol.Schema({
    vol.Required(ATTR_PERSON): cv.string,
    vol.Required(ATTR_GROUP): cv.slugify,
    vol.Required(ATTR_CAMERA_ENTITY): cv.entity_id,
})

SCHEMA_TRAIN_SERVICE = vol.Schema({
    vol.Required(ATTR_GROUP): cv.slugify,
})


def create_group(hass, name):
    """Create a new person group."""
    data = {ATTR_NAME: name}
    hass.services.call(DOMAIN, SERVICE_CREATE_GROUP, data)


def delete_group(hass, name):
    """Delete a person group."""
    data = {ATTR_NAME: name}
    hass.services.call(DOMAIN, SERVICE_DELETE_GROUP, data)


def train_group(hass, group):
    """Train a person group."""
    data = {ATTR_GROUP: group}
    hass.services.call(DOMAIN, SERVICE_TRAIN_GROUP, data)


def create_person(hass, group, name):
    """Create a person in a group."""
    data = {ATTR_GROUP: group, ATTR_NAME: name}
    hass.services.call(DOMAIN, SERVICE_CREATE_PERSON, data)


def delete_person(hass, group, name):
    """Delete a person in a group."""
    data = {ATTR_GROUP: group, ATTR_NAME: name}
    hass.services.call(DOMAIN, SERVICE_DELETE_PERSON, data)


def face_person(hass, group, person, camera_entity):
    """Add a new face picture to a person."""
    data = {ATTR_GROUP: group, ATTR_PERSON: person,
            ATTR_CAMERA_ENTITY: camera_entity}
    hass.services.call(DOMAIN, SERVICE_FACE_PERSON, data)


@asyncio.coroutine
def async_setup(hass, config):
    """Set up microsoft face."""
    entities = {}
    face = MicrosoftFace(
        hass,
        config[DOMAIN].get(CONF_AZURE_REGION),
        config[DOMAIN].get(CONF_API_KEY),
        config[DOMAIN].get(CONF_TIMEOUT),
        entities
    )

    try:
        # read exists group/person from cloud and create entities
        yield from face.update_store()
    except HomeAssistantError as err:
        _LOGGER.error("Can't load data from face api: %s", err)
        return False

    hass.data[DATA_MICROSOFT_FACE] = face

    @asyncio.coroutine
    def async_create_group(service):
        """Create a new person group."""
        name = service.data[ATTR_NAME]
        g_id = slugify(name)

        try:
            yield from face.call_api(
                'put', "persongroups/{0}".format(g_id), {'name': name})
            face.store[g_id] = {}

            entities[g_id] = MicrosoftFaceGroupEntity(hass, face, g_id, name)
            yield from entities[g_id].async_update_ha_state()
        except HomeAssistantError as err:
            _LOGGER.error("Can't create group '%s' with error: %s", g_id, err)

    hass.services.async_register(
        DOMAIN, SERVICE_CREATE_GROUP, async_create_group,
        schema=SCHEMA_GROUP_SERVICE)

    @asyncio.coroutine
    def async_delete_group(service):
        """Delete a person group."""
        g_id = slugify(service.data[ATTR_NAME])

        try:
            yield from face.call_api('delete', "persongroups/{0}".format(g_id))
            face.store.pop(g_id)

            entity = entities.pop(g_id)
            hass.states.async_remove(entity.entity_id)
        except HomeAssistantError as err:
            _LOGGER.error("Can't delete group '%s' with error: %s", g_id, err)

    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_GROUP, async_delete_group,
        schema=SCHEMA_GROUP_SERVICE)

    @asyncio.coroutine
    def async_train_group(service):
        """Train a person group."""
        g_id = service.data[ATTR_GROUP]

        try:
            yield from face.call_api(
                'post', "persongroups/{0}/train".format(g_id))
        except HomeAssistantError as err:
            _LOGGER.error("Can't train group '%s' with error: %s", g_id, err)

    hass.services.async_register(
        DOMAIN, SERVICE_TRAIN_GROUP, async_train_group,
        schema=SCHEMA_TRAIN_SERVICE)

    @asyncio.coroutine
    def async_create_person(service):
        """Create a person in a group."""
        name = service.data[ATTR_NAME]
        g_id = service.data[ATTR_GROUP]

        try:
            user_data = yield from face.call_api(
                'post', "persongroups/{0}/persons".format(g_id), {'name': name}
            )

            face.store[g_id][name] = user_data['personId']
            yield from entities[g_id].async_update_ha_state()
        except HomeAssistantError as err:
            _LOGGER.error("Can't create person '%s' with error: %s", name, err)

    hass.services.async_register(
        DOMAIN, SERVICE_CREATE_PERSON, async_create_person,
        schema=SCHEMA_PERSON_SERVICE)

    @asyncio.coroutine
    def async_delete_person(service):
        """Delete a person in a group."""
        name = service.data[ATTR_NAME]
        g_id = service.data[ATTR_GROUP]
        p_id = face.store[g_id].get(name)

        try:
            yield from face.call_api(
                'delete', "persongroups/{0}/persons/{1}".format(g_id, p_id))

            face.store[g_id].pop(name)
            yield from entities[g_id].async_update_ha_state()
        except HomeAssistantError as err:
            _LOGGER.error("Can't delete person '%s' with error: %s", p_id, err)

    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_PERSON, async_delete_person,
        schema=SCHEMA_PERSON_SERVICE)

    @asyncio.coroutine
    def async_face_person(service):
        """Add a new face picture to a person."""
        g_id = service.data[ATTR_GROUP]
        p_id = face.store[g_id].get(service.data[ATTR_PERSON])

        camera_entity = service.data[ATTR_CAMERA_ENTITY]
        camera = get_component('camera')

        try:
            image = yield from camera.async_get_image(hass, camera_entity)

            yield from face.call_api(
                'post',
                "persongroups/{0}/persons/{1}/persistedFaces".format(
                    g_id, p_id),
                image,
                binary=True
            )
        except HomeAssistantError as err:
            _LOGGER.error("Can't delete person '%s' with error: %s", p_id, err)

    hass.services.async_register(
        DOMAIN, SERVICE_FACE_PERSON, async_face_person,
        schema=SCHEMA_FACE_SERVICE)

    return True


class MicrosoftFaceGroupEntity(Entity):
    """Person-Group state/data Entity."""

    def __init__(self, hass, api, g_id, name):
        """Initialize person/group entity."""
        self.hass = hass
        self._api = api
        self._id = g_id
        self._name = name

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    @property
    def entity_id(self):
        """Return entity id."""
        return "{0}.{1}".format(DOMAIN, self._id)

    @property
    def state(self):
        """Return the state of the entity."""
        return len(self._api.store[self._id])

    @property
    def should_poll(self):
        """Return True if entity has to be polled for state."""
        return False

    @property
    def device_state_attributes(self):
        """Return device specific state attributes."""
        attr = {}
        for name, p_id in self._api.store[self._id].items():
            attr[name] = p_id

        return attr


class MicrosoftFace(object):
    """Microsoft Face api for HomeAssistant."""

    def __init__(self, hass, server_loc, api_key, timeout, entities):
        """Initialize Microsoft Face api."""
        self.hass = hass
        self.websession = async_get_clientsession(hass)
        self.timeout = timeout
        self._api_key = api_key
        self._server_url = "https://{0}.{1}".format(server_loc, FACE_API_URL)
        self._store = {}
        self._entities = entities

    @property
    def store(self):
        """Store group/person data and IDs."""
        return self._store

    @asyncio.coroutine
    def update_store(self):
        """Load all group/person data into local store."""
        groups = yield from self.call_api('get', 'persongroups')

        tasks = []
        for group in groups:
            g_id = group['personGroupId']
            self._store[g_id] = {}
            self._entities[g_id] = MicrosoftFaceGroupEntity(
                self.hass, self, g_id, group['name'])

            persons = yield from self.call_api(
                'get', "persongroups/{0}/persons".format(g_id))

            for person in persons:
                self._store[g_id][person['name']] = person['personId']

            tasks.append(self._entities[g_id].async_update_ha_state())

        if tasks:
            yield from asyncio.wait(tasks, loop=self.hass.loop)

    @asyncio.coroutine
    def call_api(self, method, function, data=None, binary=False,
                 params=None):
        """Make an api call."""
        headers = {"Ocp-Apim-Subscription-Key": self._api_key}
        url = self._server_url.format(function)

        payload = None
        if binary:
            headers[CONTENT_TYPE] = "application/octet-stream"
            payload = data
        else:
            headers[CONTENT_TYPE] = "application/json"
            if data is not None:
                payload = json.dumps(data).encode()
            else:
                payload = None

        try:
            with async_timeout.timeout(self.timeout, loop=self.hass.loop):
                response = yield from getattr(self.websession, method)(
                    url, data=payload, headers=headers, params=params)

                answer = yield from response.json()

            _LOGGER.debug("Read from microsoft face api: %s", answer)
            if response.status < 300:
                return answer

            _LOGGER.warning("Error %d microsoft face api %s",
                            response.status, response.url)
            raise HomeAssistantError(answer['error']['message'])

        except aiohttp.ClientError:
            _LOGGER.warning("Can't connect to microsoft face api")

        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout from microsoft face api %s", response.url)

        raise HomeAssistantError("Network error on microsoft face api.")
