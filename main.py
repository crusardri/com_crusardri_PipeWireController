import os
import sys
import threading
import pulsectl

from src.backend.PluginManager.PluginBase import PluginBase
from src.backend.PluginManager.ActionHolder import ActionHolder

from .actions.PipeWireAudio import PipeWireAudio
from .actions.PipeWireAudioMixer import PipeWireAudioMixer
from src.backend.DeckManagement.InputIdentifier import Input
from src.backend.PluginManager.ActionInputSupport import ActionInputSupport

class PipeWireController(PluginBase):
    def __init__(self):
        super().__init__()
        self.init_vars()

        self.audio_action_holder = ActionHolder(
            plugin_base=self,
            action_base=PipeWireAudio,
            action_id_suffix="AudioAction",
            action_name=self.lm.get("actions.pipewire-audio.name")
        )
        self.add_action_holder(self.audio_action_holder)

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

    def init_vars(self):
        self.lm = self.locale_manager
        self.lm.set_to_os_default()
        
        # Connect to pulseaudio/pipewire server with a lock for threading safety
        self.pulse = pulsectl.Pulse("stream-controller-pipewire", threading_lock=True)
