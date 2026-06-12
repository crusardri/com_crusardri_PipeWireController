"""Plugin entry point: registers action holders and the PulseAudio service.

StreamController loads this module and calls PipeWireController.__init__
once per plugin lifecycle.  All actions share the single PulseService
instance created here.
"""
from src.backend.PluginManager.PluginBase import PluginBase
from src.backend.PluginManager.ActionHolder import ActionHolder
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.ActionInputSupport import ActionInputSupport

from .actions.core.pulse_service import PulseService
from .actions.PipeWireAudioMixer import PipeWireAudioMixer
from .actions.SetDefaultDevice import SetDefaultDevice


class PipeWireController(PluginBase):
    def __init__(self):
        super().__init__()

        # Locale manager shortcut; set to the OS language at startup.
        self.lm = self.locale_manager
        self.lm.set_to_os_default()

        # Single shared connection to PipeWire/PulseAudio (MODEL layer).
        # All actions reach this via self.plugin_base.pulse_service.
        self.pulse_service = PulseService()

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

        # Announce the plugin to StreamController (name, repo, versions).
        self.register(
            plugin_name=self.lm.get("plugin.name"),
            github_repo="https://github.com/crusard/PipeWireController",
            plugin_version="1.0.0",
            app_version="1.4.11-beta"
        )
