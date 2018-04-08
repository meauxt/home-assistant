"""Device tracker for BMW Connected Drive vehicles.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/device_tracker.bmw_connected_drive/
"""
import logging

from homeassistant.components.bmw_connected_drive import DOMAIN \
    as BMW_DOMAIN
from homeassistant.util import slugify

DEPENDENCIES = ['bmw_connected_drive']

_LOGGER = logging.getLogger(__name__)


def setup_scanner(hass, config, see, discovery_info=None):
    """Set up the BMW tracker."""
    accounts = hass.data[BMW_DOMAIN]
    _LOGGER.debug('Found BMW accounts: %s',
                  ', '.join([a.name for a in accounts]))
    for account in accounts:
        for vehicle in account.account.vehicles:
            tracker = BMWDeviceTracker(see, vehicle)
            account.add_update_listener(tracker.update)
            tracker.update()
    return True


class BMWDeviceTracker(object):
    """BMW Connected Drive device tracker."""

    def __init__(self, see, vehicle):
        """Initialize the Tracker."""
        self._see = see
        self.vehicle = vehicle

    def update(self) -> None:
        """Update the device info.

        Only update the state in home assistant if tracking in
        the car is enabled.
        """
        dev_id = slugify(self.vehicle.name)

        if not self.vehicle.state.is_vehicle_tracking_enabled:
            _LOGGER.debug('Tracking is disabled for vehicle %s', dev_id)
            return

        _LOGGER.debug('Updating %s', dev_id)

        self._see(
            dev_id=dev_id, host_name=self.vehicle.name,
            gps=self.vehicle.state.gps_position, icon='mdi:car'
        )
