"""Base class for plugin actions: shared PulseService access and geometry defaults."""
import logging

from src.backend.PluginManager.ActionBase import ActionBase

log = logging.getLogger(__name__)


class PipeWireActionBase(ActionBase):

    @property
    def pulse_service(self):
        """Return the shared PulseService from the plugin, or None if unavailable."""
        return getattr(self.plugin_base, "pulse_service", None)

    def get_settings(self):
        """Return the action settings as a plain dict.

        StreamController's ActionBase.get_settings() sometimes returns a tuple
        instead of a dict (version-dependent).  This wrapper normalises the
        return value so all subclasses can rely on dict semantics.
        """
        settings = super().get_settings()
        if isinstance(settings, tuple):
            settings = settings[0] if len(settings) > 0 else {}
        if not isinstance(settings, dict):
            settings = {}
        return settings

    def get_active_applications(self):
        """Return the names of applications with active audio streams.

        Delegates to PulseService; returns an empty list when the service
        is unavailable (e.g. during plugin startup).
        """
        svc = self.pulse_service
        return svc.get_active_applications() if svc else []

    def get_calculated_defaults(self, ctrl_input):
        """Compute geometry defaults (bar, text, icons) for the given input size.

        Accounts for the horizontal crop applied to touchscreen inputs on
        devices that report a wider pixel buffer than the visible area
        (e.g. Mirabox N4: 480 px buffer, 176 px visible per key).

        Returns:
            (defaults_dict, full_width, full_height)
            where defaults_dict contains bar/text/icon position keys.
        """
        width, height = 100, 100
        visible_width = 100
        crop_margin_x = 0
        try:
            if ctrl_input:
                width, height = ctrl_input.get_image_size()
                visible_width = width
                if hasattr(ctrl_input.deck_controller, "deck"):
                    deck = ctrl_input.deck_controller.deck
                    if hasattr(deck, "TOUCHBAR_KEY_PIXEL_WIDTH"):
                        # Device reports its own visible width constant.
                        visible_width = deck.TOUCHBAR_KEY_PIXEL_WIDTH
                        crop_margin_x = (width - visible_width) // 2
                    elif deck.deck_type() == "Mirabox StreamDeck N4":
                        # Hard-coded crop for this specific device model.
                        visible_width = 176
                        crop_margin_x = (width - visible_width) // 2
        except Exception as e:
            log.debug("Error getting default size: %s", e)

        # Ensure minimum dimensions so arithmetic below doesn't divide by zero.
        width = max(32, width)
        visible_width = max(32, visible_width)
        height = max(32, height)

        defaults = {}
        bar_h = 8
        # A 3% margin keeps content away from the screen edge.
        margin = int(round(visible_width * 0.03))
        max_w = visible_width - (margin * 2)

        # Bar geometry
        defaults["bar_height"] = bar_h
        defaults["bar_x"] = crop_margin_x + margin
        defaults["bar_y"] = height - bar_h - int(round(height * 0.03))
        defaults["bar_width"] = max_w
        defaults["bar_radius"] = 5
        defaults["bar_out_width"] = 1
        defaults["bar_out_color"] = "#000000"

        # Name text (top of the dial face)
        defaults["width_name"] = max_w
        defaults["pos_x_name"] = crop_margin_x + margin
        defaults["pos_y_name"] = 3
        defaults["align_name"] = "center"

        # Percentage text (just above the bar)
        defaults["width_pct"] = defaults["bar_width"]
        defaults["pos_x_pct"] = defaults["bar_x"]
        defaults["pos_y_pct"] = defaults["bar_y"] - 5
        defaults["align_pct"] = "right"

        # Icon A (left, above the bar)
        icon_size = 48
        defaults["icon_height_a"] = icon_size
        defaults["icon_x_a"] = crop_margin_x + margin
        defaults["icon_y_a"] = defaults["bar_y"] - icon_size - 4
        defaults["icon_out_width_a"] = 1
        defaults["icon_out_color_a"] = "#000000"

        # Icon B (immediately to the right of icon A)
        defaults["icon_height_b"] = icon_size
        defaults["icon_x_b"] = defaults["icon_x_a"] + icon_size + 5
        defaults["icon_y_b"] = defaults["icon_y_a"]
        defaults["icon_out_width_b"] = 1
        defaults["icon_out_color_b"] = "#000000"

        return defaults, width, height
