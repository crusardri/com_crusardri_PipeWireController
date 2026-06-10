import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, Pango
import cairo

from src.backend.PluginManager.ActionBase import ActionBase
import logging

try:
    gi.require_version('Rsvg', '2.0')
    from gi.repository import Rsvg
    HAS_RSVG = True
except Exception:
    HAS_RSVG = False

class PipeWireActionBase(ActionBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.icon_cache = {}
        self.MAX_ICON_CACHE_SIZE = 50
        
    def safe_pulse_call(self, fn, *args, **kwargs):
        pulse = self.get_pulse()
        if not pulse:
            return None
        try:
            with self.plugin_base.pulse_lock:
                return fn(*args, **kwargs)
        except Exception as e:
            logging.getLogger(__name__).warning("PulseAudio call failed: %s", e)
            return None

    def get_volume(self, device):
        if not device: return 0
        try:
            with self.plugin_base.pulse_lock:
                vol = self.get_pulse().volume_get_all_chans(device)
                is_mute = getattr(device, 'mute', False)
                if is_mute: return 0
                return int(round(vol * 100))
        except Exception as e:
            logging.getLogger(__name__).warning("Error getting volume: %s", e)
            return 0
        
    def get_pulse(self):
        if not hasattr(self.plugin_base, "pulse"):
            return None
        return self.plugin_base.pulse

    def get_active_applications(self):
        pulse = self.get_pulse()
        if not pulse: return []
        apps = []
        try:
            with self.plugin_base.pulse_lock:
                for sink_input in pulse.sink_input_list():
                    if hasattr(sink_input, 'proplist'):
                        app_name = sink_input.proplist.get('application.process.binary') or sink_input.proplist.get('application.name')
                        if app_name and app_name not in apps:
                            apps.append(app_name)
                for source_output in pulse.source_output_list():
                    if hasattr(source_output, 'proplist'):
                        app_name = source_output.proplist.get('application.process.binary') or source_output.proplist.get('application.name')
                        if app_name and app_name not in apps:
                            apps.append(app_name)
        except Exception as e:
            logging.getLogger(__name__).warning("Error getting active applications: %s", e)
        return apps

    def _parse_color(self, hex_str):
        hex_str = hex_str.lstrip('#')
        if len(hex_str) == 6:
            r = int(hex_str[0:2], 16) / 255.0
            g = int(hex_str[2:4], 16) / 255.0
            b = int(hex_str[4:6], 16) / 255.0
            return (r, g, b, 1.0)
        if len(hex_str) == 8:
            r = int(hex_str[0:2], 16) / 255.0
            g = int(hex_str[2:4], 16) / 255.0
            b = int(hex_str[4:6], 16) / 255.0
            a = int(hex_str[6:8], 16) / 255.0
            return (r, g, b, a)
        return (1, 1, 1, 1)

    @staticmethod
    def rgba_to_hex(rgba):
        return f"#{int(rgba.red*255):02x}{int(rgba.green*255):02x}{int(rgba.blue*255):02x}"

    def draw_rounded_rect(self, cr, x, y, w, h, r):
        self.draw_rounded_rect_custom(cr, x, y, w, h, r, r, r, r)

    def draw_rounded_rect_custom(self, cr, x, y, w, h, tl, tr, br, bl):
        cr.new_sub_path()
        if tr > 0: cr.arc(x + w - tr, y + tr, tr, -1.570796, 0)
        else: cr.line_to(x + w, y)
        
        if br > 0: cr.arc(x + w - br, y + h - br, br, 0, 1.570796)
        else: cr.line_to(x + w, y + h)
        
        if bl > 0: cr.arc(x + bl, y + h - bl, bl, 1.570796, 3.141593)
        else: cr.line_to(x, y + h)
        
        if tl > 0: cr.arc(x + tl, y + tl, tl, 3.141593, -1.570796)
        else: cr.line_to(x, y)
        cr.close_path()

    def render_svg_to_cairo(self, ctx, icon_path, x, y, target_w, target_h):
        if not HAS_RSVG:
            return False
        try:
            handle = Rsvg.Handle.new_from_file(icon_path)
            dim = handle.get_dimensions()
            svg_w, svg_h = dim.width, dim.height
            
            if target_w < 0: target_w = int(svg_w * (target_h / float(svg_h))) if svg_h > 0 else 48
            if target_h < 0: target_h = int(svg_h * (target_w / float(svg_w))) if svg_w > 0 else 48
            
            ctx.save()
            ctx.translate(x, y)
            if svg_w > 0 and svg_h > 0:
                ctx.scale(target_w / svg_w, target_h / svg_h)
            handle.render_cairo(ctx)
            ctx.restore()
            return True
        except Exception as e:
            return False

    def get_calculated_defaults(self, ctrl_input, is_single_mode=False):
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
                        visible_width = deck.TOUCHBAR_KEY_PIXEL_WIDTH
                        crop_margin_x = (width - visible_width) // 2
                    elif deck.deck_type() == "Mirabox StreamDeck N4":
                        visible_width = 176
                        crop_margin_x = (width - visible_width) // 2
        except Exception as e:
            logging.getLogger(__name__).debug("Error getting default size: %s", e)
            
        width = max(32, width)
        visible_width = max(32, visible_width)
        height = max(32, height)

        defaults = {}
        bar_h = 8
        defaults["bar_height"] = bar_h
        margin = int(round(visible_width * 0.03))
        
        defaults["bar_x"] = crop_margin_x + margin
        defaults["bar_y"] = height - bar_h - int(round(height * 0.03))
        defaults["bar_width"] = visible_width - (margin * 2)
        defaults["bar_radius"] = 5
        defaults["bar_out_width"] = 1
        defaults["bar_out_color"] = "#000000"

        max_w = visible_width - (margin * 2)

        defaults["width_name"] = max_w
        defaults["pos_x_name"] = crop_margin_x + margin
        defaults["pos_y_name"] = 3
        defaults["align_name"] = "center"

        icon_size = 48
        defaults["icon_height_a"] = icon_size
        defaults["icon_x_a"] = crop_margin_x + margin
        defaults["icon_y_a"] = defaults["bar_y"] - icon_size - 4

        defaults["width_pct"] = defaults["bar_width"]
        defaults["pos_x_pct"] = defaults["bar_x"]
        defaults["pos_y_pct"] = defaults["bar_y"] - 5
        defaults["align_pct"] = "right"
        defaults["icon_out_width_a"] = 1
        defaults["icon_out_color_a"] = "#000000"

        defaults["icon_height_b"] = icon_size
        defaults["icon_x_b"] = defaults["icon_x_a"] + icon_size + 5
        defaults["icon_y_b"] = defaults["icon_y_a"]
        defaults["icon_out_width_b"] = 1
        defaults["icon_out_color_b"] = "#000000"

        return defaults, width, height
