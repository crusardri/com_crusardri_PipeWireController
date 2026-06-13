"""Plugin entry point: registers action holders and the PulseAudio service.

StreamController loads this module and calls PipeWireController.__init__
once per plugin lifecycle.  All actions share the single PulseService
instance created here.
"""
import logging

from src.backend.PluginManager.PluginBase import PluginBase
from src.backend.PluginManager.ActionHolder import ActionHolder
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.ActionInputSupport import ActionInputSupport

log = logging.getLogger(__name__)

from .actions.core.pulse_service import PulseService
from .actions.PipeWireAudioMixer import PipeWireAudioMixer
from .actions.SetDefaultDevice import SetDefaultDevice
from .actions.DeviceControl import DeviceControl


class PipeWireController(PluginBase):
    def __init__(self):
        super().__init__()

        # Locale manager shortcut; set to the OS language at startup.
        self.lm = self.locale_manager
        self.lm.set_to_os_default()

        # Single shared connection to PipeWire/PulseAudio (MODEL layer).
        # All actions reach this via self.plugin_base.pulse_service.
        self.pulse_service = PulseService()

        # Live DeviceControl instances, used to sync interactions across every
        # key bound to the same device (see broadcast_device_interaction).
        self.device_control_instances = []

        # Register the Audio Mixer action so StreamController can assign
        # it to dial inputs.  Touchscreen and key inputs are unsupported.
        self.mixer_action_holder = ActionHolder(
            plugin_base=self,
            action_base=PipeWireAudioMixer,
            action_id_suffix="AudioMixerAction",
            action_name=self.lm.get("actions.pipewire-mixer.name", "Audio Mixer"),
            action_support={
                Input.Key: ActionInputSupport.UNSUPPORTED,
                Input.Dial: ActionInputSupport.SUPPORTED,
                Input.Touchscreen: ActionInputSupport.UNSUPPORTED
            }
        )
        self.add_action_holder(self.mixer_action_holder)

        # Register the Set Default Device action (keys + dials).
        self.set_default_action_holder = ActionHolder(
            plugin_base=self,
            action_base=SetDefaultDevice,
            action_id_suffix="SetDefaultDeviceAction",
            action_name=self.lm.get("actions.set-default.name", "Set Default Device"),
            action_support={
                Input.Key: ActionInputSupport.SUPPORTED,
                Input.Dial: ActionInputSupport.SUPPORTED,
                Input.Touchscreen: ActionInputSupport.UNSUPPORTED
            }
        )
        self.add_action_holder(self.set_default_action_holder)

        # Register the Device Control action (LCD keys only).
        self.device_control_holder = ActionHolder(
            plugin_base=self,
            action_base=DeviceControl,
            action_id_suffix="DeviceControlAction",
            action_name=self.lm.get("actions.device-control.name", "Device Control"),
            action_support={
                Input.Key: ActionInputSupport.SUPPORTED,
                Input.Dial: ActionInputSupport.UNSUPPORTED,
                Input.Touchscreen: ActionInputSupport.UNSUPPORTED
            }
        )
        self.add_action_holder(self.device_control_holder)

        # Announce the plugin to StreamController (name, repo, versions).
        self.register(
            plugin_name=self.lm.get("plugin.name"),
            github_repo="https://github.com/crusard/PipeWireController",
            plugin_version="1.0.0",
            app_version="1.4.11-beta"
        )

    # ------------------------------------------------------------------
    # Cross-action interaction sync (DeviceControl)
    # ------------------------------------------------------------------

    def register_device_control(self, action):
        """Track a live DeviceControl instance for interaction broadcasts."""
        if action not in self.device_control_instances:
            self.device_control_instances.append(action)

    def unregister_device_control(self, action):
        """Drop a DeviceControl instance when it is removed."""
        try:
            self.device_control_instances.remove(action)
        except ValueError:
            pass

    def broadcast_device_interaction(self, device_name, source):
        """Tell every DeviceControl bound to `device_name` that the device was
        interacted with, so they all wake to the volume display together.

        This covers the case where the volume value did not actually change
        (e.g. already at min/max): a plain value-change check would miss it,
        so the originating key explicitly notifies its siblings.
        """
        if not device_name:
            return
        for action in list(self.device_control_instances):
            if action is source:
                continue
            try:
                action.on_external_interaction(device_name)
            except Exception as e:
                log.debug("device interaction broadcast failed: %s", e)
