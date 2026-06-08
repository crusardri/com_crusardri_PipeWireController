import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, Pango
import cairo

from src.backend.PluginManager.ActionBase import ActionBase

try:
    gi.require_version('Rsvg', '2.0')
    from gi.repository import Rsvg
    HAS_RSVG = True
except Exception:
    HAS_RSVG = False

class PipeWireActionBase(ActionBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
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
        except Exception: pass
        return apps

    def _parse_color(self, hex_str):
        hex_str = hex_str.lstrip('#')
        if len(hex_str) == 6:
            r = int(hex_str[0:2], 16) / 255.0
            g = int(hex_str[2:4], 16) / 255.0
            b = int(hex_str[4:6], 16) / 255.0
            return (r, g, b, 1.0)
        elif len(hex_str) == 8:
            r = int(hex_str[0:2], 16) / 255.0
            g = int(hex_str[2:4], 16) / 255.0
            b = int(hex_str[4:6], 16) / 255.0
            a = int(hex_str[6:8], 16) / 255.0
            return (r, g, b, a)
        return (1, 1, 1, 1)

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
