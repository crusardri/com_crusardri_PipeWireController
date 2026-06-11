from src.backend.PluginManager.PluginBase import PluginBase
from src.backend.PluginManager.ActionHolder import ActionHolder
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.ActionInputSupport import ActionInputSupport

from .actions.core.pulse_service import PulseService
from .actions.PipeWireAudioMixer import PipeWireAudioMixer


class PipeWireController(PluginBase):
    def __init__(self):
        super().__init__()
        self.lm = self.locale_manager
        self.lm.set_to_os_default()

        self.pulse_service = PulseService()

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

        self.register(
            plugin_name=self.lm.get("plugin.name"),
            github_repo="https://github.com/crusard/PipeWireController",
            plugin_version="1.0.0",
            app_version="1.4.11-beta"
        )
